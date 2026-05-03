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


def extract_video(url: str, timeout: int = 900) -> ExtractedDoc:
    """Download audio via yt-dlp, transcribe via whisper-cli.

    Returns ExtractedDoc(title=video_title, text=transcript, url=url).
    Raises RuntimeError if yt-dlp can't fetch (auth, geoblock, deletion).
    """
    with tempfile.TemporaryDirectory(prefix="hermes-video-") as tmp:
        tmp_path = Path(tmp)
        out_tpl = str(tmp_path / "%(id)s.%(ext)s")
        cmd = [
            "yt-dlp",
            "-x", "--audio-format", "wav",   # extract audio as WAV for whisper.cpp
            "--no-playlist",
            "--no-warnings",
            "--print-json",                  # one-line JSON metadata at end
            "-o", out_tpl,
            url,
        ]
        logger.debug("yt-dlp: {}", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(
                f"yt-dlp failed (rc={result.returncode}): {result.stderr.strip()[:500]}"
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
            raise RuntimeError(f"yt-dlp produced no .wav file in {tmp_path}")
        transcript = transcribe_voice(wavs[0])
        return ExtractedDoc(title=title, text=transcript, url=url)
