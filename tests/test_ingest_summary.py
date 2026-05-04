import base64
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from hermes.ingest.summary import summarize, ContentSummary


def test_summarize_parses_response():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": '{"summary": "X is a tool.", "actions": ["Try X"], "tools": [{"name": "X", "description": "tool"}]}'}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.ingest.summary.httpx.post", return_value=fake_resp):
        r = summarize(title="Hi", text="some content")
    assert r.summary == "X is a tool."
    assert r.actions == ("Try X",)
    assert r.tools == ({"name": "X", "description": "tool"},)


def test_summarize_handles_empty_text():
    r = summarize(title="t", text="")
    assert r.summary == ""
    assert r.actions == ()


def test_summarize_handles_http_error():
    import httpx as _httpx
    with patch("hermes.ingest.summary.httpx.post", side_effect=_httpx.HTTPError("down")):
        r = summarize(title="t", text="content")
    assert r.summary == ""


def _ok_resp() -> MagicMock:
    fake = MagicMock()
    fake.json.return_value = {"response": '{"summary": "ok", "actions": [], "tools": []}'}
    fake.raise_for_status = MagicMock()
    return fake


def test_summarize_includes_images_in_payload(tmp_path: Path):
    """When images are passed, they're base64-encoded and sent via the
    `images` field on the Ollama /api/generate payload."""
    img1 = tmp_path / "frame_00.jpg"
    img1.write_bytes(b"\xff\xd8\xff\xe0frame00bytes")
    img2 = tmp_path / "frame_01.jpg"
    img2.write_bytes(b"\xff\xd8\xff\xe0frame01bytes")

    captured: dict = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        return _ok_resp()

    with patch("hermes.ingest.summary.httpx.post", side_effect=fake_post):
        summarize(title="T", text="transcript", images=[img1, img2])

    assert captured["url"].endswith("/api/generate")
    body = captured["json"]
    assert "images" in body
    assert len(body["images"]) == 2
    # Verify the encoding round-trips back to the original bytes.
    assert base64.b64decode(body["images"][0]) == img1.read_bytes()
    assert base64.b64decode(body["images"][1]) == img2.read_bytes()


def test_summarize_handles_image_read_failure(tmp_path: Path):
    """Missing keyframe paths are skipped (warned), but the call still goes
    through with the readable images."""
    good = tmp_path / "good.jpg"
    good.write_bytes(b"\xff\xd8\xff\xe0g")
    missing = tmp_path / "nope.jpg"  # never created

    captured: dict = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["json"] = json
        return _ok_resp()

    with patch("hermes.ingest.summary.httpx.post", side_effect=fake_post):
        r = summarize(title="T", text="transcript", images=[missing, good])

    # We should still succeed and the payload should contain only the readable image.
    assert r.summary == "ok"
    assert "images" in captured["json"]
    assert len(captured["json"]["images"]) == 1
    assert base64.b64decode(captured["json"]["images"][0]) == good.read_bytes()


def test_summarize_includes_ocr_in_prompt(tmp_path: Path):
    """OCR snippets land in the prompt under 'On-screen text:' header."""
    captured: dict = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["json"] = json
        return _ok_resp()

    with patch("hermes.ingest.summary.httpx.post", side_effect=fake_post):
        summarize(
            title="T",
            text="hello there",
            ocr_snippets=["BUY NOW", "limited time only"],
        )

    prompt = captured["json"]["prompt"]
    assert "On-screen text" in prompt
    assert "BUY NOW" in prompt
    assert "limited time only" in prompt


def test_summarize_skips_blank_ocr_snippets(tmp_path: Path):
    """Empty/whitespace OCR strings are filtered out (don't pollute prompt)."""
    captured: dict = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["json"] = json
        return _ok_resp()

    with patch("hermes.ingest.summary.httpx.post", side_effect=fake_post):
        summarize(title="T", text="hi", ocr_snippets=["", "   ", "\n"])

    # No OCR section should appear when every snippet is blank.
    prompt = captured["json"]["prompt"]
    assert "On-screen text" not in prompt


def test_summarize_no_images_no_ocr_keeps_legacy_payload():
    """Existing callers that pass only title+text should produce a payload
    with no `images` key (no regression)."""
    captured: dict = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["json"] = json
        return _ok_resp()

    with patch("hermes.ingest.summary.httpx.post", side_effect=fake_post):
        summarize(title="T", text="article body")

    assert "images" not in captured["json"]
    assert "On-screen text" not in captured["json"]["prompt"]


def test_summarize_with_images_only_no_text(tmp_path: Path):
    """Caller passes images but empty text — still proceeds (vision-only)."""
    img = tmp_path / "frame.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0px")

    captured: dict = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["json"] = json
        return _ok_resp()

    with patch("hermes.ingest.summary.httpx.post", side_effect=fake_post):
        r = summarize(title="silent video", text="", images=[img])

    assert r.summary == "ok"
    assert "images" in captured["json"]


def test_summarize_all_image_reads_fail_still_returns(tmp_path: Path):
    """When every image path fails to read, payload omits `images` but
    the call still goes through."""
    captured: dict = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["json"] = json
        return _ok_resp()

    with patch("hermes.ingest.summary.httpx.post", side_effect=fake_post):
        r = summarize(
            title="T", text="some text",
            images=[tmp_path / "missing1.jpg", tmp_path / "missing2.jpg"],
        )

    assert r.summary == "ok"
    assert "images" not in captured["json"]
