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
        "-ng",            # No GPU. Apple's launchd user-domain agents cannot
                          # reliably initialize Metal — process spins waiting
                          # for shader compilation. base.en CPU is fast enough
                          # for our use case (~10-15s for a 30s clip on M4).
        "-t", "8",        # CPU thread count; ghengis has 4P+6E cores.
        str(audio_path),
    ]
    logger.debug("whisper-cli: {}", " ".join(cmd))
    # Route whisper-cli's progress output to a logfile next to the audio so we
    # can debug post-mortem. Closing stdin is critical when running under
    # launchd — without it whisper-cli's TTY-detection has been observed to
    # hang at 0% CPU before ever reaching model load.
    log_path = out_prefix.with_suffix(".whisper.log")
    with open(log_path, "wb") as log_fp:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    if result.returncode != 0:
        try:
            tail = log_path.read_text(errors="replace")[-400:]
        except OSError:
            tail = "(log unreadable)"
        raise RuntimeError(f"whisper-cli failed (rc={result.returncode}): {tail}")
    if txt_out.exists():
        return txt_out.read_text().strip()
    # Fallback: parse the stdout transcript if -otxt didn't write a file.
    return result.stdout.strip()
