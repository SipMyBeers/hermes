from __future__ import annotations
from email.message import Message
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
) -> str:
    prompt = _PROMPT.format(
        sender_name=sender_name,
        from_=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        body=_body_text(msg)[:2500],
    )
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
