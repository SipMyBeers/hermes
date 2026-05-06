"""Content-rounds cron: pull saved feeds, summarize new items, post button
cards to Telegram.

Runs ~4 times during waking hours via launchd plist (08/12/16/20). Each run:
1. For each active feed in `feeds` table:
   - poll_feed() to get current entries
   - store_new_items() — only NEW entries (UNIQUE constraint dedupes)
   - mark_polled()
2. For each new entry, in published-time order (newest first):
   - Fetch full page via webclaw (LLM-optimized markdown).
   - Summarize via gemma4.
   - INSERT into actions(source_kind='feed_item', source_id=feed_items.id).
   - Post a Telegram card with three buttons.
3. Cap per-round at _MAX_PER_ROUND cards to avoid flooding the user.

Failures per item are isolated — one bad feed or one bad fetch doesn't kill
the round.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sqlite3
from loguru import logger
from hermes.actions.store import add_actions
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.ingest.summary import summarize
from hermes.ingest.web import extract_web
from hermes.rss.poller import (
    FeedItem,
    list_active_feeds,
    mark_polled,
    poll_feed,
    store_new_items,
)
from hermes.telegram.client import TelegramClient
from hermes.telegram.keyboards import feed_card

# Cap how many feed-item cards we send per round so a busy day with 50
# new entries across feeds doesn't flood Telegram. Enforced in the SQL
# LIMIT too — keeps memory pressure low if a feed flares.
_MAX_PER_ROUND = 8


def _ingest_and_summarize(item: FeedItem) -> tuple[str, str]:
    """Fetch the article via webclaw (or trafilatura fallback) and summarize.

    Returns (summary, body_text). Empty strings on failure — caller surfaces
    a bare title-only card so the user still sees the link."""
    try:
        doc = extract_web(item.url)
    except Exception as e:
        logger.warning("rounds: extract_web failed for {}: {}", item.url, e)
        return "", ""
    if not doc.text:
        return "", ""
    try:
        cs = summarize(title=item.title, text=doc.text)
    except Exception as e:
        logger.warning("rounds: summarize failed for {}: {}", item.url, e)
        return "", doc.text
    return cs.summary, doc.text


def run_round(db: sqlite3.Connection, tg: TelegramClient | None) -> int:
    """Run one content round. Returns count of cards posted."""
    feeds = list_active_feeds(db)
    if not feeds:
        return 0

    # Pull every active feed; isolate failures per feed so a 503 on one
    # doesn't black-hole the rest of the round.
    new_item_ids: list[int] = []
    for feed_url, _name in feeds:
        try:
            items = poll_feed(feed_url)
            mark_polled(db, feed_url)
            new_item_ids += store_new_items(db, items)
        except Exception as e:
            logger.warning("rounds: poll_feed failed for {}: {}", feed_url, e)

    if not new_item_ids:
        return 0

    # Look up the new feed_items rows in time order (newest first), capped.
    placeholders = ",".join("?" for _ in new_item_ids)
    rows = db.execute(
        f"SELECT id, feed_url, title, url, published_at FROM feed_items "
        f"WHERE id IN ({placeholders}) "
        f"ORDER BY published_at DESC LIMIT ?",
        (*new_item_ids, _MAX_PER_ROUND),
    ).fetchall()

    posted = 0
    for fid, feed_url, title, url, published_at in rows:
        title = title or ""
        url = url or ""
        summary_text, _body_text = _ingest_and_summarize(
            FeedItem(
                feed_url=feed_url, entry_id="", title=title,
                url=url, published_at=published_at or "",
            )
        )
        # Save summary on the feed_item.
        db.execute(
            "UPDATE feed_items SET summary=? WHERE id=?",
            (summary_text or "", fid),
        )
        # Add the row to the action log so it appears in /actions. Importance
        # is 'low' so feed_item rows don't trigger urgent pings or count
        # against the default /actions display unless include_low=True.
        action_ids = add_actions(
            db,
            source_kind="feed_item",
            source_id=fid,
            texts=[f"Read: {title[:120]}"],
            importance="low",
            source_subject=title or "(no title)",
            source_sender=feed_url,
        )
        action_id = action_ids[0] if action_ids else 0

        if tg is None:
            posted += 1
            continue

        text = f"📰 *{title[:120] or '(untitled)'}*\n{url}"
        if summary_text:
            text += f"\n\n{summary_text[:600]}"
        try:
            tg.send_message(
                text,
                reply_markup=feed_card(action_id, fid, url),
            )
            posted += 1
        except Exception as e:
            logger.warning("rounds: telegram send failed for fid={}: {}", fid, e)

    return posted


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-rounds")
    p.add_argument(
        "--db",
        default=str(Path.home() / ".hermes" / "state.sqlite"),
    )
    p.add_argument(
        "--print", action="store_true",
        help="dry-run: poll + summarize but skip Telegram",
    )
    args = p.parse_args()
    cfg = load_config()
    db = connect(Path(args.db))
    migrate(db)
    tg = None
    if not args.print:
        tg = TelegramClient(
            cfg.telegram.bot_token, cfg.telegram.chat_id,
        )
    n = run_round(db, tg)
    logger.info("rounds: posted {} cards", n)
    print(f"posted {n} cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
