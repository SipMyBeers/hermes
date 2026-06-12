"""Liveness watchdog for hermes-mail.

Runs as its own launchd job every 30 minutes. Reads the heartbeats table
directly (no daemon code paths — a daemon bug must not break the watchdog),
alerts Telegram when the heartbeat is stale, and attempts one
`launchctl kickstart` of the mail daemon.
"""
from __future__ import annotations
import argparse
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from loguru import logger

MAIL_JOB = "system/com.beers.hermes-mail"


def check(
    conn: sqlite3.Connection,
    notify: Callable[[str], object],
    kickstart: Callable[[], object],
    max_age_minutes: int = 60,
) -> bool:
    """Return True when healthy. On stale/missing heartbeat: notify + kickstart."""
    row = conn.execute(
        "SELECT ts FROM heartbeats WHERE component='hermes-mail'"
    ).fetchone()
    now = datetime.now(timezone.utc)
    if row:
        last = datetime.fromisoformat(row[0])
        if now - last <= timedelta(minutes=max_age_minutes):
            return True
        msg = (
            f"hermes-mail heartbeat stale: last beat {row[0]} "
            f"(>{max_age_minutes}m ago). Kickstarting."
        )
    else:
        msg = "hermes-mail has no heartbeat row — daemon likely never started. Kickstarting."
    logger.warning(msg)
    try:
        notify(msg)
    except Exception as e:  # Telegram down must not stop the kickstart
        logger.error("watchdog notify failed: {}", e)
    try:
        kickstart()
    except Exception as e:
        logger.error("watchdog kickstart failed: {}", e)
    return False


def _kickstart() -> None:
    subprocess.run(["launchctl", "kickstart", "-k", MAIL_JOB], check=False, timeout=30)


def _notify(text: str) -> None:
    from hermes.config import load_config
    from hermes.telegram.client import TelegramClient
    tg = load_config().telegram
    TelegramClient(tg.bot_token, tg.chat_id).send_message(text)


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-watchdog")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--max-age-minutes", type=int, default=60)
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    healthy = check(
        conn, notify=_notify, kickstart=_kickstart, max_age_minutes=args.max_age_minutes
    )
    return 0 if healthy else 1
