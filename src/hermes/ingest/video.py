"""Video URL extraction — yt-dlp downloads audio, whisper-cli transcribes.

Supports Instagram reels, YouTube (regular + Shorts), TikTok, X status URLs,
and anything else yt-dlp recognizes. Authentication-required content (private
IG profiles, members-only YT) will fail at the yt-dlp step and surface as
RuntimeError; the orchestrator records that to the digestions row's `error`
column so the bot reply is informative.
"""
from __future__ import annotations
import json
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
) -> ExtractedDoc:
    """Download audio via yt-dlp, transcribe via whisper-cli.

    Returns ExtractedDoc(title=video_title, text=transcript, url=url).
    Raises RuntimeError if yt-dlp can't fetch (auth, geoblock, deletion).

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
    import shutil
    work_root = Path.home() / ".hermes" / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="hermes-video-", dir=work_root))
    keep_workdir = False
    try:
        out_tpl = str(tmp_path / "%(id)s.%(ext)s")
        base_cmd = [
            "yt-dlp",
            "-x", "--audio-format", "wav",   # extract audio as WAV for whisper.cpp
            "--no-playlist",
            "--no-warnings",
            "--print-json",                  # one-line JSON metadata at end
            "-o", out_tpl,
        ]

        # Build the list of attempts. Each tuple is (auth_args, label).
        attempts: list[tuple[list[str], str]] = []
        if cookies_path:
            attempts.append((["--cookies", cookies_path], f"cookies file {cookies_path}"))
        for browser in cookies_from_browser:
            attempts.append((["--cookies-from-browser", browser], f"browser {browser}"))
        if not attempts:
            attempts.append(([], "no auth"))

        last_err = ""
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
        for line in result.stdout.strip().splitlines():
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue
        title = str(info.get("title") or info.get("fulltitle") or "")
        wavs = list(tmp_path.glob("*.wav"))
        if not wavs:
            keep_workdir = True
            raise RuntimeError(f"yt-dlp produced no .wav file in {tmp_path}")
        try:
            transcript = transcribe_voice(wavs[0])
        except Exception:
            keep_workdir = True
            raise
        return ExtractedDoc(title=title, text=transcript, url=url)
    finally:
        if not keep_workdir:
            shutil.rmtree(tmp_path, ignore_errors=True)
        else:
            logger.warning("kept work dir for post-mortem: {}", tmp_path)
