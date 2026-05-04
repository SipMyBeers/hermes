"""whisper-cli (whisper.cpp) backed audio transcription.

Audio formats whisper.cpp accepts: wav (preferred), mp3, ogg, flac.
Caller is responsible for any prior format conversion (e.g. ffmpeg).

Default model location matches the existing ggml-base.en.bin on ghengis.
Override via model_path or set WHISPER_MODEL env var.
"""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
from loguru import logger

# Pre-installed model paths on ghengis (saved by aurajam project earlier).
_MODEL_CANDIDATES = [
    Path.home() / "Documents" / "GitHub" / "aurajam" / "models" / "ggml-base.en.bin",
    Path.home() / ".hermes" / "whisper" / "ggml-base.en.bin",
    Path.home() / "Documents" / "GitHub" / "aurajam" / "models" / "ggml-tiny.en.bin",
]


def _resolve_model(explicit: Path | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("WHISPER_MODEL")
    if env:
        return Path(env)
    for cand in _MODEL_CANDIDATES:
        if cand.exists():
            return cand
    # Last-resort: the first candidate (won't exist; will raise below with a clear path)
    return _MODEL_CANDIDATES[0]


def transcribe_voice(
    audio_path: Path,
    model_path: Path | None = None,
    language: str = "en",
    timeout: int = 600,
) -> str:
    """Run whisper-cli over the audio file, return plain transcript text."""
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file not found: {audio_path}")
    model = _resolve_model(model_path)
    if not model.exists():
        raise FileNotFoundError(f"whisper model not found: {model}")

    # whisper-cli writes <prefix>.txt when called with -otxt -of <prefix>.
    out_prefix = audio_path.with_suffix("")
    txt_out = out_prefix.with_suffix(".txt")
    cmd = [
        "whisper-cli",
        "-m", str(model),
        "-l", language,
        "-otxt",
        "-of", str(out_prefix),
        str(audio_path),
    ]
    logger.debug("whisper-cli: {}", " ".join(cmd))
    # whisper-cli streams a lot of progress output to stderr. Python's default
    # subprocess pipe buffer is 64KB — fill that and the child deadlocks
    # waiting for the parent to drain. We don't need stderr (we read the .txt
    # output file separately), so redirect both streams to DEVNULL. If you ever
    # need to debug, drop the redirect and watch with `log stream`.
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"whisper-cli failed (rc={result.returncode})")
    if txt_out.exists():
        return txt_out.read_text().strip()
    # Fallback: parse the stdout transcript if -otxt didn't write a file.
    return result.stdout.strip()
