"""Whisper.cpp-backed audio transcription.

v1.2 install path:
    brew install whisper-cpp
    download a model: https://huggingface.co/ggerganov/whisper.cpp
        (e.g. ggml-base.en.bin into ~/.hermes/whisper/)

v1.2 implementation: subprocess-call whisper-cli with the audio path,
parse stdout, return text. The voice file arrives via Telegram bot's
`getFile` API (already wired in TelegramClient).
"""
from __future__ import annotations
from pathlib import Path


def transcribe_voice(
    audio_path: Path,
    model_path: Path | None = None,
    language: str = "en",
) -> str:
    raise NotImplementedError(
        "v1.2: whisper-cpp subprocess (see module docstring for install)"
    )
