"""Periodic content rounds: poll RSS feeds, surface new entries.

Workflow per feed:
1. feedparser.parse(feed_url) — handles RSS 2.0, Atom 1.0, malformed XML.
2. For each entry not in feed_items (UNIQUE(feed_url, entry_id) protects):
     a. INSERT into feed_items with title, url, published_at.
     b. Caller (rounds/job.py) fetches the full page via webclaw and
        summarizes via gemma4.
3. Update feeds.last_polled_at.

Errors per feed are isolated — a 503 on one feed doesn't kill the round.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from loguru import logger


@dataclass(frozen=True)
class FeedItem:
    feed_url: str
    entry_id: str
    title: str
    url: str
    published_at: str    # ISO 8601, best-effort
    summary: str = ""    # filled in by the rounds job, not the poller


def _entry_id(entry) -> str:
    """feedparser entries don't all have .id — fall back to URL or guid hash."""
    if getattr(entry, "id", None):
        return str(entry.id)
    if getattr(entry, "link", None):
        return str(entry.link)
    return str(getattr(entry, "title", "") or "no-id")[:200]


def _published_iso(entry) -> str:
    """Best-effort ISO 8601 from feedparser's parsed_time tuple."""
    pt = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if pt:
        try:
            return datetime(*pt[:6], tzinfo=timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).isoformat()


def poll_feed(feed_url: str, timeout: float = 30.0) -> list[FeedItem]:
    """Fetch and parse a single feed. Returns the entries; caller dedupes
    against feed_items via the schema's UNIQUE constraint.

    On a parser error with no recovered entries (bozo=True, entries=[])
    we log and return [] so the round skips the feed cleanly. bozo=True
    with non-empty entries still returns the entries — feedparser flags
    minor XML issues as bozo even on otherwise-readable feeds.
    """
    import feedparser
    parsed = feedparser.parse(feed_url, request_headers={
        "User-Agent": "Mozilla/5.0 (Hermes/0.1)",
    })
    if parsed.bozo and not parsed.entries:
        logger.warning(
            "feedparser bozo on {}: {}", feed_url, parsed.bozo_exception
        )
        return []
    items: list[FeedItem] = []
    for entry in parsed.entries:
        items.append(FeedItem(
            feed_url=feed_url,
            entry_id=_entry_id(entry),
            title=str(getattr(entry, "title", "")).strip(),
            url=str(getattr(entry, "link", "")).strip(),
            published_at=_published_iso(entry),
        ))
    return items


def store_new_items(
    db: sqlite3.Connection, items: list[FeedItem]
) -> list[int]:
    """INSERT OR IGNORE; returns the rowids of items that were actually new."""
    now = datetime.now(timezone.utc).isoformat()
    new_ids: list[int] = []
    for it in items:
        cur = db.execute(
            "INSERT OR IGNORE INTO feed_items"
            "(feed_url, entry_id, title, url, published_at, seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (it.feed_url, it.entry_id, it.title, it.url, it.published_at, now),
        )
        if cur.rowcount > 0:
            new_ids.append(cur.lastrowid)
    return new_ids


def list_active_feeds(db: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return (url, name) for every configured feed."""
    rows = db.execute("SELECT url, name FROM feeds ORDER BY url").fetchall()
    return [(r[0], r[1] or r[0]) for r in rows]


def upsert_feed(db: sqlite3.Connection, url: str, name: str = "") -> None:
    db.execute(
        "INSERT INTO feeds(url, name) VALUES (?, ?) "
        "ON CONFLICT(url) DO UPDATE SET name = excluded.name",
        (url, name),
    )


def remove_feed(db: sqlite3.Connection, url: str) -> bool:
    res = db.execute("DELETE FROM feeds WHERE url=?", (url,))
    return res.rowcount > 0


def mark_polled(db: sqlite3.Connection, feed_url: str) -> None:
    db.execute(
        "UPDATE feeds SET last_polled_at=? WHERE url=?",
        (datetime.now(timezone.utc).isoformat(), feed_url),
    )
