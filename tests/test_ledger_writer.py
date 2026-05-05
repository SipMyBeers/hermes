from datetime import date, datetime, timezone
from pathlib import Path
from hermes.db import connect, migrate
from hermes.extract.receipt import ReceiptInfo
from hermes.extract.ledger_writer import (
    maybe_write_receipt,
    LEDGER_CONFIDENCE_MIN,
)


def _db(tmp_path: Path):
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    return db


def _r(**overrides) -> ReceiptInfo:
    base = dict(
        vendor="Stripe",
        amount_cents=2900,
        currency="USD",
        line_items=("Pro plan",),
        date=date(2026, 3, 15),
        confidence=0.9,
    )
    base.update(overrides)
    return ReceiptInfo(**base)


def test_maybe_write_receipt_writes_high_confidence(tmp_path):
    db = _db(tmp_path)
    rid = maybe_write_receipt(db, _r(confidence=0.9), message_id="mid-1")
    assert isinstance(rid, int) and rid > 0
    row = db.execute(
        "SELECT direction, vendor, amount_cents, status, message_id, month "
        "FROM ledger WHERE id=?",
        (rid,),
    ).fetchone()
    assert row[0] == "in"
    assert row[1] == "Stripe"
    assert row[2] == 2900
    assert row[3] == "paid"
    assert row[4] == "mid-1"
    assert row[5] == "2026-03"


def test_maybe_write_receipt_skips_low_confidence(tmp_path):
    db = _db(tmp_path)
    # Threshold is the gate.
    just_below = max(0.0, LEDGER_CONFIDENCE_MIN - 0.01)
    rid = maybe_write_receipt(db, _r(confidence=just_below))
    assert rid is None
    rows = db.execute("SELECT COUNT(*) FROM ledger").fetchone()
    assert rows[0] == 0


def test_maybe_write_receipt_skips_zero_amount(tmp_path):
    db = _db(tmp_path)
    rid = maybe_write_receipt(db, _r(amount_cents=0))
    assert rid is None
    rows = db.execute("SELECT COUNT(*) FROM ledger").fetchone()
    assert rows[0] == 0


def test_maybe_write_receipt_skips_negative_amount(tmp_path):
    """Defensive: a negative amount is gibberish, not a refund."""
    db = _db(tmp_path)
    rid = maybe_write_receipt(db, _r(amount_cents=-100))
    assert rid is None


def test_maybe_write_receipt_skips_empty_vendor(tmp_path):
    db = _db(tmp_path)
    rid = maybe_write_receipt(db, _r(vendor=""))
    assert rid is None


def test_maybe_write_receipt_uses_receipt_date_for_month(tmp_path):
    db = _db(tmp_path)
    rid = maybe_write_receipt(db, _r(date=date(2026, 3, 15)))
    row = db.execute(
        "SELECT month, paid_at FROM ledger WHERE id=?", (rid,)
    ).fetchone()
    assert row[0] == "2026-03"
    assert row[1] == "2026-03-15"


def test_maybe_write_receipt_falls_back_to_today_when_no_date(tmp_path):
    db = _db(tmp_path)
    rid = maybe_write_receipt(db, _r(date=None))
    row = db.execute(
        "SELECT month, paid_at FROM ledger WHERE id=?", (rid,)
    ).fetchone()
    today = datetime.now(timezone.utc)
    assert row[0] == today.strftime("%Y-%m")
    assert row[1] == today.date().isoformat()


def test_maybe_write_receipt_returns_lastrowid(tmp_path):
    """Sanity: returned id is the one we just wrote, not stale state."""
    db = _db(tmp_path)
    a = maybe_write_receipt(db, _r(vendor="Stripe"))
    b = maybe_write_receipt(db, _r(vendor="Cloudflare"))
    assert a is not None and b is not None
    assert b == a + 1
