"""Cron entry point for the stale-thread nag.

Scans for client mail older than N days that hasn't been replied to via
the outbox queue, and posts a single Telegram digest of those threads.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from loguru import logger
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.stale.check import (
    StalePipelineRow, StaleThread, find_stale_pipeline, find_stale_threads,
)
from hermes.telegram.client import TelegramClient


def _format(threads: list[StaleThread]) -> str:
    if not threads:
        return ""
    lines = [f"⏰ *Stale client threads ({len(threads)})*\n"]
    for t in threads[:15]:
        subj = (t.subject or "(no subject)")[:80]
        lines.append(f"`[{t.uid}]` *{subj}*")
        lines.append(f"   {t.sender}")
        lines.append(
            f"   {t.days_stale}d stale · `/draft {t.uid}` · "
            f"`/reclassify {t.uid} other` to dismiss"
        )
    return "\n".join(lines)


def _format_pipeline(rows: list[StalePipelineRow]) -> str:
    if not rows:
        return ""
    lines = [f"💤 *Waiting on reply ({len(rows)})*\n"]
    for r in rows[:15]:
        who = r.display_name or r.contact_email
        lines.append(f"*{who}* — {r.stage} · {r.days_waiting}d quiet")
        hint = f"`/draft {r.last_inbound_uid}` to follow up" if r.last_inbound_uid else r.contact_email
        lines.append(f"   {hint}")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-stale")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--days", type=int, default=5,
                   help="threshold in days for staleness (default: 5)")
    p.add_argument("--print", action="store_true",
                   help="print to stdout instead of Telegram")
    args = p.parse_args()

    cfg = load_config()
    db = connect(Path(args.db)); migrate(db)
    threads = find_stale_threads(db, days=args.days)
    pipeline_rows = find_stale_pipeline(db, days=4)
    parts = [t for t in (_format(threads), _format_pipeline(pipeline_rows)) if t]
    text = "\n\n".join(parts)
    if not text:
        logger.info("no stale threads/pipeline at {}d threshold", args.days)
        return 0
    if args.print:
        print(text)
        return 0
    TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id).send_message(text)
    logger.info("posted stale nag: {} threads, {} pipeline", len(threads), len(pipeline_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
