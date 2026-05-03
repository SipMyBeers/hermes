from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from hermes.ingest.video import extract_video


def test_extract_video_happy_path(tmp_path: Path):
    """yt-dlp produces a wav, transcribe_voice consumes it, return ExtractedDoc."""
    def fake_yt_dlp(cmd, **kwargs):
        # Find the -o template and write a fake wav at its location.
        idx = cmd.index("-o")
        # Template is "<dir>/%(id)s.%(ext)s" — extract the dir and write a wav there.
        out_tpl = Path(cmd[idx + 1])
        out_dir = out_tpl.parent
        (out_dir / "abc.wav").write_bytes(b"RIFFfake")
        meta = '{"title": "Sample Reel", "id": "abc"}'
        return MagicMock(returncode=0, stdout=meta + "\n", stderr="")

    with patch("hermes.ingest.video.subprocess.run", side_effect=fake_yt_dlp), \
         patch("hermes.ingest.video.transcribe_voice", return_value="this is the transcript"):
        doc = extract_video("https://www.instagram.com/reel/abc/")

    assert doc.url == "https://www.instagram.com/reel/abc/"
    assert doc.title == "Sample Reel"
    assert doc.text == "this is the transcript"


def test_extract_video_yt_dlp_failure_raises():
    with patch("hermes.ingest.video.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="",
                                      stderr="ERROR: Private video")):
        with pytest.raises(RuntimeError, match="yt-dlp failed"):
            extract_video("https://www.instagram.com/reel/private/")


def test_extract_video_no_audio_produced_raises(tmp_path: Path):
    """yt-dlp succeeded but didn't write a wav — surface a clear error."""
    def fake_yt_dlp(cmd, **kwargs):
        return MagicMock(returncode=0, stdout='{"title": "x"}', stderr="")

    with patch("hermes.ingest.video.subprocess.run", side_effect=fake_yt_dlp):
        with pytest.raises(RuntimeError, match="no .wav file"):
            extract_video("https://example.com/video")
