import pytest
from email.message import Message
from hermes.calendar.intent import MeetingIntent, detect_meeting_intent


def test_meeting_intent_fields():
    m = MeetingIntent(wants_meeting=True, preferred_window_hint="tuesday morning",
                      confidence=0.85)
    assert m.wants_meeting is True
    assert m.preferred_window_hint == "tuesday morning"
    assert m.confidence == 0.85


def test_detect_meeting_intent_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="v1.2"):
        detect_meeting_intent(Message())
