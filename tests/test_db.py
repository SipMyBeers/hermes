from pathlib import Path
import sqlite3
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
    assert {"messages", "clients", "ledger", "outbox"}.issubset(tables)


def test_messages_uniqueness(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
        "VALUES (1, 'mid-1', '2026-05-02T00:00:00', 'a@b.com', 'promo', 'rule')"
    )
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
            "VALUES (2, 'mid-1', '2026-05-02T00:00:00', 'a@b.com', 'promo', 'rule')"
        )
