from email import message_from_bytes
from pathlib import Path
from unittest.mock import patch, MagicMock
from hermes.classifier.drafter import draft_reply

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def test_draft_reply_returns_text():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": "Hi Sam, thanks for the question..."}
    fake_resp.raise_for_status = MagicMock()
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    with patch("hermes.classifier.drafter.httpx.post", return_value=fake_resp):
        text = draft_reply(msg, sender_name="Dylan", model="gemma4", host="http://x")
    assert "Hi Sam" in text


def test_draft_reply_handles_failure():
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    with patch("hermes.classifier.drafter.httpx.post", side_effect=Exception("boom")):
        text = draft_reply(msg, sender_name="Dylan", model="gemma4", host="http://x")
    assert text == ""
