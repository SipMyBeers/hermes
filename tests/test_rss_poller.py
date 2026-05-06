"""Tests for the Phase M.2 RSS poller — feedparser-backed, idempotent stores."""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import patch

from hermes.db import connect, migrate
from hermes.rss.poller import (
    FeedItem,
    _entry_id,
    _published_iso,
    list_active_feeds,
    mark_polled,
    poll_feed,
    remove_feed,
    store_new_items,
    upsert_feed,
)


def test_feed_item_fields():
    item = FeedItem(
        feed_url="https://example.com/feed.xml",
        entry_id="entry-123",
        title="Hello",
        url="https://example.com/post/123",
        published_at="2026-05-02T10:00:00Z",
        summary="A short summary",
    )
    assert item.feed_url == "https://example.com/feed.xml"
    assert item.entry_id == "entry-123"
    assert item.summary == "A short summary"


def test_feed_item_summary_default_empty():
    item = FeedItem(
        feed_url="u", entry_id="e", title="t", url="u",
        published_at="2026-05-02",
    )
    assert item.summary == ""


def _fake_entry(*, id=None, link=None, title=None,
                published_parsed=None, updated_parsed=None):
    """feedparser entries are duck-typed objects — SimpleNamespace stands
    in for one. Only attrs we set are present; getattr defaults handle
    the rest, matching feedparser's behavior."""
    kwargs = {}
    if id is not None:
        kwargs["id"] = id
    if link is not None:
        kwargs["link"] = link
    if title is not None:
        kwargs["title"] = title
    if published_parsed is not None:
        kwargs["published_parsed"] = published_parsed
    if updated_parsed is not None:
        kwargs["updated_parsed"] = updated_parsed
    return SimpleNamespace(**kwargs)


def test_poll_feed_parses_rss():
    """Patch feedparser.parse and assert FeedItem fields are populated."""
    fake = SimpleNamespace(
        bozo=False,
        bozo_exception=None,
        entries=[
            _fake_entry(
                id="post-1",
                link="https://example.com/posts/1",
                title="First post",
                published_parsed=(2026, 5, 1, 12, 0, 0, 0, 0, 0),
            ),
            _fake_entry(
                id="post-2",
                link="https://example.com/posts/2",
                title="Second post",
                published_parsed=(2026, 5, 2, 9, 0, 0, 0, 0, 0),
            ),
        ],
    )
    with patch("feedparser.parse", return_value=fake):
        items = poll_feed("https://example.com/feed.xml")
    assert len(items) == 2
    assert items[0].entry_id == "post-1"
    assert items[0].url == "https://example.com/posts/1"
    assert items[0].title == "First post"
    assert items[0].feed_url == "https://example.com/feed.xml"
    assert items[0].published_at.startswith("2026-05-01T12:00:00")
    assert items[1].entry_id == "post-2"


def test_poll_feed_returns_empty_on_bozo_with_no_entries():
    """A truly malformed feed (bozo=True + entries=[]) returns [], not raises."""
    fake = SimpleNamespace(
        bozo=True,
        bozo_exception=Exception("not XML"),
        entries=[],
    )
    with patch("feedparser.parse", return_value=fake):
        items = poll_feed("https://broken.example/feed.xml")
    assert items == []


def test_poll_feed_keeps_entries_when_bozo_but_recoverable():
    """Many real feeds set bozo=True for minor XML quirks but still
    populate entries — we shouldn't drop them on the floor."""
    fake = SimpleNamespace(
        bozo=True,
        bozo_exception=Exception("undefined entity"),
        entries=[
            _fake_entry(id="x", link="https://x.example/1", title="X"),
        ],
    )
    with patch("feedparser.parse", return_value=fake):
        items = poll_feed("https://quirky.example/feed.xml")
    assert len(items) == 1
    assert items[0].entry_id == "x"


def test_entry_id_falls_back_to_link_then_title():
    """No .id → use .link; no .link either → use title (truncated)."""
    e_with_id = _fake_entry(id="real-id", link="https://x.com/1", title="t")
    assert _entry_id(e_with_id) == "real-id"

    e_link_only = _fake_entry(link="https://x.com/2", title="t")
    assert _entry_id(e_link_only) == "https://x.com/2"

    e_title_only = _fake_entry(title="just a title")
    assert _entry_id(e_title_only) == "just a title"

    e_blank = _fake_entry()
    assert _entry_id(e_blank) == "no-id"


def test_published_iso_handles_missing_timestamp():
    """A feedparser entry with no published_parsed gets a now-ish ISO."""
    e = _fake_entry(title="undated")
    iso = _published_iso(e)
    # ISO 8601 with timezone — sanity check, it parses back to a datetime.
    from datetime import datetime
    parsed = datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None


def test_published_iso_uses_updated_when_published_missing():
    """Atom feeds often only set updated_parsed; we still get a real
    timestamp out instead of falling back to now()."""
    e = _fake_entry(updated_parsed=(2026, 4, 1, 8, 30, 0, 0, 0, 0))
    iso = _published_iso(e)
    assert iso.startswith("2026-04-01T08:30:00")


def test_store_new_items_dedupes(tmp_path):
    """Calling twice with the same items returns [] the second time —
    UNIQUE(feed_url, entry_id) protects against duplicates."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    upsert_feed(db, "https://example.com/feed.xml", "Example")
    items = [
        FeedItem(
            feed_url="https://example.com/feed.xml",
            entry_id="post-1",
            title="t1",
            url="https://example.com/1",
            published_at="2026-05-02T00:00:00",
        ),
        FeedItem(
            feed_url="https://example.com/feed.xml",
            entry_id="post-2",
            title="t2",
            url="https://example.com/2",
            published_at="2026-05-02T00:00:00",
        ),
    ]
    first = store_new_items(db, items)
    assert len(first) == 2
    second = store_new_items(db, items)
    assert second == []


def test_upsert_feed_round_trips(tmp_path):
    """upsert_feed inserts then updates the name on conflict — both
    paths land in list_active_feeds correctly."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    upsert_feed(db, "https://hn.example/rss", "HN")
    upsert_feed(db, "https://lobsters.example/rss", "Lobsters")
    feeds = list_active_feeds(db)
    assert ("https://hn.example/rss", "HN") in feeds
    assert ("https://lobsters.example/rss", "Lobsters") in feeds

    # Upsert with new name updates in place.
    upsert_feed(db, "https://hn.example/rss", "Hacker News")
    feeds = dict(list_active_feeds(db))
    assert feeds["https://hn.example/rss"] == "Hacker News"


def test_upsert_feed_blank_name_falls_back_to_url(tmp_path):
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    upsert_feed(db, "https://nameless.example/rss")  # default name=""
    feeds = list_active_feeds(db)
    assert feeds == [("https://nameless.example/rss", "https://nameless.example/rss")]


def test_remove_feed_returns_false_for_missing(tmp_path):
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    assert remove_feed(db, "https://nope.example/rss") is False
    upsert_feed(db, "https://present.example/rss", "Present")
    assert remove_feed(db, "https://present.example/rss") is True
    assert remove_feed(db, "https://present.example/rss") is False


def test_mark_polled_sets_timestamp(tmp_path):
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    upsert_feed(db, "https://example.com/feed.xml", "Example")
    mark_polled(db, "https://example.com/feed.xml")
    row = db.execute(
        "SELECT last_polled_at FROM feeds WHERE url=?",
        ("https://example.com/feed.xml",),
    ).fetchone()
    assert row[0] is not None
    # ISO 8601 with timezone offset.
    from datetime import datetime
    parsed = datetime.fromisoformat(row[0])
    assert parsed.tzinfo is not None
