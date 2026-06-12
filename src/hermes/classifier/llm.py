from __future__ import annotations
from dataclasses import dataclass
from email.message import Message
import json
import httpx
from loguru import logger

ALLOWED_LABELS = {"promo", "client", "personal", "transactional", "other"}

_PROMPT = """You are classifying an email into ONE label. Respond ONLY with JSON of the form:
{{"label": "<label>", "confidence": <0..1>, "why": "<short reason>"}}

Allowed labels (use exactly one):
- promo         : marketing, sales, newsletters, deals
- client        : a paying or prospective business client of mine asking about services
- personal     : friends, family, personal correspondence
- transactional : receipts, order confirmations, payment notifications
- other         : doesn't fit any above

Email:
From: {sender}
Subject: {subject}
Body (first 1500 chars):
{body}
"""


@dataclass(frozen=True)
class LLMResult:
    label: str
    confidence: float
    why: str


def _body_text(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def classify_llm(
    msg: Message,
    model: str = "qwen3:4b",
    host: str = "http://localhost:11434",
    timeout: float = 30.0,
    retries: int = 1,
) -> LLMResult:
    prompt = _PROMPT.format(
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        body=_body_text(msg)[:1500],
    )
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
    last_err = ""
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(f"{host}/api/generate", json=payload, timeout=timeout)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("ollama unreachable: {}", e)
            return LLMResult(label="unknown", confidence=0.0, why=f"ollama error: {e}")

        raw = resp.json().get("response", "")
        try:
            data = json.loads(raw)
            label = str(data.get("label", "")).lower()
            if label not in ALLOWED_LABELS:
                label = "other"
            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            why = str(data.get("why", ""))
            return LLMResult(label=label, confidence=confidence, why=why)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            last_err = str(e)
            if attempt < retries:
                logger.debug(
                    "llm response unparseable (attempt {}): {!r}; retrying",
                    attempt + 1, raw[:120],
                )
                continue
            logger.warning("llm response unparseable after retries: {!r} -> {}", raw[:200], e)
    return LLMResult(label="unknown", confidence=0.0, why=f"unparseable response: {last_err}")
