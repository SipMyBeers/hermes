from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

# Note: databases migrated from a v0/initial install may still have the old
# `idx_messages_received` single-column index hanging around alongside the
# new composite `idx_messages_class_received`. It's harmless — SQLite ignores
# unused indexes — and we don't bother dropping it on migrate.
_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS messages (
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

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  direction TEXT NOT NULL CHECK (direction IN ('out','in')),
  client TEXT,
  vendor TEXT,
  invoice_number TEXT,
  month TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  status TEXT NOT NULL,
  pdf_path TEXT,
  message_id TEXT,
  sent_at TEXT,
  paid_at TEXT
);

CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('reply','invoice','adhoc')),
  related_uid INTEGER,
  client TEXT,
  to_addr TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  attachments TEXT,
  status TEXT NOT NULL CHECK (status IN ('queued','sent','failed')),
  last_error TEXT,
  created_at TEXT NOT NULL,
  sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_class_received ON messages(classification, received_at);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);
CREATE INDEX IF NOT EXISTS idx_ledger_month ON ledger(month, direction);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")  # ms — concurrent writer waits
    return conn


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    # v0 -> v1: create everything (idempotent IF NOT EXISTS); the v1 schema
    # already includes raw_email so a fresh install lands at v2 in one step.
    conn.executescript(_SCHEMA_V1)
    # v1 -> v2: existing installs (e.g. ghengis) created the messages table
    # without raw_email; add it via ALTER. Use _has_column() so re-running the
    # migration on a partially-upgraded DB is a no-op.
    if current < 2 and not _has_column(conn, "messages", "raw_email"):
        conn.execute("ALTER TABLE messages ADD COLUMN raw_email BLOB")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
