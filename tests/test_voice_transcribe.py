import pytest
from pathlib import Path
from hermes.voice.transcribe import transcribe_voice


def test_transcribe_voice_not_implemented_yet(tmp_path: Path):
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake")
    with pytest.raises(NotImplementedError, match="v1.2"):
        transcribe_voice(audio)
