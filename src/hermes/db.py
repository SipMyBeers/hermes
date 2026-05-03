from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS messages (
  uid INTEGER PRIMARY KEY,
  message_id TEXT UNIQUE NOT NULL,
  received_at TEXT NOT NULL,
  sender TEXT NOT NULL,
  subject TEXT,
  classification TEXT NOT NULL,
  classifier TEXT NOT NULL,
  confidence REAL,
  label TEXT,
  draft_text TEXT,
  drafted_at TEXT,
  notified_at TEXT
);

CREATE TABLE IF NOT EXISTS clients (
  name TEXT PRIMARY KEY,
  domain TEXT,
  contact_email TEXT NOT NULL,
  billing_amount_cents INTEGER,
  billing_terms TEXT,
  service_description TEXT,
  active INTEGER NOT NULL DEFAULT 1
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

CREATE INDEX IF NOT EXISTS idx_messages_received ON messages(received_at);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);
CREATE INDEX IF NOT EXISTS idx_ledger_month ON ledger(month, direction);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    conn.executescript(_SCHEMA_V1)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
