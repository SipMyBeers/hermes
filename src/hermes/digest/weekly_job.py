"""Sunday-morning recap cron entry point."""
from __future__ import annotations
import argparse
from pathlib import Path
from loguru import logger
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.digest.weekly import build_weekly_recap
from hermes.telegram.client import TelegramClient


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-weekly-recap")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--print", action="store_true")
    args = p.parse_args()
    cfg = load_config()
    db = connect(Path(args.db)); migrate(db)
    text = build_weekly_recap(db)
    if args.print:
        print(text)
    else:
        TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id).send_message(text)
    logger.info("weekly recap posted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
