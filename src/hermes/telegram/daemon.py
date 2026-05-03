from __future__ import annotations
import argparse
from pathlib import Path
import time
from loguru import logger
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.telegram.client import TelegramClient
from hermes.telegram.commands import handle_update


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-tg")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    args = p.parse_args()

    cfg = load_config()
    db = connect(Path(args.db)); migrate(db)
    tg = TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id)
    offset = 0
    logger.info("hermes-tg started, chat_id={}", cfg.telegram.chat_id)
    while True:
        try:
            updates = tg.get_updates(offset=offset, timeout=30)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    handle_update(u, cfg, db, tg)
                except Exception as e:
                    logger.exception("handler failed: {}", e)
                    tg.send_message(f"⚠ handler error: {e}")
        except Exception as e:
            logger.warning("getUpdates failed: {}; sleeping 10s", e)
            time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
