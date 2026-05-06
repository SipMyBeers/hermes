from __future__ import annotations
from email.message import Message
from pathlib import Path
import httpx
from loguru import logger
from hermes.classifier.llm import _body_text

_PROMPT = """You are drafting a brief, professional reply on behalf of {sender_name}.
- Two short paragraphs maximum.
- Tone: friendly, direct, no fluff.
- Do NOT make up facts or commitments. If the answer requires info you don't have, ask one clarifying question.
- End with "{sender_name}".

Original email:
From: {from_}
Subject: {subject}
Body:
{body}

Reply:
"""


def draft_reply(
    msg: Message,
    sender_name: str,
    model: str = "gemma4",
    host: str = "http://localhost:11434",
    timeout: float = 60.0,
    availability_path: Path | None = None,
) -> str:
    """Generate a reply draft.

    Phase N.3: when `availability_path` is provided AND the message
    looks like a meeting request (per `detect_meeting_intent`), append
    three concrete proposed slots from `next_slots` to the prompt and
    instruct the LLM to weave them into the reply rather than invent
    arbitrary times. The default `availability_path=None` keeps every
    pre-N.3 caller working unchanged — no calendar awareness, no slot
    section, drafter behaves exactly as before.
    """
    slot_section = ""
    if availability_path is not None:
        try:
            from hermes.calendar.intent import detect_meeting_intent
            from hermes.calendar.slots import next_slots
            intent = detect_meeting_intent(msg)
            if intent.wants_meeting:
                slots = next_slots(availability_path, n=3)
                if slots:
                    slot_lines = "\n".join(
                        f"  - {s.start.strftime('%a %b %d, %I:%M %p %Z')}"
                        for s in slots
                    )
                    slot_section = (
                        "\n\nThe sender wants to meet. Weave THESE THREE "
                        "proposed slots into the reply naturally — do not "
                        "invent other times:\n"
                        f"{slot_lines}\n"
                    )
        except Exception as e:
            # Calendar awareness must never break draft generation —
            # any failure (bad TOML, missing tz, intent crash) just
            # falls back to the non-availability prompt.
            logger.warning("availability-aware draft skipped: {}", e)

    prompt = _PROMPT.format(
        sender_name=sender_name,
        from_=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        body=_body_text(msg)[:2500],
    ) + slot_section
    try:
        resp = httpx.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        return str(resp.json().get("response", "")).strip()
    except Exception as e:
        logger.warning("drafter failed: {}", e)
        return ""
