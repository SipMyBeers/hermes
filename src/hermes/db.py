from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 5

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

# Phase F: skeleton tables for receipt/sub/lead/renewal/bookmarks/rss features.
# All additive — no v1/v2 tables are altered.
_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS subscriptions (
  vendor TEXT PRIMARY KEY,
  cadence TEXT NOT NULL CHECK (cadence IN ('monthly','annual','irregular')),
  amount_cents INTEGER NOT NULL,
  monthly_estimate_cents INTEGER NOT NULL,
  occurrences INTEGER NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_charge_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','stale','cancelled'))
);

CREATE TABLE IF NOT EXISTS renewals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  vendor TEXT NOT NULL,
  resource TEXT NOT NULL,
  expires_at TEXT,
  auto_renew INTEGER NOT NULL DEFAULT 0,
  cost_cents INTEGER,
  source_message_id TEXT,
  notified_at TEXT,
  UNIQUE(vendor, resource)
);

CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid INTEGER NOT NULL,
  sender TEXT NOT NULL,
  sender_domain TEXT NOT NULL,
  reason TEXT,
  matches TEXT,
  detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookmarks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  title TEXT,
  notes TEXT,
  saved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feeds (
  url TEXT PRIMARY KEY,
  name TEXT,
  last_polled_at TEXT
);

CREATE TABLE IF NOT EXISTS feed_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feed_url TEXT NOT NULL REFERENCES feeds(url),
  entry_id TEXT NOT NULL,
  title TEXT,
  url TEXT,
  published_at TEXT,
  seen_at TEXT NOT NULL,
  summary TEXT,
  UNIQUE(feed_url, entry_id)
);

CREATE INDEX IF NOT EXISTS idx_feed_items_seen ON feed_items(seen_at);
CREATE INDEX IF NOT EXISTS idx_renewals_expires ON renewals(expires_at);
"""

# Phase G: URL ingestion. The user sends a URL to the Telegram bot; Hermes
# fetches, extracts, summarizes, and stores it here. Web URLs are real today;
# video URLs (Instagram reels, YouTube, TikTok, X) are stubbed until v1.3
# brings yt-dlp + whisper-cpp transcription. Additive only.
_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS digestions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('web','video','unknown')),
  title TEXT,
  content_text TEXT,
  summary TEXT,
  actions TEXT,                  -- JSON array
  tools TEXT,                    -- JSON array of {name, description}
  source_chat_id INTEGER,
  ingested_at TEXT NOT NULL,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_digestions_ingested ON digestions(ingested_at);
"""

# Phase J.2: cross-source open-action log. Email enrichment and URL ingest
# both extract action items today, but they live trapped inside their source
# rows. The actions table promotes those into a single queryable workflow
# surface — one place to see "what do I need to do today" with /actions,
# /done, /dismiss, /snooze. Source rows stay immutable; this is the mutable
# layer.
_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_kind TEXT NOT NULL CHECK (source_kind IN ('email','digestion','manual')),
  source_id INTEGER,                   -- messages.uid or digestions.id; NULL for manual
  text TEXT NOT NULL,
  importance TEXT NOT NULL DEFAULT 'normal'
    CHECK (importance IN ('urgent','normal','low')),
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open','done','dismissed','snoozed','stale')),
  due_at TEXT,                         -- ISO 8601, optional
  snoozed_until TEXT,                  -- ISO 8601, only set when status='snoozed'
  created_at TEXT NOT NULL,
  done_at TEXT,
  source_subject TEXT,                 -- denormalized snapshot for /actions display
  source_sender TEXT
);

CREATE INDEX IF NOT EXISTS idx_actions_status_importance
  ON actions(status, importance, created_at);
CREATE INDEX IF NOT EXISTS idx_actions_source
  ON actions(source_kind, source_id);
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
    if current < 1:
        # v0 -> v1: create everything (idempotent IF NOT EXISTS); the v1 schema
        # already includes raw_email so a fresh install picks it up here.
        conn.executescript(_SCHEMA_V1)
    if current < 2:
        # v1 -> v2: existing installs (e.g. ghengis) created the messages table
        # without raw_email; add it via ALTER. _has_column() guard so re-running
        # the migration on a partially-upgraded DB is a no-op.
        if not _has_column(conn, "messages", "raw_email"):
            conn.execute("ALTER TABLE messages ADD COLUMN raw_email BLOB")
    if current < 3:
        # v2 -> v3: provision Phase F skeleton tables (subs, renewals, leads,
        # bookmarks, feeds, feed_items). All IF NOT EXISTS / additive.
        conn.executescript(_SCHEMA_V3)
    if current < 4:
        # v3 -> v4: provision Phase G URL ingestion table (digestions).
        # Additive — no v1/v2/v3 tables touched.
        conn.executescript(_SCHEMA_V4)
    if current < 5:
        # v4 -> v5: provision Phase J.2 actions table for the cross-source
        # open-action log. Additive — source tables (messages, digestions)
        # are not touched.
        conn.executescript(_SCHEMA_V5)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
