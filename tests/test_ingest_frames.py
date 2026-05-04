from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from hermes.ingest.frames import extract_keyframes


def _fake_video(tmp_path: Path) -> Path:
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"fake-video")
    return v


def _make_runner(duration: str, ffmpeg_rc: int = 0):
    """Build a fake subprocess.run that responds to ffprobe + ffmpeg.

    duration: stdout for the ffprobe call (e.g. "30.0", "0.0").
    ffmpeg_rc: returncode for ffmpeg invocations. When 0, the test will
    write the expected output JPEG so the function sees it on disk.
    """
    def runner(cmd, **kwargs):
        prog = cmd[0]
        if prog == "ffprobe":
            return MagicMock(returncode=0, stdout=duration, stderr="")
        # ffmpeg path: write a fake jpeg if we're "succeeding".
        if ffmpeg_rc == 0:
            # The output path is the last arg.
            out = Path(cmd[-1])
            out.write_bytes(b"\xff\xd8\xff\xe0fakejpg")
        return MagicMock(returncode=ffmpeg_rc, stderr=b"some error" if ffmpeg_rc else b"")
    return runner


def test_extract_keyframes_happy_path(tmp_path: Path):
    video = _fake_video(tmp_path)
    out_dir = tmp_path / "frames"

    with patch("hermes.ingest.frames.subprocess.run", side_effect=_make_runner("30.0")):
        frames = extract_keyframes(video, out_dir, n=4)

    assert len(frames) == 4
    for i, p in enumerate(frames):
        assert p.name == f"frame_{i:02d}.jpg"
        assert p.exists()


def test_extract_keyframes_zero_duration_falls_back_to_single_frame(tmp_path: Path):
    video = _fake_video(tmp_path)
    out_dir = tmp_path / "frames"

    with patch("hermes.ingest.frames.subprocess.run", side_effect=_make_runner("0.0")):
        frames = extract_keyframes(video, out_dir, n=4)

    # Fallback path: only frame_00.jpg is produced (single ffmpeg call).
    assert len(frames) == 1
    assert frames[0].name == "frame_00.jpg"


def test_extract_keyframes_ffmpeg_fails_returns_empty(tmp_path: Path):
    video = _fake_video(tmp_path)
    out_dir = tmp_path / "frames"

    with patch("hermes.ingest.frames.subprocess.run",
               side_effect=_make_runner("30.0", ffmpeg_rc=1)):
        frames = extract_keyframes(video, out_dir, n=4)

    assert frames == []


def test_extract_keyframes_ffprobe_failure_raises(tmp_path: Path):
    video = _fake_video(tmp_path)
    out_dir = tmp_path / "frames"

    def runner(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return MagicMock(returncode=1, stdout="", stderr="bad codec")
        return MagicMock(returncode=0)

    with patch("hermes.ingest.frames.subprocess.run", side_effect=runner):
        with pytest.raises(RuntimeError, match="ffprobe failed"):
            extract_keyframes(video, out_dir, n=4)


def test_extract_keyframes_ffprobe_unparseable_duration_falls_back(tmp_path: Path):
    """Some weird containers return non-numeric output — treat as 0 duration."""
    video = _fake_video(tmp_path)
    out_dir = tmp_path / "frames"

    with patch("hermes.ingest.frames.subprocess.run",
               side_effect=_make_runner("N/A")):
        frames = extract_keyframes(video, out_dir, n=4)

    assert len(frames) == 1


def test_extract_keyframes_missing_video_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="video not found"):
        extract_keyframes(tmp_path / "nope.mp4", tmp_path / "frames", n=2)


def test_extract_keyframes_creates_out_dir(tmp_path: Path):
    video = _fake_video(tmp_path)
    out_dir = tmp_path / "deep" / "nested" / "frames"
    assert not out_dir.exists()

    with patch("hermes.ingest.frames.subprocess.run", side_effect=_make_runner("10.0")):
        frames = extract_keyframes(video, out_dir, n=2)

    assert out_dir.exists()
    assert len(frames) == 2
