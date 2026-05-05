from pathlib import Path
from hermes.db import connect, migrate
from hermes.classifier.learned import (
    record_dismiss,
    is_learned_promo,
    PROMO_THRESHOLD,
)


def _db(tmp_path: Path):
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    return db


def test_record_dismiss_creates_row(tmp_path):
    db = _db(tmp_path)
    n = record_dismiss(db, "casey@x.com")
    assert n == 1
    row = db.execute(
        "SELECT sender_email, sender_domain, dismissed_count "
        "FROM learned_promos"
    ).fetchone()
    assert row == ("casey@x.com", "x.com", 1)


def test_record_dismiss_increments(tmp_path):
    db = _db(tmp_path)
    record_dismiss(db, "casey@x.com")
    n = record_dismiss(db, "casey@x.com")
    assert n == 2
    row = db.execute(
        "SELECT dismissed_count FROM learned_promos WHERE sender_email=?",
        ("casey@x.com",),
    ).fetchone()
    assert row[0] == 2


def test_record_dismiss_handles_full_from_header(tmp_path):
    db = _db(tmp_path)
    n = record_dismiss(db, "Casey <casey@x.com>")
    assert n == 1
    row = db.execute(
        "SELECT sender_email FROM learned_promos"
    ).fetchone()
    assert row[0] == "casey@x.com"


def test_record_dismiss_lowercases(tmp_path):
    """Address comparison must be case-insensitive."""
    db = _db(tmp_path)
    record_dismiss(db, "Casey@X.COM")
    n = record_dismiss(db, "casey@x.com")
    # Same logical address — second call increments instead of inserting.
    assert n == 2
    rows = db.execute("SELECT COUNT(*) FROM learned_promos").fetchone()
    assert rows[0] == 1


def test_record_dismiss_ignores_unparseable(tmp_path):
    db = _db(tmp_path)
    n = record_dismiss(db, "")
    assert n == 0
    n2 = record_dismiss(db, "not-an-email")
    assert n2 == 0
    rows = db.execute("SELECT COUNT(*) FROM learned_promos").fetchone()
    assert rows[0] == 0


def test_is_learned_promo_threshold(tmp_path):
    db = _db(tmp_path)
    # PROMO_THRESHOLD is the gate; one dismiss should not yet promote.
    record_dismiss(db, "casey@x.com")
    if PROMO_THRESHOLD > 1:
        assert is_learned_promo(db, "casey@x.com") is False
    # Reach the threshold.
    for _ in range(PROMO_THRESHOLD - 1):
        record_dismiss(db, "casey@x.com")
    assert is_learned_promo(db, "casey@x.com") is True


def test_is_learned_promo_unknown_sender(tmp_path):
    db = _db(tmp_path)
    assert is_learned_promo(db, "stranger@y.com") is False


def test_is_learned_promo_handles_full_from_header(tmp_path):
    db = _db(tmp_path)
    for _ in range(PROMO_THRESHOLD):
        record_dismiss(db, "casey@x.com")
    # Lookup with a full From header should resolve to the same address.
    assert is_learned_promo(db, "Casey <casey@x.com>") is True


def test_is_learned_promo_ignores_unparseable(tmp_path):
    db = _db(tmp_path)
    assert is_learned_promo(db, "") is False
    assert is_learned_promo(db, "no-at-here") is False
