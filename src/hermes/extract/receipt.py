"""Extract structured fields from a transactional 'receipt' email via gemma4.

Hooked from hermes.mail.daemon when classification == 'transactional'; the
returned ReceiptInfo is fed to hermes.extract.ledger_writer.maybe_write_receipt
which appends a `direction='in'` row to the ledger when the extraction is
confident enough. End of year: tax-ready CSV from `ledger`.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from email.message import Message
import json
import httpx
from loguru import logger
from hermes.classifier.llm import _body_text


@dataclass(frozen=True)
class ReceiptInfo:
    vendor: str                       # e.g. "Stripe", "Cloudflare", "Anthropic"
    amount_cents: int                 # 0 if not found
    currency: str                     # "USD" default
    line_items: tuple[str, ...]       # raw item descriptions if extracted
    date: date | None                 # the charge date
    confidence: float                 # 0.0 if extraction failed


_PROMPT = """Extract receipt fields from this email. Output JSON ONLY:

{{
  "vendor": "<merchant name, e.g. Stripe, Cloudflare, Anthropic>",
  "amount_cents": <integer; total charged in cents>,
  "currency": "<ISO 4217 e.g. USD; default USD if unclear>",
  "line_items": ["<one short label per item charged>"],
  "date": "<YYYY-MM-DD of the charge if present, else empty string>",
  "confidence": <0..1 — how sure you are this is a real receipt vs marketing>
}}

Rules:
- If amount can't be confidently extracted, set amount_cents=0 and confidence < 0.3.
- 'line_items' may be empty if the receipt is a single item.
- 'date' is the charge/transaction date, NOT today.

Email:
From: {sender}
Subject: {subject}
Body (first 4000 chars):
{body}
"""


_EMPTY = ReceiptInfo(
    vendor="", amount_cents=0, currency="USD",
    line_items=(), date=None, confidence=0.0,
)


def extract_receipt(
    msg: Message,
    model: str = "gemma4",
    host: str = "http://localhost:11434",
    timeout: float = 60.0,
) -> ReceiptInfo:
    """Run gemma4 over the receipt email and return structured fields.

    Returns ReceiptInfo(confidence=0.0) on any HTTP / JSON / parse failure
    so callers can degrade gracefully. The downstream ledger writer's
    confidence gate will skip these.
    """
    prompt = _PROMPT.format(
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        body=_body_text(msg)[:4000],
    )
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
    try:
        resp = httpx.post(f"{host}/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        data = json.loads(raw)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("receipt extraction transport failed: {}", e)
        return _EMPTY

    try:
        vendor = str(data.get("vendor", "")).strip()
        amount = int(data.get("amount_cents", 0) or 0)
        currency = str(data.get("currency", "USD")).strip().upper() or "USD"
        line_items = tuple(
            str(li).strip()
            for li in (data.get("line_items") or [])
            if li
        )
        date_str = str(data.get("date", "")).strip()
        parsed_date: date | None = None
        if date_str:
            try:
                parsed_date = date.fromisoformat(date_str)
            except ValueError:
                parsed_date = None
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        return ReceiptInfo(
            vendor=vendor,
            amount_cents=amount,
            currency=currency,
            line_items=line_items,
            date=parsed_date,
            confidence=confidence,
        )
    except (AttributeError, TypeError, ValueError) as e:
        logger.warning("receipt extraction shape mismatch: {}", e)
        return _EMPTY
