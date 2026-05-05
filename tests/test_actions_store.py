from datetime import datetime, timedelta, timezone
from hermes.db import connect, migrate
from hermes.actions.store import (
    add_actions, list_open, count_open, mark_done, dismiss, snooze,
    expire_snoozed, decay_stale, parse_when,
)


def _db(tmp_path):
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    return db


def test_add_actions_inserts_rows(tmp_path):
    db = _db(tmp_path)
    ids = add_actions(
        db,
        source_kind="email",
        source_id=42,
        texts=["Send the invoice", "Reply to Casey"],
        importance="normal",
        source_subject="Re: invoice",
        source_sender="casey@x.com",
    )
    assert len(ids) == 2
    assert all(isinstance(i, int) for i in ids)
    rows = list(db.execute("SELECT text, status, source_kind, source_id FROM actions"))
    assert len(rows) == 2
    assert rows[0] == ("Send the invoice", "open", "email", 42)


def test_add_actions_skips_empty_text(tmp_path):
    db = _db(tmp_path)
    ids = add_actions(
        db,
        source_kind="email",
        source_id=1,
        texts=["", "  ", "real action", "\t"],
    )
    assert len(ids) == 1
    rows = list(db.execute("SELECT text FROM actions"))
    assert rows == [("real action",)]


def test_list_open_orders_by_importance(tmp_path):
    db = _db(tmp_path)
    add_actions(db, source_kind="email", source_id=1, texts=["low one"], importance="low")
    add_actions(db, source_kind="email", source_id=1, texts=["normal one"], importance="normal")
    add_actions(db, source_kind="email", source_id=1, texts=["urgent one"], importance="urgent")
    rows = list_open(db)
    texts = [r.text for r in rows]
    assert texts == ["urgent one", "normal one", "low one"]


def test_list_open_excludes_done_dismissed_snoozed(tmp_path):
    db = _db(tmp_path)
    ids = add_actions(db, source_kind="email", source_id=1,
                     texts=["a", "b", "c", "d"])
    mark_done(db, ids[0])
    dismiss(db, ids[1])
    snooze(db, ids[2], datetime.now(timezone.utc) + timedelta(days=2))
    rows = list_open(db)
    assert [r.text for r in rows] == ["d"]


def test_mark_done_idempotent(tmp_path):
    db = _db(tmp_path)
    ids = add_actions(db, source_kind="email", source_id=1, texts=["x"])
    assert mark_done(db, ids[0]) is True
    # Second call returns False — already-done can't be re-closed.
    assert mark_done(db, ids[0]) is False


def test_dismiss_only_affects_active(tmp_path):
    db = _db(tmp_path)
    ids = add_actions(db, source_kind="email", source_id=1, texts=["x"])
    mark_done(db, ids[0])
    # Dismissing a done row is a no-op.
    assert dismiss(db, ids[0]) is False
    row = db.execute("SELECT status FROM actions WHERE id=?", (ids[0],)).fetchone()
    assert row[0] == "done"


def test_snooze_sets_status_and_until(tmp_path):
    db = _db(tmp_path)
    ids = add_actions(db, source_kind="email", source_id=1, texts=["x"])
    when = datetime.now(timezone.utc) + timedelta(days=3)
    assert snooze(db, ids[0], when) is True
    row = db.execute("SELECT status, snoozed_until FROM actions WHERE id=?",
                     (ids[0],)).fetchone()
    assert row[0] == "snoozed"
    assert row[1] is not None
    # Should be parseable ISO 8601 in UTC
    parsed = datetime.fromisoformat(row[1])
    assert parsed.tzinfo is not None


def test_expire_snoozed_reopens_past_due(tmp_path):
    db = _db(tmp_path)
    ids = add_actions(db, source_kind="email", source_id=1, texts=["a", "b"])
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    future = datetime.now(timezone.utc) + timedelta(days=2)
    snooze(db, ids[0], past)
    snooze(db, ids[1], future)
    reopened = expire_snoozed(db)
    assert reopened == 1
    rows = dict(db.execute("SELECT id, status FROM actions").fetchall())
    assert rows[ids[0]] == "open"
    assert rows[ids[1]] == "snoozed"


def test_decay_stale_marks_old_open_rows(tmp_path):
    db = _db(tmp_path)
    # Insert an "old" action manually so we can backdate created_at.
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
    db.execute(
        "INSERT INTO actions(source_kind, source_id, text, importance, status, created_at) "
        "VALUES ('email', 1, 'old', 'normal', 'open', ?)", (old,),
    )
    db.execute(
        "INSERT INTO actions(source_kind, source_id, text, importance, status, created_at) "
        "VALUES ('email', 1, 'recent', 'normal', 'open', ?)", (recent,),
    )
    moved = decay_stale(db, days=7)
    assert moved == 1
    rows = dict(db.execute("SELECT text, status FROM actions").fetchall())
    assert rows["old"] == "stale"
    assert rows["recent"] == "open"


def test_decay_stale_does_not_touch_done_or_dismissed(tmp_path):
    db = _db(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    db.execute(
        "INSERT INTO actions(source_kind, source_id, text, importance, status, created_at, done_at) "
        "VALUES ('email', 1, 'old-done', 'normal', 'done', ?, ?)", (old, old),
    )
    db.execute(
        "INSERT INTO actions(source_kind, source_id, text, importance, status, created_at) "
        "VALUES ('email', 1, 'old-dismissed', 'normal', 'dismissed', ?)", (old,),
    )
    moved = decay_stale(db, days=7)
    assert moved == 0


def test_parse_when_relative():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)
    assert parse_when("+3d", now=now) == now + timedelta(days=3)
    assert parse_when("3d", now=now) == now + timedelta(days=3)
    assert parse_when("2h", now=now) == now + timedelta(hours=2)
    assert parse_when("+1d", now=now) == now + timedelta(days=1)


def test_parse_when_keywords():
    now = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)
    assert parse_when("tomorrow", now=now) == now + timedelta(days=1)
    assert parse_when("today", now=now) == now + timedelta(hours=4)
    assert parse_when("next week", now=now) == now + timedelta(days=7)
    assert parse_when("nextweek", now=now) == now + timedelta(days=7)


def test_parse_when_weekday():
    # Saturday 2026-05-02 -> Monday is +2 days
    now = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)
    assert now.weekday() == 5  # sanity: Saturday
    assert parse_when("monday", now=now) == now + timedelta(days=2)
    # Same day asked for => next week, not today
    monday = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    assert monday.weekday() == 0
    assert parse_when("monday", now=monday) == monday + timedelta(days=7)
    # weekday + qualifier ("monday morning") still resolves to the day
    assert parse_when("monday morning", now=now) == now + timedelta(days=2)


def test_parse_when_unparseable():
    assert parse_when("fubar") is None
    assert parse_when("") is None
    assert parse_when("   ") is None


def test_count_open(tmp_path):
    db = _db(tmp_path)
    assert count_open(db) == 0
    ids = add_actions(db, source_kind="email", source_id=1, texts=["a", "b", "c"])
    assert count_open(db) == 3
    mark_done(db, ids[0])
    assert count_open(db) == 2
