"""Detect 'wants to meet' intent in inbound mail."""
from __future__ import annotations
from dataclasses import dataclass
from email.message import Message


@dataclass(frozen=True)
class MeetingIntent:
    wants_meeting: bool
    preferred_window_hint: str    # raw quoted phrase from the email if present
    confidence: float


def detect_meeting_intent(msg: Message) -> MeetingIntent:
    """v1.2 implementation:
      - Stage 1: regex sweep for 'meet|call|chat|sync|hop on|jump on|grab time|
        catch up|schedule (a|some) (call|meeting|time)'.
      - Stage 2: gemma4 prompt for ambiguous cases.
      - preferred_window_hint extracts the user's stated preferred time
        ('tuesday morning', 'this week') if mentioned.
    """
    raise NotImplementedError("v1.2: regex + gemma4 fallback")
