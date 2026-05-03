from pathlib import Path
import pytest
from hermes.db import connect, migrate
from hermes.bookmarks.store import (
    Bookmark,
    save_bookmark,
    recent_bookmarks,
    weekly_digest_lines,
)


def test_bookmark_fields():
    b = Bookmark(id=1, url="https://example.com", title="Example",
                 notes="cool tool", saved_at="2026-05-02T10:00:00+00:00")
    assert b.url == "https://example.com"
    assert b.title == "Example"


def test_save_and_recent_round_trip(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    bid = save_bookmark(conn, "https://anthropic.com", title="Anthropic", notes="hq")
    assert isinstance(bid, int) and bid >= 1

    rows = recent_bookmarks(conn, days=7)
    assert len(rows) == 1
    assert rows[0].url == "https://anthropic.com"
    assert rows[0].title == "Anthropic"
    assert rows[0].notes == "hq"


def test_weekly_digest_with_bookmarks(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    save_bookmark(conn, "https://a.example", title="A")
    save_bookmark(conn, "https://b.example", title="B", notes="see chapter 3")
    save_bookmark(conn, "https://c.example")  # no title or notes

    lines = weekly_digest_lines(conn)
    # Header + one entry per bookmark = 4 lines (each entry is a single
    # newline-joined string in the output list).
    assert lines[0].startswith("*Bookmarks (last 7 days, 3)*")
    assert any("see chapter 3" in line for line in lines)
    # Bookmark with no title falls back to URL.
    assert any("https://c.example" in line for line in lines[1:])


def test_weekly_digest_empty(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    lines = weekly_digest_lines(conn)
    assert lines == ["*Bookmarks (last 7 days):* none saved."]
