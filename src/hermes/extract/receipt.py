"""Extract structured fields from a transactional 'receipt' email via gemma4.

Future: hook from hermes.mail.daemon when classification == 'transactional'
and confidence > threshold; write the extracted ReceiptInfo to the
ledger table with direction='in'."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from email.message import Message


@dataclass(frozen=True)
class ReceiptInfo:
    vendor: str                       # e.g. "Stripe", "Cloudflare", "Anthropic"
    amount_cents: int                 # 0 if not found
    currency: str                     # "USD" default
    line_items: tuple[str, ...]       # raw item descriptions if extracted
    date: date | None                 # the charge date
    confidence: float                 # 0.0 if extraction failed


def extract_receipt(
    msg: Message,
    model: str = "gemma4",
    host: str = "http://localhost:11434",
    timeout: float = 60.0,
) -> ReceiptInfo:
    """Run gemma4 over the receipt email and return structured fields.

    v1.2 implementation:
      - Use a JSON-format Ollama prompt similar to digest.enrich.summarize_for_brief.
      - Prompt asks for vendor, amount in cents, currency, date, line items, confidence.
      - Return ReceiptInfo(confidence=0.0) on any error so callers can degrade.
    """
    raise NotImplementedError("v1.2: gemma4 JSON-format extraction (see docstring)")
