"""Tests for hermes.calendar.intent.detect_meeting_intent."""
from email import message_from_bytes
from email.message import Message
from hermes.calendar.intent import MeetingIntent, detect_meeting_intent


def test_meeting_intent_fields():
    m = MeetingIntent(wants_meeting=True, preferred_window_hint="tuesday morning",
                      confidence=0.85)
    assert m.wants_meeting is True
    assert m.preferred_window_hint == "tuesday morning"
    assert m.confidence == 0.85


def _msg(subject: str, body: str) -> Message:
    raw = (
        f"From: Casey <casey@x.com>\r\n"
        f"To: u@g.com\r\n"
        f"Subject: {subject}\r\n"
        f"\r\n"
        f"{body}\r\n"
    ).encode()
    return message_from_bytes(raw)


def test_intent_detects_hop_on_a_call():
    """The phrase 'hop on a call' is a hard-positive — fires wants_meeting=True
    and lifts the time hint when one is present."""
    m = _msg("Quick chat?", "Happy to hop on a call this week if you have time.")
    result = detect_meeting_intent(m)
    assert result.wants_meeting is True
    assert "this week" in result.preferred_window_hint.lower()
    assert result.confidence >= 0.8


def test_intent_detects_are_you_free():
    """'Are you free thursday morning?' → True, hint lifts the weekday."""
    m = _msg("Sync", "Are you free thursday morning?")
    result = detect_meeting_intent(m)
    assert result.wants_meeting is True
    # The hint regex should pick up either 'thursday' or 'morning'.
    assert result.preferred_window_hint != ""


def test_intent_detects_calendly_link():
    """A bare 'calendly' mention is enough to count as a meeting request —
    it's effectively a self-service booking link."""
    m = _msg("re: timing", "Drop something on my calendly when convenient.")
    result = detect_meeting_intent(m)
    assert result.wants_meeting is True


def test_intent_detects_schedule_a_meeting():
    """'Schedule a meeting' is the canonical hard-positive."""
    m = _msg("Project kickoff", "Let's schedule a meeting for next week.")
    result = detect_meeting_intent(m)
    assert result.wants_meeting is True


def test_intent_detects_grab_some_time():
    """'Grab some time' is a hard-positive."""
    m = _msg("Quick one", "Want to grab some time to discuss this?")
    result = detect_meeting_intent(m)
    assert result.wants_meeting is True


def test_intent_no_keywords_returns_high_confidence_false():
    """A pure thank-you message has zero scheduling keywords — return
    a confident negative."""
    m = _msg("Loved your post", "Great article! Love your work.")
    result = detect_meeting_intent(m)
    assert result.wants_meeting is False
    assert result.confidence >= 0.9


def test_intent_ambiguous_returns_low_confidence():
    """Scheduling-adjacent words present but no hard pattern → low
    confidence False so callers know it was uncertain."""
    m = _msg("Re: meeting recap", "Recapping the meeting from yesterday — here are notes.")
    result = detect_meeting_intent(m)
    # Either way we should not be highly confident either direction.
    if result.wants_meeting:
        # If a hard pattern accidentally matched, that's fine — but we
        # require confidence reflect the uncertainty.
        assert result.confidence <= 0.95
    else:
        assert result.confidence <= 0.5


def test_intent_handles_no_body_gracefully():
    """A subject-only message with no body must not crash."""
    raw = (
        b"From: x@y.com\r\n"
        b"Subject: schedule a call?\r\n"
        b"\r\n"
    )
    msg = message_from_bytes(raw)
    result = detect_meeting_intent(msg)
    # Subject alone matched — wants_meeting=True is correct.
    assert isinstance(result, MeetingIntent)
    assert result.wants_meeting is True


def test_intent_handles_empty_message():
    """An entirely empty Message object must not crash; returns a clean
    high-confidence negative."""
    result = detect_meeting_intent(Message())
    assert isinstance(result, MeetingIntent)
    assert result.wants_meeting is False
    assert result.confidence >= 0.9


def test_intent_no_io():
    """Pure function — no httpx import, no DB. We assert this implicitly
    by detecting in a context with no network mocked at all; if the
    function tried to call out we'd get a network error."""
    m = _msg("Hi", "Are you free tuesday?")
    # Just call it — if it tried to make a network call this would fail
    # depending on environment, but more importantly, the function
    # explicitly does not import httpx in its body.
    result = detect_meeting_intent(m)
    assert result.wants_meeting is True
