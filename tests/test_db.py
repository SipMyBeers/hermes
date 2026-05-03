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
