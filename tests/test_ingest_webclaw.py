"""Tests for the webclaw subprocess wrapper.

webclaw may or may not be installed locally — every test patches
shutil.which / subprocess.run so the suite is deterministic regardless."""
from __future__ import annotations
import subprocess
from unittest.mock import MagicMock, patch
from hermes.ingest.webclaw import extract, WebclawResult


def _fake_proc(stdout: bytes = b"", stderr: bytes = b"", rc: int = 0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = rc
    return proc


def test_extract_returns_none_when_webclaw_missing():
    with patch("hermes.ingest.webclaw.shutil.which", return_value=None):
        assert extract("https://example.com") is None


def test_extract_parses_stdout():
    with patch("hermes.ingest.webclaw.shutil.which", return_value="/usr/local/bin/webclaw"), \
         patch("hermes.ingest.webclaw.subprocess.run",
               return_value=_fake_proc(stdout=b"Hello body text\nmore content")):
        r = extract("https://example.com")
    assert r is not None
    assert "Hello body text" in r.text
    assert r.title == ""


def test_extract_lifts_title_from_first_h1():
    md = b"# My Article\n\nbody paragraph with some content."
    with patch("hermes.ingest.webclaw.shutil.which", return_value="/usr/local/bin/webclaw"), \
         patch("hermes.ingest.webclaw.subprocess.run",
               return_value=_fake_proc(stdout=md)):
        r = extract("https://example.com")
    assert r is not None
    assert r.title == "My Article"
    assert "body paragraph" in r.text


def test_extract_returns_none_on_nonzero_exit():
    with patch("hermes.ingest.webclaw.shutil.which", return_value="/usr/local/bin/webclaw"), \
         patch("hermes.ingest.webclaw.subprocess.run",
               return_value=_fake_proc(stderr=b"network error", rc=1)):
        assert extract("https://example.com") is None


def test_extract_returns_none_on_timeout():
    with patch("hermes.ingest.webclaw.shutil.which", return_value="/usr/local/bin/webclaw"), \
         patch("hermes.ingest.webclaw.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="webclaw", timeout=120)):
        assert extract("https://example.com") is None


def test_extract_returns_none_on_filenotfound():
    """Race: shutil.which says yes, but subprocess.run can still
    FileNotFoundError if PATH changes mid-call. Still must fall through."""
    with patch("hermes.ingest.webclaw.shutil.which", return_value="/usr/local/bin/webclaw"), \
         patch("hermes.ingest.webclaw.subprocess.run",
               side_effect=FileNotFoundError("webclaw")):
        assert extract("https://example.com") is None


def test_extract_returns_none_on_empty_stdout():
    with patch("hermes.ingest.webclaw.shutil.which", return_value="/usr/local/bin/webclaw"), \
         patch("hermes.ingest.webclaw.subprocess.run",
               return_value=_fake_proc(stdout=b"   \n  \n")):
        assert extract("https://example.com") is None


def test_extract_passes_crawl_args_when_depth_set():
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_proc(stdout=b"# x\nbody")

    with patch("hermes.ingest.webclaw.shutil.which", return_value="/usr/local/bin/webclaw"), \
         patch("hermes.ingest.webclaw.subprocess.run", side_effect=fake_run):
        r = extract("https://example.com/docs", depth=2)
    assert r is not None
    cmd = captured["cmd"]
    assert "--crawl" in cmd
    # depth value follows the --depth flag immediately
    i = cmd.index("--depth")
    assert cmd[i + 1] == "2"


def test_extract_passes_format_flag():
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_proc(stdout=b"body")

    with patch("hermes.ingest.webclaw.shutil.which", return_value="/usr/local/bin/webclaw"), \
         patch("hermes.ingest.webclaw.subprocess.run", side_effect=fake_run):
        extract("https://example.com", output_format="markdown")
    cmd = captured["cmd"]
    i = cmd.index("-f")
    assert cmd[i + 1] == "markdown"


def test_webclaw_result_is_frozen():
    r = WebclawResult(text="x", title="t")
    import dataclasses
    try:
        r.text = "y"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("WebclawResult should be frozen")
