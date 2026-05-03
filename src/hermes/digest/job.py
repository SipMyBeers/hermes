from __future__ import annotations
import argparse
from pathlib import Path
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.digest.build import build_morning_digest
from hermes.telegram.client import TelegramClient


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-digest")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--evening", action="store_true")
    p.add_argument("--print", action="store_true", help="print to stdout instead of Telegram")
    args = p.parse_args()
    cfg = load_config()
    db = connect(Path(args.db)); migrate(db)
    text = build_morning_digest(db, cfg)
    if args.print:
        print(text)
    else:
        TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id).send_message(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
