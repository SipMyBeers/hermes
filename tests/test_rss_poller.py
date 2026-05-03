import pytest
from hermes.rss.poller import FeedItem, poll_feed, daily_digest_lines


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
    item = FeedItem(feed_url="u", entry_id="e", title="t", url="u", published_at="2026-05-02")
    assert item.summary == ""


def test_poll_feed_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="v1.2"):
        poll_feed("https://example.com/feed.xml")


def test_daily_digest_lines_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="v1.2"):
        daily_digest_lines(db=None)
