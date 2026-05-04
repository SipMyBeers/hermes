"""Video URL extraction — yt-dlp pulls the video file, ffmpeg splits audio for
whisper-cli, and a parallel keyframe sweep + Tesseract OCR feeds the
multimodal summarizer along with the spoken transcript.

Supports Instagram reels, YouTube (regular + Shorts), TikTok, X status URLs,
and anything else yt-dlp recognizes. Authentication-required content (private
IG profiles, members-only YT) will fail at the yt-dlp step and surface as
RuntimeError; the orchestrator records that to the digestions row's `error`
column so the bot reply is informative.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from loguru import logger
from hermes.ingest.web import ExtractedDoc
from hermes.voice.transcribe import transcribe_voice


def extract_video(
    url: str,
    timeout: int = 900,
    cookies_path: str = "",
    cookies_from_browser: tuple[str, ...] = (),
    keyframes: int = 4,
) -> ExtractedDoc:
    """Download video via yt-dlp, transcribe its audio, and sample keyframes.

    Returns ExtractedDoc with title, transcript text, keyframes (JPEG paths),
    and OCR snippets (per-keyframe text). The caller is expected to pass the
    keyframes + OCR forward to `summarize()` for multimodal analysis.

    Auth (Instagram, members-only YT, etc.):
    - cookies_path: path to a Netscape cookies.txt file → yt-dlp gets --cookies <path>.
    - cookies_from_browser: tuple of browser names tried IN ORDER. First success wins.
      Examples: ('firefox',), ('safari',), ('firefox','chrome').
    Both empty → no auth (works for public YT, TikTok, Twitter).
    """
    # Persist the work dir under ~/.hermes/work/ so whisper logs survive a
    # failure (the previous tempfile.TemporaryDirectory wiped the .whisper.log
    # before we could read it). Successful runs are cleaned up below; failed
    # runs leave the dir behind for post-mortem.
    work_root = Path.home() / ".hermes" / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="hermes-video-", dir=work_root))
    keep_workdir = False
    try:
        # 1) yt-dlp: download the video file (audio + video). We get an mp4
        # so ffmpeg can both split audio out as WAV and sample keyframes.
        out_tpl = str(tmp_path / "%(id)s.%(ext)s")
        base_cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--print-json",
            "-f", "best[ext=mp4]/best",   # prefer mp4 with audio for ffmpeg
            "-o", out_tpl,
        ]

        attempts: list[tuple[list[str], str]] = []
        if cookies_path:
            attempts.append((["--cookies", cookies_path], f"cookies file {cookies_path}"))
        for browser in cookies_from_browser:
            attempts.append((["--cookies-from-browser", browser], f"browser {browser}"))
        if not attempts:
            attempts.append(([], "no auth"))

        last_err = ""
        result = None
        for auth_args, label in attempts:
            cmd = base_cmd + auth_args + [url]
            logger.debug("yt-dlp ({}): {}", label, " ".join(cmd))
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                break
            last_err = result.stderr.strip()[:500]
            logger.debug("yt-dlp attempt with {} failed: {}", label, last_err)
        else:
            raise RuntimeError(
                f"yt-dlp failed after {len(attempts)} attempt(s): {last_err}"
            )

        # Parse the last valid JSON line for metadata (yt-dlp prints multiple lines).
        info: dict = {}
        for line in (result.stdout or "").strip().splitlines():
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue
        title = str(info.get("title") or info.get("fulltitle") or "")

        videos = (
            list(tmp_path.glob("*.mp4"))
            + list(tmp_path.glob("*.mkv"))
            + list(tmp_path.glob("*.webm"))
        )
        if not videos:
            keep_workdir = True
            raise RuntimeError(f"yt-dlp produced no video file in {tmp_path}")
        video_path = videos[0]

        # 2) ffmpeg: split audio out as WAV for whisper.
        wav_path = tmp_path / (video_path.stem + ".wav")
        ff_audio = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", str(video_path), "-ac", "1", "-ar", "16000",
             "-vn", str(wav_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        if ff_audio.returncode != 0 or not wav_path.exists():
            keep_workdir = True
            err = (ff_audio.stderr or b"").decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"ffmpeg audio extract failed: {err}")

        # 3) whisper-cli on the WAV.
        try:
            transcript = transcribe_voice(wav_path)
        except Exception:
            keep_workdir = True
            raise

        # 4) Keyframes + OCR. Imported lazily so callers that mock
        # extract_video don't need these modules around.
        from hermes.ingest.frames import extract_keyframes
        from hermes.ingest.ocr import ocr_images
        try:
            frames = extract_keyframes(video_path, out_dir=tmp_path, n=keyframes)
        except Exception as e:
            # Don't fail the whole pipeline if keyframes can't be sampled —
            # fall back to a transcript-only summary.
            logger.warning("keyframe extraction failed for {}: {}", url, e)
            frames = []
        ocr_texts = ocr_images(frames) if frames else []

        # Keep the workdir alive while we still hold keyframe paths — the
        # caller (url.py) base64-encodes them inside summarize() before
        # invoking `cleanup_workdir(doc)` to drop the temp tree. When there
        # are no frames, the doc carries no workdir and `finally` removes it.
        if frames:
            keep_workdir = True
            return ExtractedDoc(
                title=title,
                text=transcript,
                url=url,
                keyframes=tuple(frames),
                ocr_snippets=tuple(ocr_texts),
                workdir=tmp_path,
            )

        return ExtractedDoc(
            title=title,
            text=transcript,
            url=url,
            keyframes=(),
            ocr_snippets=(),
        )
    finally:
        if not keep_workdir:
            shutil.rmtree(tmp_path, ignore_errors=True)
        else:
            logger.debug("kept work dir (post-mortem or vision payload): {}", tmp_path)


def cleanup_workdir(doc: ExtractedDoc) -> None:
    """Remove the temp work dir on a video doc once the summarizer has
    finished reading its keyframes. Safe to call on docs without a workdir
    (no-op)."""
    if doc.workdir is None:
        return
    shutil.rmtree(doc.workdir, ignore_errors=True)
