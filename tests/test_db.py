from pathlib import Path
import sqlite3
import pytest
from hermes.db import connect, migrate, SCHEMA_VERSION


def test_connect_creates_file(tmp_path: Path):
    db_path = tmp_path / "test.sqlite"
    conn = connect(db_path)
    assert db_path.exists()
    conn.close()


def test_migrate_idempotent(tmp_path: Path):
    db_path = tmp_path / "test.sqlite"
    conn = connect(db_path)
    migrate(conn)
    migrate(conn)  # second call must not raise
    cur = conn.execute("PRAGMA user_version")
    assert cur.fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_migrate_creates_tables(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"messages", "ledger", "outbox"}.issubset(tables)


def test_messages_uniqueness(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
        "VALUES (1, 'mid-1', '2026-05-02T00:00:00', 'a@b.com', 'promo', 'rule')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
            "VALUES (2, 'mid-1', '2026-05-02T00:00:00', 'a@b.com', 'promo', 'rule')"
        )


def test_busy_timeout_set(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    v = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert v == 5000
    conn.close()


def test_journal_mode_is_wal(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    v = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert v == "wal"
    conn.close()


def test_classification_check_rejects_garbage(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
            "VALUES (1, 'mid-1', '2026-05-02', 'a@b.com', 'garbage', 'rule')"
        )


def test_classifier_check_rejects_garbage(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
            "VALUES (1, 'mid-1', '2026-05-02', 'a@b.com', 'promo', 'gemma4')"
        )


def test_composite_index_exists(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='messages'"
    )}
    assert "idx_messages_class_received" in indexes


def test_migrate_v1_to_v2_adds_raw_email_column(tmp_path: Path):
    """Existing v1 DBs (e.g. ghengis) get raw_email added via ALTER, not a recreate."""
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    # Simulate a v1 DB: same shape as the deployed v1 schema but WITHOUT raw_email.
    conn.executescript("""
    CREATE TABLE messages (
      uid INTEGER PRIMARY KEY,
      message_id TEXT UNIQUE NOT NULL,
      received_at TEXT NOT NULL,
      sender TEXT NOT NULL,
      subject TEXT,
      classification TEXT NOT NULL CHECK (classification IN ('promo','client','personal','transactional','review','other','unknown')),
      classifier TEXT NOT NULL CHECK (classifier IN ('rule','llm','user')),
      confidence REAL,
      label TEXT,
      draft_text TEXT,
      drafted_at TEXT,
      notified_at TEXT
    );
    CREATE TABLE ledger (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      direction TEXT NOT NULL CHECK (direction IN ('out','in')),
      client TEXT, vendor TEXT, invoice_number TEXT,
      month TEXT NOT NULL, amount_cents INTEGER NOT NULL,
      status TEXT NOT NULL, pdf_path TEXT, message_id TEXT,
      sent_at TEXT, paid_at TEXT
    );
    CREATE TABLE outbox (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      kind TEXT NOT NULL CHECK (kind IN ('reply','invoice','adhoc')),
      related_uid INTEGER, client TEXT,
      to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
      attachments TEXT,
      status TEXT NOT NULL CHECK (status IN ('queued','sent','failed')),
      last_error TEXT, created_at TEXT NOT NULL, sent_at TEXT
    );
    """)
    conn.execute("PRAGMA user_version = 1")
    # Insert a sentinel row to confirm migration preserves data.
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
        "VALUES (1, 'mid-1', '2026-05-02T00:00:00', 'a@b.com', 'promo', 'rule')"
    )
    conn.close()

    # Run migrate — should add the raw_email column without nuking the row.
    conn2 = connect(db_path)
    migrate(conn2)

    cols = [r[1] for r in conn2.execute("PRAGMA table_info(messages)").fetchall()]
    assert "raw_email" in cols
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    # Pre-existing row survived
    surviving = conn2.execute("SELECT uid, raw_email FROM messages WHERE uid=1").fetchone()
    assert surviving == (1, None)
    conn2.close()


def test_migrate_v2_to_v3_adds_skeleton_tables(tmp_path: Path):
    """v2 -> v3 provisions the Phase F skeleton tables additively."""
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    # Start at v2 by running migrate then forcing user_version back.
    migrate(conn)
    conn.execute("PRAGMA user_version = 2")
    # Drop the v3 tables to simulate a fresh v2 DB without skeleton tables.
    for t in ("subscriptions", "renewals", "leads", "bookmarks", "feed_items", "feeds"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    # Now upgrade to v3.
    migrate(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"subscriptions", "renewals", "leads", "bookmarks", "feeds", "feed_items"}.issubset(tables)
    # migrate() always lands at SCHEMA_VERSION (v4 once Phase G shipped); the
    # v3 step ran along the way which is what this test guards.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_migrate_v3_to_v4_adds_digestions_table(tmp_path: Path):
    """v3 -> v4 provisions the Phase G digestions table additively."""
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    # Start at v3 by running migrate then forcing user_version back.
    migrate(conn)
    conn.execute("PRAGMA user_version = 3")
    conn.execute("DROP TABLE IF EXISTS digestions")
    # Now upgrade to v4.
    migrate(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "digestions" in tables
    # CHECK constraint sanity: only allowed kinds.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(digestions)").fetchall()]
    assert {"id", "url", "kind", "title", "content_text", "summary",
            "actions", "tools", "source_chat_id", "ingested_at", "error"}.issubset(set(cols))
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='digestions'"
    )}
    assert "idx_digestions_ingested" in indexes
    # migrate() always lands at SCHEMA_VERSION; the v4 step ran along the way.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_migrate_v4_to_v5_adds_actions_table(tmp_path: Path):
    """v4 -> v5 provisions the Phase J.2 actions table additively."""
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    # Start at v4 by running migrate then forcing user_version back.
    migrate(conn)
    conn.execute("PRAGMA user_version = 4")
    conn.execute("DROP TABLE IF EXISTS actions")
    # Now upgrade to v5.
    migrate(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "actions" in tables
    cols = [r[1] for r in conn.execute("PRAGMA table_info(actions)").fetchall()]
    assert {"id", "source_kind", "source_id", "text", "importance", "status",
            "due_at", "snoozed_until", "created_at", "done_at",
            "source_subject", "source_sender"}.issubset(set(cols))
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='actions'"
    )}
    assert "idx_actions_status_importance" in indexes
    assert "idx_actions_source" in indexes
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_migrate_v5_to_v6_adds_learned_promos_table(tmp_path: Path):
    """v5 -> v6 provisions the Phase L stage 2 learned_promos table additively."""
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    # Start at v5 by running migrate then forcing user_version back.
    migrate(conn)
    conn.execute("PRAGMA user_version = 5")
    conn.execute("DROP TABLE IF EXISTS learned_promos")
    # Now upgrade to v6.
    migrate(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "learned_promos" in tables
    cols = [r[1] for r in conn.execute("PRAGMA table_info(learned_promos)").fetchall()]
    assert {"sender_email", "sender_domain", "dismissed_count",
            "first_dismissed_at", "last_dismissed_at"}.issubset(set(cols))
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='learned_promos'"
    )}
    assert "idx_learned_promos_domain" in indexes
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_migrate_v6_to_v7_widens_source_kind_check(tmp_path: Path):
    """v6 -> v7 widens actions.source_kind CHECK to allow 'feed_item'.

    Build a v6-shaped DB with the original 3-kind CHECK and a pre-existing
    row, then run migrate(). The migration must (a) preserve the existing
    row, (b) accept new INSERTs with source_kind='feed_item', and
    (c) leave the original 'email' kind still valid.
    """
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    # Simulate a v6 DB: same schema as v5 but BEFORE the v7 widening — the
    # CHECK only knows about 'email','digestion','manual'.
    conn.executescript("""
    CREATE TABLE actions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_kind TEXT NOT NULL CHECK (source_kind IN ('email','digestion','manual')),
      source_id INTEGER,
      text TEXT NOT NULL,
      importance TEXT NOT NULL DEFAULT 'normal'
        CHECK (importance IN ('urgent','normal','low')),
      status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','done','dismissed','snoozed','stale')),
      due_at TEXT,
      snoozed_until TEXT,
      created_at TEXT NOT NULL,
      done_at TEXT,
      source_subject TEXT,
      source_sender TEXT
    );
    """)
    # Sentinel row: must survive migration.
    conn.execute(
        "INSERT INTO actions(source_kind, source_id, text, created_at) "
        "VALUES ('email', 42, 'reply to casey', '2026-05-02T00:00:00')"
    )
    conn.execute("PRAGMA user_version = 6")
    conn.close()

    # Pre-condition: feed_item is NOT yet accepted by the v6 CHECK.
    conn = connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO actions(source_kind, source_id, text, created_at) "
            "VALUES ('feed_item', 1, 'read article', '2026-05-02T01:00:00')"
        )
    conn.close()

    # Migrate to v7.
    conn2 = connect(db_path)
    migrate(conn2)

    # Sentinel survived.
    surviving = conn2.execute(
        "SELECT source_kind, text FROM actions WHERE source_id=42"
    ).fetchone()
    assert surviving == ("email", "reply to casey")

    # feed_item is now accepted.
    conn2.execute(
        "INSERT INTO actions(source_kind, source_id, text, created_at) "
        "VALUES ('feed_item', 1, 'read article', '2026-05-02T01:00:00')"
    )
    row = conn2.execute(
        "SELECT source_kind FROM actions WHERE source_kind='feed_item'"
    ).fetchone()
    assert row[0] == "feed_item"

    # Original kinds still accepted.
    conn2.execute(
        "INSERT INTO actions(source_kind, source_id, text, created_at) "
        "VALUES ('digestion', 9, 'skim', '2026-05-02T02:00:00')"
    )

    # Garbage source_kind still rejected by the new CHECK.
    with pytest.raises(sqlite3.IntegrityError):
        conn2.execute(
            "INSERT INTO actions(source_kind, source_id, text, created_at) "
            "VALUES ('garbage', 1, 'x', '2026-05-02')"
        )

    # Indexes preserved.
    indexes = {row[0] for row in conn2.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='actions'"
    )}
    assert "idx_actions_status_importance" in indexes
    assert "idx_actions_source" in indexes

    assert conn2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    # Re-running migrate is a no-op (idempotency).
    migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn2.close()


def test_migrate_v7_to_v8_adds_thread_columns(tmp_path: Path):
    """v7 -> v8 adds thread_key on messages, thread_key+thread_count on
    actions, and matching indexes — without touching existing rows."""
    db_path = tmp_path / "t.sqlite"
    conn = connect(db_path)
    # Build a v7 DB by running migrate then forcing user_version back, and
    # dropping the v8-introduced columns / indexes so we can re-add them.
    migrate(conn)
    # Sanity: v8 already ran via migrate(); simulate a v7 DB by recreating
    # messages and actions WITHOUT the v8 additions, then re-running migrate.
    conn.executescript("""
        DROP TABLE IF EXISTS messages;
        DROP TABLE IF EXISTS actions;
        CREATE TABLE messages (
          uid INTEGER PRIMARY KEY,
          message_id TEXT UNIQUE NOT NULL,
          received_at TEXT NOT NULL,
          sender TEXT NOT NULL,
          subject TEXT,
          classification TEXT NOT NULL CHECK (classification IN ('promo','client','personal','transactional','review','other','unknown')),
          classifier TEXT NOT NULL CHECK (classifier IN ('rule','llm','user')),
          confidence REAL,
          label TEXT,
          draft_text TEXT,
          drafted_at TEXT,
          notified_at TEXT,
          raw_email BLOB
        );
        CREATE TABLE actions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source_kind TEXT NOT NULL CHECK (source_kind IN ('email','digestion','manual','feed_item')),
          source_id INTEGER,
          text TEXT NOT NULL,
          importance TEXT NOT NULL DEFAULT 'normal'
            CHECK (importance IN ('urgent','normal','low')),
          status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open','done','dismissed','snoozed','stale')),
          due_at TEXT,
          snoozed_until TEXT,
          created_at TEXT NOT NULL,
          done_at TEXT,
          source_subject TEXT,
          source_sender TEXT
        );
    """)
    # Sentinel rows that must survive the migration with NULL/default new cols.
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
        "VALUES (1, 'mid-1', '2026-05-02T00:00:00', 'a@b.com', 'client', 'rule')"
    )
    conn.execute(
        "INSERT INTO actions(source_kind, source_id, text, created_at) "
        "VALUES ('email', 1, 'Reply to Casey', '2026-05-02T00:00:00')"
    )
    conn.execute("PRAGMA user_version = 7")

    # Pre-condition: thread_key / thread_count not present.
    msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    act_cols = {r[1] for r in conn.execute("PRAGMA table_info(actions)")}
    assert "thread_key" not in msg_cols
    assert "thread_key" not in act_cols
    assert "thread_count" not in act_cols

    # Migrate to v8.
    migrate(conn)

    msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    act_cols = {r[1] for r in conn.execute("PRAGMA table_info(actions)")}
    assert "thread_key" in msg_cols
    assert "thread_key" in act_cols
    assert "thread_count" in act_cols

    # Existing rows survived; new columns default to NULL / 1.
    surviving_msg = conn.execute(
        "SELECT thread_key FROM messages WHERE uid=1"
    ).fetchone()
    assert surviving_msg == (None,)
    surviving_act = conn.execute(
        "SELECT thread_key, thread_count FROM actions WHERE source_id=1"
    ).fetchone()
    assert surviving_act == (None, 1)

    # Indexes added.
    indexes = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    )}
    assert "idx_messages_thread" in indexes
    assert "idx_actions_thread" in indexes

    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    # Idempotency — re-running migrate is a no-op.
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_messages_can_store_raw_email_blob(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    blob = b"From: a@b.com\r\nSubject: hi\r\n\r\nbody"
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier, raw_email) "
        "VALUES (1, 'mid-1', '2026-05-02T00:00:00', 'a@b.com', 'client', 'rule', ?)",
        (blob,),
    )
    row = conn.execute("SELECT raw_email FROM messages WHERE uid=1").fetchone()
    assert row[0] == blob


def test_migrate_v9_creates_heartbeats(tmp_path):
    from hermes.db import connect, migrate
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    conn.execute(
        "INSERT INTO heartbeats (component, ts) VALUES ('hermes-mail', '2026-06-11T00:00:00+00:00') "
        "ON CONFLICT(component) DO UPDATE SET ts=excluded.ts"
    )
    row = conn.execute("SELECT ts FROM heartbeats WHERE component='hermes-mail'").fetchone()
    assert row[0].startswith("2026-06-11")


def test_beat_upserts(tmp_path):
    from hermes.db import connect, migrate, beat
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    beat(conn, "hermes-mail")
    first = conn.execute("SELECT ts FROM heartbeats WHERE component='hermes-mail'").fetchone()[0]
    beat(conn, "hermes-mail")
    rows = conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
    second = conn.execute("SELECT ts FROM heartbeats WHERE component='hermes-mail'").fetchone()[0]
    assert rows == 1
    assert second >= first
