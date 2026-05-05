"""Persist a successful receipt extraction to the ledger as direction='in'.

Called by the mail daemon after extract_receipt() runs over a transactional
email. Confidence gate avoids polluting the tax-ready ledger with garbage
LLM output; vendor/amount sanity checks defend against partial extractions.
"""
from __future__ import annotations
from datetime import datetime, timezone
import sqlite3
from loguru import logger
from hermes.extract.receipt import ReceiptInfo

# Threshold below which we don't trust the extraction enough to ledger it.
LEDGER_CONFIDENCE_MIN = 0.5


def maybe_write_receipt(
    db: sqlite3.Connection,
    receipt: ReceiptInfo,
    message_id: str | None = None,
) -> int | None:
    """Insert a ledger row if the receipt extraction is confident enough.

    Returns the new row id or None when skipped. Skip reasons:
      - confidence below LEDGER_CONFIDENCE_MIN
      - amount_cents <= 0 (extractor couldn't find a total)
      - vendor empty (can't tag the line in the EOY CSV)

    Note: there is no UNIQUE on ledger.message_id today, so a re-process of
    the same email would double-write. The daemon's INSERT OR IGNORE on
    messages prevents that in practice — but a manual reprocess (e.g. after
    a schema change) would need explicit dedupe. Flagged in the L2 review.
    """
    if receipt.confidence < LEDGER_CONFIDENCE_MIN:
        return None
    if receipt.amount_cents <= 0:
        return None
    if not receipt.vendor:
        return None
    month = (
        receipt.date.strftime("%Y-%m")
        if receipt.date
        else datetime.now(timezone.utc).strftime("%Y-%m")
    )
    paid_at = (
        receipt.date.isoformat()
        if receipt.date
        else datetime.now(timezone.utc).date().isoformat()
    )
    cur = db.execute(
        "INSERT INTO ledger(direction, vendor, month, amount_cents, status, "
        "message_id, paid_at) "
        "VALUES ('in', ?, ?, ?, 'paid', ?, ?)",
        (
            receipt.vendor,
            month,
            receipt.amount_cents,
            message_id,
            paid_at,
        ),
    )
    logger.info(
        "ledger.in: {} {} cents from {} (confidence={:.2f})",
        receipt.amount_cents, receipt.currency, receipt.vendor, receipt.confidence,
    )
    return cur.lastrowid
