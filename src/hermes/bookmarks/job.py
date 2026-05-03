"""Weekly cron entry point for the bookmark digest."""
from __future__ import annotations
import argparse
from pathlib import Path
from loguru import logger
from hermes.bookmarks.store import weekly_digest_lines
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.telegram.client import TelegramClient


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-bookmarks-weekly")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--print", action="store_true")
    args = p.parse_args()
    cfg = load_config()
    db = connect(Path(args.db)); migrate(db)
    text = "\n".join(weekly_digest_lines(db))
    if args.print:
        print(text)
    else:
        TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id).send_message(text)
    logger.info("bookmarks-weekly digest posted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
