from datetime import datetime, timezone

from hermes.db import connect, migrate
from hermes.digest.weekly import build_weekly_recap


def _db(tmp_path):
    db = connect(tmp_path / "weekly.sqlite")
    migrate(db)
    return db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_weekly_recap_quiet_week_returns_friendly_line(tmp_path):
    db = _db(tmp_path)
    text = build_weekly_recap(db)
    assert "Quiet week" in text
    # Header always renders.
    assert "Hermes — week of" in text


def test_weekly_recap_counts_action_states(tmp_path):
    db = _db(tmp_path)
    now = _now()
    # 3 done in the last 7 days.
    for i in range(3):
        db.execute(
            "INSERT INTO actions (source_kind, source_id, text, status, created_at, done_at) "
            "VALUES ('email', ?, ?, 'done', ?, ?)",
            (i, f"closed-{i}", now, now),
        )
    # 1 dismissed in the last 7 days.
    db.execute(
        "INSERT INTO actions (source_kind, source_id, text, status, created_at) "
        "VALUES ('email', 99, 'dismissed-1', 'dismissed', ?)",
        (now,),
    )
    text = build_weekly_recap(db)
    assert "Closed: 3" in text
    assert "Dismissed: 1" in text


def test_weekly_recap_includes_mail_class_counts(tmp_path):
    db = _db(tmp_path)
    now = _now()
    rows = (
        [("promo", i) for i in range(4)]
        + [("client", 100)]
    )
    for cls, uid in rows:
        db.execute(
            "INSERT INTO messages "
            "(uid, message_id, received_at, sender, subject, classification, classifier, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, 'rule', 1.0)",
            (uid, f"<m{uid}@x>", now, "x@y.com", "subj", cls),
        )
    text = build_weekly_recap(db)
    assert "promo: 4" in text
    assert "client: 1" in text


def test_weekly_recap_money_formatting(tmp_path):
    db = _db(tmp_path)
    now = _now()
    db.execute(
        "INSERT INTO ledger (direction, month, amount_cents, status, sent_at) "
        "VALUES ('out', '2026-05', 15000, 'sent', ?)",
        (now,),
    )
    db.execute(
        "INSERT INTO ledger (direction, month, amount_cents, status, paid_at) "
        "VALUES ('in', '2026-05', 2900, 'paid', ?)",
        (now,),
    )
    text = build_weekly_recap(db)
    assert "$150.00" in text
    assert "$29.00" in text


def test_weekly_recap_skips_zero_buckets(tmp_path):
    db = _db(tmp_path)
    # Insert a single closed action so the recap is not "Quiet week".
    now = _now()
    db.execute(
        "INSERT INTO actions (source_kind, source_id, text, status, created_at, done_at) "
        "VALUES ('email', 1, 'x', 'done', ?, ?)",
        (now, now),
    )
    text = build_weekly_recap(db)
    # No bookmarks, no URLs → no "Saved" section.
    assert "Bookmarks" not in text
    assert "URLs ingested" not in text
    # No ledger activity → no "Ledger" section.
    assert "Ledger" not in text
    # No mail in the window → no "Mail" section.
    assert "*Mail*" not in text


def test_weekly_recap_learned_promos_line(tmp_path):
    db = _db(tmp_path)
    now = _now()
    for i, addr in enumerate(("a@x.com", "b@y.com")):
        db.execute(
            "INSERT INTO learned_promos "
            "(sender_email, sender_domain, dismissed_count, first_dismissed_at, last_dismissed_at) "
            "VALUES (?, ?, 3, ?, ?)",
            (addr, addr.split("@", 1)[1], now, now),
        )
    text = build_weekly_recap(db)
    assert "2 sender(s)" in text
