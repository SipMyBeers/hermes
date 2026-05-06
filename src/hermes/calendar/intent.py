"""Detect 'wants to meet' intent in inbound mail."""
from __future__ import annotations
from dataclasses import dataclass
from email.message import Message
import re
from hermes.classifier.llm import _body_text


@dataclass(frozen=True)
class MeetingIntent:
    wants_meeting: bool
    preferred_window_hint: str    # raw quoted phrase from the email if present
    confidence: float


# Hard-positive regex: phrases that almost always mean "let's meet". Hits
# any of these and we report wants_meeting=True without an LLM call.
_HARD_PATTERNS = [
    r"\b(?:hop|jump|get)\s+on\s+(?:a\s+)?(?:call|meeting)\b",
    r"\bschedule\s+(?:a\s+)?(?:call|meeting|chat|sync|time)\b",
    r"\b(?:grab|find|set\s+up|book)\s+(?:a|some)\s+time\b",
    r"\b(?:catch|meet|sync)\s+up\b",
    r"\b(?:are\s+you|you)\s+(?:free|available)\b",
    r"\b(?:calendly|calendar)\b",
    r"\bwhen\s+(?:works|are\s+you|can\s+you)\b",
]
_HARD_RE = re.compile("|".join(_HARD_PATTERNS), re.IGNORECASE)

# Time-window hint extraction (rough — the slot resolver does the real
# work, this is just a label we surface for telemetry / future LLM
# fallback prompts).
_HINT_PATTERNS = [
    r"\bthis\s+(?:mon|tues|wed|thurs|fri|sat|sun)(?:day)?\b",
    r"\b(?:mon|tues|wed|thurs|fri|sat|sun)(?:day)?\b",
    r"\b(?:this|next|coming)\s+(?:week|month)\b",
    r"\b(?:morning|afternoon|evening)s?\b",
    r"\bweekend\b",
]
_HINT_RE = re.compile("|".join(_HINT_PATTERNS), re.IGNORECASE)

# Quick-negative gate: if NONE of these scheduling-adjacent words appear
# anywhere in the text, the message is not a meeting request — skip the
# downstream LLM entirely. Use word-START boundaries (\b) without an end
# boundary so 'meeting', 'calendly', 'scheduling', 'appointments' all
# count — these are real signals that get false-negatived by strict
# whole-word matching.
_KEYWORD_RE = re.compile(
    r"\b(?:meet|call|chat|sync|time|talk|schedul|calendar|calendly|"
    r"appointment)",
    re.IGNORECASE,
)


def detect_meeting_intent(msg: Message) -> MeetingIntent:
    """Regex-first hybrid detector.

    Cheap path: the subject + first 4000 chars of body are matched
    against hard-positive phrases. Hits report wants_meeting=True with
    confidence 0.9.

    Quick negative: if none of the scheduling-adjacent keywords (meet,
    call, schedule, etc.) appear, return wants_meeting=False with high
    confidence (0.95) so callers know it was a clean negative.

    Ambiguous middle (keywords present, no hard pattern): currently
    returns wants_meeting=False with low confidence (0.4) so callers can
    treat 'uncertain' differently from 'definitely not.' v1.1 follow-up
    will defer to gemma4 here. We keep the regex-only path for v1 so
    every classification stays fast — no extra LLM round-trip per
    inbound message.

    No I/O: pure function over the parsed Message.
    """
    subject = str(msg.get("Subject", "") or "")
    try:
        body = _body_text(msg)
    except Exception:
        body = ""
    haystack = f"{subject}\n{body[:4000]}"

    hard = _HARD_RE.search(haystack)
    if hard:
        hint_match = _HINT_RE.search(haystack)
        return MeetingIntent(
            wants_meeting=True,
            preferred_window_hint=(hint_match.group(0) if hint_match else ""),
            confidence=0.9,
        )

    if not _KEYWORD_RE.search(haystack):
        return MeetingIntent(
            wants_meeting=False, preferred_window_hint="", confidence=0.95,
        )

    # Ambiguous: scheduling-adjacent words present but no hard pattern
    # fired. v1: report False with low confidence; v1.1 will route to
    # gemma4 for these.
    return MeetingIntent(
        wants_meeting=False, preferred_window_hint="", confidence=0.4,
    )
