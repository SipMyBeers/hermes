from datetime import datetime
from email import message_from_bytes
from pathlib import Path
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo
from hermes.calendar.intent import MeetingIntent
from hermes.calendar.slots import TimeSlot
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


# --- Phase N.3 availability-aware drafter tests ------------------------------


def _captured_post(captured: dict):
    """Build a fake httpx.post that records the request body and returns
    a stub gemma4 response. Use with patch() on hermes.classifier.drafter
    .httpx.post."""
    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        resp = MagicMock()
        resp.json.return_value = {"response": "drafted reply"}
        resp.raise_for_status = MagicMock()
        return resp
    return fake_post


def test_draft_includes_slots_when_meeting_intent_fires(tmp_path, monkeypatch):
    """When detect_meeting_intent reports wants_meeting=True and
    next_slots returns three slots, the gemma4 prompt MUST contain the
    formatted slot lines so the model weaves them into the reply rather
    than inventing arbitrary times."""
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_intent = MeetingIntent(
        wants_meeting=True, preferred_window_hint="this week", confidence=0.9,
    )
    tz = ZoneInfo("America/Los_Angeles")
    fake_slots = [
        TimeSlot(start=datetime(2026, 5, 4, 9, 0, tzinfo=tz),
                 end=datetime(2026, 5, 4, 9, 30, tzinfo=tz)),
        TimeSlot(start=datetime(2026, 5, 4, 9, 30, tzinfo=tz),
                 end=datetime(2026, 5, 4, 10, 0, tzinfo=tz)),
        TimeSlot(start=datetime(2026, 5, 4, 10, 0, tzinfo=tz),
                 end=datetime(2026, 5, 4, 10, 30, tzinfo=tz)),
    ]
    monkeypatch.setattr(
        "hermes.calendar.intent.detect_meeting_intent",
        lambda m: fake_intent,
    )
    monkeypatch.setattr(
        "hermes.calendar.slots.next_slots",
        lambda path, n=3: fake_slots,
    )
    captured: dict = {}
    fake_path = tmp_path / "availability.toml"
    fake_path.write_text("timezone='UTC'\n[windows]\nmonday=[]\n")
    with patch("hermes.classifier.drafter.httpx.post",
               side_effect=_captured_post(captured)):
        text = draft_reply(
            msg, sender_name="Dylan", host="http://x",
            availability_path=fake_path,
        )
    assert text == "drafted reply"
    prompt = captured["json"]["prompt"]
    # The slot section header MUST be present.
    assert "proposed slots" in prompt
    # Each slot start should be formatted with weekday + month + day +
    # hour:minute. We assert the day of all three slots shows up.
    assert "May 04" in prompt
    # Three bulleted slot lines.
    assert prompt.count("\n  - ") >= 3


def test_draft_skips_slots_when_no_intent(tmp_path, monkeypatch):
    """When detect_meeting_intent reports False, the drafter must NOT
    inject the slot section even if availability_path is provided."""
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_intent = MeetingIntent(
        wants_meeting=False, preferred_window_hint="", confidence=0.95,
    )
    monkeypatch.setattr(
        "hermes.calendar.intent.detect_meeting_intent",
        lambda m: fake_intent,
    )
    # next_slots should not be reached, but defensive-stub it anyway.
    monkeypatch.setattr(
        "hermes.calendar.slots.next_slots",
        lambda path, n=3: [],
    )
    captured: dict = {}
    fake_path = tmp_path / "availability.toml"
    fake_path.write_text("timezone='UTC'\n[windows]\nmonday=[]\n")
    with patch("hermes.classifier.drafter.httpx.post",
               side_effect=_captured_post(captured)):
        draft_reply(msg, sender_name="Dylan", host="http://x",
                    availability_path=fake_path)
    prompt = captured["json"]["prompt"]
    assert "proposed slots" not in prompt


def test_draft_skips_slots_when_no_availability_path():
    """availability_path=None (the default) preserves pre-N.3 behaviour
    exactly — no detect_meeting_intent call, no slot section, prompt
    is the bare _PROMPT template."""
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    captured: dict = {}
    with patch("hermes.classifier.drafter.httpx.post",
               side_effect=_captured_post(captured)):
        draft_reply(msg, sender_name="Dylan", host="http://x")
    prompt = captured["json"]["prompt"]
    assert "proposed slots" not in prompt


def test_draft_skips_slots_when_no_slots_returned(tmp_path, monkeypatch):
    """Intent fires True but next_slots returns [] (e.g. fully booked
    next 14 days). Drafter still runs — it just doesn't append a slot
    section so the model falls back to a generic 'happy to find a
    time' reply."""
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_intent = MeetingIntent(
        wants_meeting=True, preferred_window_hint="", confidence=0.9,
    )
    monkeypatch.setattr(
        "hermes.calendar.intent.detect_meeting_intent",
        lambda m: fake_intent,
    )
    monkeypatch.setattr(
        "hermes.calendar.slots.next_slots",
        lambda path, n=3: [],
    )
    captured: dict = {}
    fake_path = tmp_path / "availability.toml"
    fake_path.write_text("timezone='UTC'\n[windows]\nmonday=[]\n")
    with patch("hermes.classifier.drafter.httpx.post",
               side_effect=_captured_post(captured)):
        draft_reply(msg, sender_name="Dylan", host="http://x",
                    availability_path=fake_path)
    prompt = captured["json"]["prompt"]
    assert "proposed slots" not in prompt


def test_draft_swallows_calendar_failure(tmp_path, monkeypatch):
    """Any exception from the calendar module (bad TOML, intent crash)
    must NOT break draft generation. Drafter logs a warning and falls
    back to the bare prompt."""
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())

    def _boom(_msg):
        raise RuntimeError("intent broke")
    monkeypatch.setattr(
        "hermes.calendar.intent.detect_meeting_intent", _boom
    )
    captured: dict = {}
    fake_path = tmp_path / "availability.toml"
    fake_path.write_text("timezone='UTC'\n[windows]\nmonday=[]\n")
    with patch("hermes.classifier.drafter.httpx.post",
               side_effect=_captured_post(captured)):
        text = draft_reply(msg, sender_name="Dylan", host="http://x",
                           availability_path=fake_path)
    # Drafter still produces a reply; calendar fail is swallowed.
    assert text == "drafted reply"
    prompt = captured["json"]["prompt"]
    assert "proposed slots" not in prompt
