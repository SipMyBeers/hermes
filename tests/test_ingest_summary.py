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
