"""Daily housekeeping for the action log: expire snoozed, decay stale."""
from __future__ import annotations
import argparse
from pathlib import Path
from loguru import logger
from hermes.actions.store import decay_stale, expire_snoozed
from hermes.config import load_config
from hermes.db import connect, migrate


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-actions-decay")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--days", type=int, default=7)
    args = p.parse_args()
    load_config()  # validate config exists, even though we don't use it
    db = connect(Path(args.db))
    migrate(db)
    reopened = expire_snoozed(db)
    staled = decay_stale(db, days=args.days)
    logger.info(
        "actions housekeeping: reopened={} staled={}", reopened, staled
    )
    print(
        f"reopened {reopened} snoozed actions; "
        f"moved {staled} open actions to stale"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
