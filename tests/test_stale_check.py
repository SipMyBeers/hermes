from pathlib import Path
import pytest
from hermes.db import connect, migrate
from hermes.stale.check import StaleThread, find_stale_threads


def test_stale_thread_fields():
    s = StaleThread(uid=1, sender="sam@boarhog.example", subject="Hi",
                    received_at="2026-04-01T00:00:00", days_stale=31)
    assert s.uid == 1
    assert s.days_stale == 31


def test_find_stale_threads_returns_only_unanswered_old_client_mail(tmp_path: Path):
    """Three messages: stale-without-reply, stale-with-reply, fresh-without-reply.
    Only the first should come back."""
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)

    # 1. Stale (10 days old), client, no reply -> should appear
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier) "
        "VALUES (1, 'mid-stale-noreply', datetime('now', '-10 days'), 'sam@boarhog.example', "
        "'Status update?', 'client', 'rule')"
    )
    # 2. Stale, client, but with a sent reply -> should NOT appear
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier) "
        "VALUES (2, 'mid-stale-replied', datetime('now', '-10 days'), 'casey@otherclient.example', "
        "'Quote?', 'client', 'rule')"
    )
    conn.execute(
        "INSERT INTO outbox(kind, related_uid, to_addr, subject, body, status, created_at, sent_at) "
        "VALUES ('reply', 2, 'casey@otherclient.example', 'Re: Quote?', 'Sent.', "
        "'sent', datetime('now', '-9 days'), datetime('now', '-9 days'))"
    )
    # 3. Fresh (1 day old), client, no reply -> should NOT appear
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier) "
        "VALUES (3, 'mid-fresh', datetime('now', '-1 days'), 'fresh@client.example', "
        "'Hello', 'client', 'rule')"
    )

    results = find_stale_threads(conn, days=5)
    assert len(results) == 1
    assert results[0].uid == 1
    assert results[0].sender == "sam@boarhog.example"
    assert results[0].days_stale >= 9  # cast-to-int can land at 9 or 10


def test_find_stale_threads_ignores_non_client_classifications(tmp_path: Path):
    """A 10-day-old promo with no reply must not be flagged."""
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier) "
        "VALUES (1, 'mid-promo', datetime('now', '-10 days'), 'spam@promo.example', "
        "'50% off!', 'promo', 'rule')"
    )
    assert find_stale_threads(conn, days=5) == []


def test_failed_outbox_does_not_count_as_reply(tmp_path: Path):
    """A failed reply attempt must not satisfy the 'has been replied to' check."""
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier) "
        "VALUES (1, 'mid', datetime('now', '-10 days'), 'sam@boarhog.example', "
        "'Status?', 'client', 'rule')"
    )
    conn.execute(
        "INSERT INTO outbox(kind, related_uid, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 1, 'sam@boarhog.example', 'Re: Status?', 'tried', "
        "'failed', datetime('now', '-9 days'))"
    )
    results = find_stale_threads(conn, days=5)
    assert len(results) == 1
    assert results[0].uid == 1


def test_find_stale_pipeline_waiting_on_reply(tmp_path):
    """Rows in lead/replied/quoted where we wrote last and N+ days passed."""
    from datetime import datetime, timedelta, timezone
    from hermes.db import connect, migrate
    from hermes.pipeline.store import upsert_lead, set_stage
    from hermes.stale.check import find_stale_pipeline

    db = connect(tmp_path / "t.sqlite"); migrate(db)
    old = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()

    # stale: replied 6 days ago, no inbound since
    upsert_lead(db, "slow@x.com", display_name="Slow", source="direct")
    db.execute("UPDATE pipeline SET stage='replied', last_outbound=? WHERE contact_email='slow@x.com'", (old,))
    # not stale: they answered after our last outbound
    upsert_lead(db, "chatty@x.com", display_name="Chatty", source="direct")
    db.execute("UPDATE pipeline SET stage='replied', last_outbound=?, last_inbound=? WHERE contact_email='chatty@x.com'", (old, fresh))
    # not stale: outbound recent
    upsert_lead(db, "recent@x.com", display_name="Recent", source="direct")
    db.execute("UPDATE pipeline SET stage='quoted', last_outbound=? WHERE contact_email='recent@x.com'", (fresh,))
    # not stale: active stage is out of scope
    upsert_lead(db, "won@x.com", display_name="Won", source="direct")
    set_stage(db, "won@x.com", "active")
    db.execute("UPDATE pipeline SET last_outbound=? WHERE contact_email='won@x.com'", (old,))
    db.commit()

    rows = find_stale_pipeline(db, days=4)
    assert [r.contact_email for r in rows] == ["slow@x.com"]
    assert rows[0].stage == "replied"
    assert rows[0].days_waiting >= 5
