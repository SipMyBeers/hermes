"""Save URLs for later, with a weekly Telegram digest.

v1.2 intake path: a Telegram /save <url> [notes...] command (commands.py
addition). v1.3: a tiny FastAPI endpoint + bookmarklet so any browser
page can be saved with one click."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3


@dataclass(frozen=True)
class Bookmark:
    id: int
    url: str
    title: str | None
    notes: str | None
    saved_at: str


def save_bookmark(
    db: sqlite3.Connection,
    url: str,
    title: str | None = None,
    notes: str | None = None,
) -> int:
    cur = db.execute(
        "INSERT INTO bookmarks(url, title, notes, saved_at) VALUES (?, ?, ?, ?)",
        (url, title, notes, datetime.now(timezone.utc).isoformat()),
    )
    return cur.lastrowid


def recent_bookmarks(db: sqlite3.Connection, days: int = 7) -> list[Bookmark]:
    rows = db.execute(
        f"SELECT id, url, title, notes, saved_at FROM bookmarks "
        f"WHERE saved_at > datetime('now', '-{int(days)} days') "
        f"ORDER BY saved_at DESC"
    ).fetchall()
    return [Bookmark(id=r[0], url=r[1], title=r[2], notes=r[3], saved_at=r[4]) for r in rows]


def weekly_digest_lines(db: sqlite3.Connection) -> list[str]:
    bms = recent_bookmarks(db, days=7)
    if not bms:
        return ["*Bookmarks (last 7 days):* none saved."]
    lines = [f"*Bookmarks (last 7 days, {len(bms)})*"]
    for b in bms:
        title = b.title or b.url
        suffix = f" — {b.notes}" if b.notes else ""
        lines.append(f"  • {title}{suffix}\n    {b.url}")
    return lines
