from email import message_from_bytes
from pathlib import Path
from unittest.mock import patch, MagicMock
import httpx
from hermes.digest.enrich import summarize_for_brief, EmailBrief, Tool

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def _msg():
    return message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())


def test_summarize_parses_response():
    """Valid gemma4 JSON populates summary, actions, tools dataclass fields."""
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "response": (
            '{"summary":"Sam at Boarhog wants a hosting renewal quote.",'
            '"actions":["Reply with renewal terms","Send invoice"],'
            '"tools":[{"name":"Stripe","description":"payments platform"}]}'
        )
    }
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.digest.enrich.httpx.post", return_value=fake_resp):
        brief = summarize_for_brief(_msg(), model="gemma4", host="http://x")
    assert isinstance(brief, EmailBrief)
    assert "Boarhog" in brief.summary
    assert brief.actions == ("Reply with renewal terms", "Send invoice")
    assert len(brief.tools) == 1
    assert brief.tools[0] == Tool(name="Stripe", description="payments platform")


def test_summarize_handles_http_error():
    """Ollama unreachable -> empty EmailBrief (digest still renders)."""
    with patch(
        "hermes.digest.enrich.httpx.post",
        side_effect=httpx.HTTPError("connection refused"),
    ):
        brief = summarize_for_brief(_msg(), model="gemma4", host="http://x")
    assert brief.summary == ""
    assert brief.actions == ()
    assert brief.tools == ()


def test_summarize_handles_unparseable_json():
    """gemma4 returns prose -> empty EmailBrief, no exception."""
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": "not json at all"}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.digest.enrich.httpx.post", return_value=fake_resp):
        brief = summarize_for_brief(_msg(), model="gemma4", host="http://x")
    assert brief == EmailBrief(summary="", actions=(), tools=())


def test_summarize_drops_empty_action_strings_and_nameless_tools():
    """Defensive: malformed-but-shaped JSON shouldn't leak empty entries."""
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "response": (
            '{"summary":"x","actions":["do this","",null,"and that"],'
            '"tools":[{"name":"","description":"d"},{"name":"Datadog","description":""}]}'
        )
    }
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.digest.enrich.httpx.post", return_value=fake_resp):
        brief = summarize_for_brief(_msg())
    assert brief.actions == ("do this", "and that")
    assert brief.tools == (Tool(name="Datadog", description=""),)
