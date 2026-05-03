"""Poll configured RSS feeds, store new entries, summarize daily.

v1.2 dependency: feedparser. Add to pyproject.toml when implementing.

v1.2 schema: feeds(url PK, name, last_polled_at) and feed_items
(id, feed_url, entry_id UNIQUE per feed, title, url, published_at, seen_at,
summary). Already provisioned in db.py schema_v3."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class FeedItem:
    feed_url: str
    entry_id: str
    title: str
    url: str
    published_at: str
    summary: str = ""


def poll_feed(feed_url: str, timeout: float = 30.0) -> list[FeedItem]:
    raise NotImplementedError("v1.2: feedparser; add 'feedparser' to pyproject.toml")


def daily_digest_lines(db, days: int = 1) -> list[str]:
    raise NotImplementedError("v1.2: SELECT recent feed_items + LLM summary")
