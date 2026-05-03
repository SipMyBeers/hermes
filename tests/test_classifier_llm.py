from email import message_from_bytes
from pathlib import Path
from unittest.mock import patch, MagicMock
from hermes.classifier.llm import classify_llm, LLMResult

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def _msg(name: str):
    return message_from_bytes((FIXTURES / name).read_bytes())


def test_classify_llm_parses_response():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "model": "qwen3:4b",
        "response": '{"label": "client", "confidence": 0.82, "why": "asks question about service"}',
    }
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.classifier.llm.httpx.post", return_value=fake_resp) as p:
        r = classify_llm(_msg("ambiguous.eml"), model="qwen3:4b", host="http://x")
    assert isinstance(r, LLMResult)
    assert r.label == "client"
    assert 0.81 < r.confidence < 0.83
    p.assert_called_once()


def test_classify_llm_falls_back_on_unparseable():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": "not json at all"}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.classifier.llm.httpx.post", return_value=fake_resp):
        r = classify_llm(_msg("ambiguous.eml"), model="qwen3:4b", host="http://x")
    assert r.label == "unknown"
    assert r.confidence == 0.0


def test_classify_llm_clamps_label():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": '{"label": "spam", "confidence": 0.9}'}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.classifier.llm.httpx.post", return_value=fake_resp):
        r = classify_llm(_msg("ambiguous.eml"), model="qwen3:4b", host="http://x")
    assert r.label == "other"  # unknown -> other
