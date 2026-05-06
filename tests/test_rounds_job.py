"""Tests for the Phase M.2 hermes-rounds orchestrator.

The rounds job pulls saved feeds, dedupes via the schema's UNIQUE
constraint, fetches each new entry, summarizes it, logs an action,
and posts a Telegram card. Failures per feed and per item must stay
isolated — one bad feed can't kill the whole round.
"""
from __future__ import annotations
from unittest.mock import MagicMock, patch

from hermes.db import connect, migrate
from hermes.rounds.job import _MAX_PER_ROUND, run_round
from hermes.rss.poller import FeedItem, upsert_feed


def _seed_feeds(db, *urls: str) -> None:
    for u in urls:
        upsert_feed(db, u, name=u)


def _fake_item(feed_url: str, eid: str, *, title: str | None = None,
               url: str | None = None,
               published_at: str = "2026-05-02T00:00:00") -> FeedItem:
    return FeedItem(
        feed_url=feed_url,
        entry_id=eid,
        title=title or f"title-{eid}",
        url=url or f"https://example.com/{eid}",
        published_at=published_at,
    )


class _FakeDoc:
    """Stand-in for ExtractedDoc — only attributes the rounds job reads."""
    def __init__(self, text: str = "body text", title: str = ""):
        self.text = text
        self.title = title


class _FakeSummary:
    def __init__(self, summary: str = "short summary"):
        self.summary = summary
        self.actions: tuple = ()
        self.tools: tuple = ()


def test_run_round_no_feeds_returns_zero(tmp_path):
    """Empty feeds table → no work, no Telegram calls."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    tg = MagicMock()
    n = run_round(db, tg)
    assert n == 0
    tg.send_message.assert_not_called()


def test_run_round_polls_each_feed(tmp_path):
    """Two feeds, each with 1 new item: poll_feed runs twice, mark_polled
    runs twice, and 2 cards land in Telegram."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    _seed_feeds(db, "https://a.example/rss", "https://b.example/rss")
    tg = MagicMock()

    poll_calls: list[str] = []

    def fake_poll(feed_url: str):
        poll_calls.append(feed_url)
        return [_fake_item(feed_url, f"e-{feed_url[-1]}")]

    with patch("hermes.rounds.job.poll_feed", side_effect=fake_poll), \
         patch("hermes.rounds.job.extract_web", return_value=_FakeDoc()), \
         patch("hermes.rounds.job.summarize", return_value=_FakeSummary()):
        n = run_round(db, tg)

    assert n == 2
    assert sorted(poll_calls) == ["https://a.example/rss", "https://b.example/rss"]
    # Both feeds got their last_polled_at set.
    last_polled = db.execute(
        "SELECT url, last_polled_at FROM feeds ORDER BY url"
    ).fetchall()
    assert all(r[1] is not None for r in last_polled)
    # Two Telegram cards.
    assert tg.send_message.call_count == 2


def test_run_round_dedupes_via_store(tmp_path):
    """Same items returned twice — second round posts zero cards because
    the UNIQUE(feed_url, entry_id) constraint dedupes."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    _seed_feeds(db, "https://a.example/rss")
    items = [_fake_item("https://a.example/rss", "e-1"),
             _fake_item("https://a.example/rss", "e-2")]
    tg = MagicMock()
    with patch("hermes.rounds.job.poll_feed", return_value=items), \
         patch("hermes.rounds.job.extract_web", return_value=_FakeDoc()), \
         patch("hermes.rounds.job.summarize", return_value=_FakeSummary()):
        first = run_round(db, tg)
        tg.reset_mock()
        second = run_round(db, tg)
    assert first == 2
    assert second == 0
    tg.send_message.assert_not_called()


def test_run_round_caps_at_max_per_round(tmp_path):
    """20 brand-new items across one feed → at most _MAX_PER_ROUND cards."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    _seed_feeds(db, "https://a.example/rss")
    items = [
        _fake_item("https://a.example/rss", f"e-{i}",
                   published_at=f"2026-05-02T00:{i:02d}:00")
        for i in range(20)
    ]
    tg = MagicMock()
    with patch("hermes.rounds.job.poll_feed", return_value=items), \
         patch("hermes.rounds.job.extract_web", return_value=_FakeDoc()), \
         patch("hermes.rounds.job.summarize", return_value=_FakeSummary()):
        n = run_round(db, tg)
    assert n == _MAX_PER_ROUND
    assert tg.send_message.call_count == _MAX_PER_ROUND


def test_run_round_isolates_feed_failure(tmp_path):
    """One feed raises in poll_feed; the other still polled and posted."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    _seed_feeds(db, "https://broken.example/rss", "https://ok.example/rss")
    tg = MagicMock()

    def flaky_poll(feed_url: str):
        if "broken" in feed_url:
            raise RuntimeError("503 service unavailable")
        return [_fake_item(feed_url, "ok-1")]

    with patch("hermes.rounds.job.poll_feed", side_effect=flaky_poll), \
         patch("hermes.rounds.job.extract_web", return_value=_FakeDoc()), \
         patch("hermes.rounds.job.summarize", return_value=_FakeSummary()):
        n = run_round(db, tg)
    assert n == 1
    # Only the ok feed got mark_polled; the broken one bailed before.
    polled = db.execute(
        "SELECT url, last_polled_at FROM feeds"
    ).fetchall()
    polled_map = dict(polled)
    assert polled_map["https://ok.example/rss"] is not None
    assert polled_map["https://broken.example/rss"] is None


def test_run_round_skips_telegram_when_summary_fails(tmp_path):
    """extract_web returns empty doc → summary blank → card still posted
    with title-only payload (the user can still tap Read to open the link)."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    _seed_feeds(db, "https://a.example/rss")
    tg = MagicMock()
    items = [_fake_item("https://a.example/rss", "e-1", title="Just a title",
                        url="https://example.com/1")]
    with patch("hermes.rounds.job.poll_feed", return_value=items), \
         patch("hermes.rounds.job.extract_web", return_value=_FakeDoc(text="")):
        n = run_round(db, tg)
    assert n == 1
    sent_text = tg.send_message.call_args.args[0]
    assert "Just a title" in sent_text
    # No summary text appended (only header line + url).
    assert "short summary" not in sent_text
    # Stored summary is empty.
    row = db.execute("SELECT summary FROM feed_items WHERE entry_id='e-1'").fetchone()
    assert row[0] == ""


def test_run_round_writes_action_log(tmp_path):
    """A feed_item action row lands with source_kind='feed_item' so it
    shows up in /actions and in dismiss-aware callbacks."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    _seed_feeds(db, "https://a.example/rss")
    items = [_fake_item("https://a.example/rss", "e-1", title="A post")]
    tg = MagicMock()
    with patch("hermes.rounds.job.poll_feed", return_value=items), \
         patch("hermes.rounds.job.extract_web", return_value=_FakeDoc()), \
         patch("hermes.rounds.job.summarize", return_value=_FakeSummary()):
        run_round(db, tg)
    row = db.execute(
        "SELECT source_kind, source_id, importance, source_subject, "
        "source_sender, status FROM actions"
    ).fetchone()
    assert row is not None
    assert row[0] == "feed_item"
    # source_id = the feed_items.id of the saved entry.
    fid = db.execute(
        "SELECT id FROM feed_items WHERE entry_id='e-1'"
    ).fetchone()[0]
    assert row[1] == fid
    assert row[2] == "low"  # feed_item rows are low-importance.
    assert row[3] == "A post"
    assert row[4] == "https://a.example/rss"
    assert row[5] == "open"


def test_run_round_print_mode_skips_telegram(tmp_path):
    """tg=None (--print dry-run) still polls + stores + writes actions
    but never tries to send a message."""
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    _seed_feeds(db, "https://a.example/rss")
    items = [_fake_item("https://a.example/rss", "e-1")]
    with patch("hermes.rounds.job.poll_feed", return_value=items), \
         patch("hermes.rounds.job.extract_web", return_value=_FakeDoc()), \
         patch("hermes.rounds.job.summarize", return_value=_FakeSummary()):
        n = run_round(db, None)
    # Counted 1 even with no Telegram (the action log entry is the proof
    # of work).
    assert n == 1
    n_actions = db.execute(
        "SELECT COUNT(*) FROM actions WHERE source_kind='feed_item'"
    ).fetchone()[0]
    assert n_actions == 1
