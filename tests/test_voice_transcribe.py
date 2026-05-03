from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from hermes.voice.transcribe import transcribe_voice


def test_transcribe_reads_txt_output(tmp_path: Path):
    """Happy path: whisper-cli writes <prefix>.txt; we return its contents."""
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFFfake")
    model = tmp_path / "ggml-base.en.bin"
    model.write_bytes(b"model")

    def fake_run(cmd, **kwargs):
        # Find the -of <prefix> arg and write a .txt file there.
        idx = cmd.index("-of")
        prefix = Path(cmd[idx + 1])
        prefix.with_suffix(".txt").write_text("hello transcribed world\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("hermes.voice.transcribe.subprocess.run", side_effect=fake_run):
        text = transcribe_voice(audio, model_path=model)
    assert text == "hello transcribed world"


def test_transcribe_falls_back_to_stdout(tmp_path: Path):
    """If whisper-cli doesn't write a .txt (older builds), use stdout."""
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFFfake")
    model = tmp_path / "m.bin"
    model.write_bytes(b"model")
    with patch("hermes.voice.transcribe.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="from stdout\n", stderr="")):
        text = transcribe_voice(audio, model_path=model)
    assert text == "from stdout"


def test_transcribe_raises_on_nonzero_exit(tmp_path: Path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"x")
    model = tmp_path / "m.bin"
    model.write_bytes(b"m")
    with patch("hermes.voice.transcribe.subprocess.run",
               return_value=MagicMock(returncode=2, stdout="", stderr="bad audio")):
        with pytest.raises(RuntimeError, match="whisper-cli failed"):
            transcribe_voice(audio, model_path=model)


def test_transcribe_missing_audio_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="audio file not found"):
        transcribe_voice(tmp_path / "missing.wav", model_path=tmp_path / "m.bin")


def test_transcribe_missing_model_raises(tmp_path: Path):
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"x")
    with pytest.raises(FileNotFoundError, match="whisper model not found"):
        transcribe_voice(audio, model_path=tmp_path / "missing.bin")
