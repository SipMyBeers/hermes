import json
import pytest
from unittest.mock import patch, MagicMock
from hermes.db import connect, migrate
from hermes.ingest.url import ingest, classify_url, find_url, Digestion
from hermes.ingest.web import ExtractedDoc
from hermes.ingest.summary import ContentSummary


def test_classify_url_web():
    assert classify_url("https://example.com/article") == "web"
    assert classify_url("https://news.ycombinator.com/item?id=1") == "web"


def test_classify_url_video():
    assert classify_url("https://www.instagram.com/reel/abc/") == "video"
    assert classify_url("https://www.youtube.com/watch?v=abc") == "video"
    assert classify_url("https://youtu.be/abc") == "video"
    assert classify_url("https://tiktok.com/@x/video/123") == "video"


def test_find_url_extracts_first():
    assert find_url("hey check https://x.com/foo cool") == "https://x.com/foo"
    assert find_url("no url here") is None


def test_ingest_web_persists_and_returns(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    fake_doc = ExtractedDoc(title="Hello", text="World content", url="https://example.com/x")
    fake_summary = ContentSummary(summary="A short article.",
                                   actions=("Read it",),
                                   tools=({"name": "Tool", "description": "desc"},))
    with patch("hermes.ingest.url.extract_web", return_value=fake_doc), \
         patch("hermes.ingest.url.summarize", return_value=fake_summary):
        d = ingest("https://example.com/x", db, source_chat_id=42)
    assert d.kind == "web"
    assert d.title == "Hello"
    assert d.summary == "A short article."
    assert d.error == ""
    rows = list(db.execute("SELECT url, summary, actions FROM digestions"))
    assert rows[0][0] == "https://example.com/x"
    assert json.loads(rows[0][2]) == ["Read it"]


def test_ingest_video_returns_error_with_v13_message(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    d = ingest("https://www.instagram.com/reel/abc/", db)
    assert d.kind == "video"
    assert "v1.3" in d.error
    assert d.summary == ""


def test_ingest_records_extraction_failure(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    with patch("hermes.ingest.url.extract_web", side_effect=Exception("network down")):
        d = ingest("https://example.com/y", db)
    assert "network down" in d.error
    assert d.summary == ""
