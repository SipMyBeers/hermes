from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from hermes.ingest.video import extract_video, cleanup_workdir


def _yt_dlp_writes_mp4(out_dir: Path, vid_id: str = "abc") -> None:
    """Write a fake mp4 inside the yt-dlp output dir."""
    (out_dir / f"{vid_id}.mp4").write_bytes(b"fake-mp4-bytes")


def _build_runner(*, ytdlp_rc: int = 0, ffmpeg_rc: int = 0,
                  yt_meta: str = '{"title": "Sample Reel", "id": "abc"}'):
    """Build a fake subprocess.run for video.py.

    yt-dlp call: writes a fake mp4 in the -o template dir, returns the meta.
    ffmpeg call: when rc=0, writes the wav (last arg).
    """
    def runner(cmd, **kwargs):
        prog = cmd[0]
        if prog == "yt-dlp":
            if ytdlp_rc == 0:
                idx = cmd.index("-o")
                out_tpl = Path(cmd[idx + 1])
                _yt_dlp_writes_mp4(out_tpl.parent)
            return MagicMock(
                returncode=ytdlp_rc,
                stdout=(yt_meta + "\n") if ytdlp_rc == 0 else "",
                stderr="" if ytdlp_rc == 0 else "ERROR: Private video",
            )
        if prog == "ffmpeg":
            if ffmpeg_rc == 0:
                # output is the last arg.
                Path(cmd[-1]).write_bytes(b"RIFFwav-bytes")
            return MagicMock(returncode=ffmpeg_rc, stderr=b"" if ffmpeg_rc == 0 else b"bad codec")
        # Anything else (shouldn't happen in these tests).
        return MagicMock(returncode=0, stdout="", stderr="")
    return runner


def test_extract_video_happy_path(tmp_path: Path):
    """yt-dlp -> mp4 -> ffmpeg wav -> whisper -> keyframes -> ocr."""
    runner = _build_runner()
    fake_frames = [tmp_path / "f0.jpg", tmp_path / "f1.jpg"]
    fake_frames[0].write_bytes(b"\xff\xd8frame0")
    fake_frames[1].write_bytes(b"\xff\xd8frame1")

    with patch("hermes.ingest.video.subprocess.run", side_effect=runner), \
         patch("hermes.ingest.video.transcribe_voice", return_value="this is the transcript"), \
         patch("hermes.ingest.frames.extract_keyframes", return_value=fake_frames), \
         patch("hermes.ingest.ocr.ocr_images", return_value=["BUY NOW", "TIP #3"]):
        doc = extract_video("https://www.instagram.com/reel/abc/")

    try:
        assert doc.url == "https://www.instagram.com/reel/abc/"
        assert doc.title == "Sample Reel"
        assert doc.text == "this is the transcript"
        assert doc.keyframes == tuple(fake_frames)
        assert doc.ocr_snippets == ("BUY NOW", "TIP #3")
        assert doc.workdir is not None
        assert doc.workdir.exists()
    finally:
        cleanup_workdir(doc)


def test_extract_video_yt_dlp_failure_raises():
    runner = _build_runner(ytdlp_rc=1)
    with patch("hermes.ingest.video.subprocess.run", side_effect=runner):
        with pytest.raises(RuntimeError, match="yt-dlp failed"):
            extract_video("https://www.instagram.com/reel/private/")


def test_extract_video_no_video_file_raises(tmp_path: Path):
    """yt-dlp succeeded but didn't write any video file — clear error."""
    def runner(cmd, **kwargs):
        if cmd[0] == "yt-dlp":
            return MagicMock(returncode=0, stdout='{"title": "x"}\n', stderr="")
        return MagicMock(returncode=0)

    with patch("hermes.ingest.video.subprocess.run", side_effect=runner):
        with pytest.raises(RuntimeError, match="no video file"):
            extract_video("https://example.com/video")


def test_extract_video_ffmpeg_audio_failure_keeps_workdir(tmp_path: Path):
    """When ffmpeg fails to extract audio, RuntimeError surfaces and the
    work dir is preserved for post-mortem (not cleaned up)."""
    runner = _build_runner(ffmpeg_rc=1)
    captured: dict = {}

    # Wrap rmtree to record whether it was called.
    import shutil as _shutil
    real_rmtree = _shutil.rmtree

    def spy_rmtree(path, **kwargs):
        captured["rmtree"] = str(path)
        return real_rmtree(path, **kwargs)

    with patch("hermes.ingest.video.subprocess.run", side_effect=runner), \
         patch("hermes.ingest.video.shutil.rmtree", side_effect=spy_rmtree):
        with pytest.raises(RuntimeError, match="ffmpeg audio extract failed"):
            extract_video("https://www.instagram.com/reel/abc/")

    # The work dir was kept (rmtree was NOT called for cleanup) — the only
    # rmtree call would be from cleanup_workdir, which we didn't invoke.
    assert "rmtree" not in captured


def test_extract_video_keyframe_failure_falls_back_to_transcript_only(tmp_path: Path):
    """If keyframe extraction blows up, the transcript-only doc is still
    returned (no keyframes, no OCR) and the workdir is cleaned up."""
    runner = _build_runner()

    def raise_keyframes(*a, **kw):
        raise RuntimeError("ffprobe failed: no streams")

    with patch("hermes.ingest.video.subprocess.run", side_effect=runner), \
         patch("hermes.ingest.video.transcribe_voice", return_value="just words"), \
         patch("hermes.ingest.frames.extract_keyframes", side_effect=raise_keyframes):
        doc = extract_video("https://www.instagram.com/reel/abc/")

    assert doc.text == "just words"
    assert doc.keyframes == ()
    assert doc.ocr_snippets == ()
    # No workdir on transcript-only docs (cleaned up in finally).
    assert doc.workdir is None


def test_cleanup_workdir_removes_dir(tmp_path: Path):
    workdir = tmp_path / "to-delete"
    workdir.mkdir()
    (workdir / "frame_00.jpg").write_bytes(b"x")
    from hermes.ingest.web import ExtractedDoc
    doc = ExtractedDoc(
        title="t", text="x", url="u",
        keyframes=(workdir / "frame_00.jpg",),
        workdir=workdir,
    )
    cleanup_workdir(doc)
    assert not workdir.exists()


def test_cleanup_workdir_noop_for_web_doc():
    """Doc without a workdir is a no-op."""
    from hermes.ingest.web import ExtractedDoc
    doc = ExtractedDoc(title="t", text="x", url="u")
    cleanup_workdir(doc)  # must not raise
