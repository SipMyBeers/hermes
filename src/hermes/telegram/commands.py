from __future__ import annotations
from datetime import datetime, timezone
import sqlite3
from loguru import logger
from hermes.config import Config
from hermes.mail.smtp_client import SmtpSender, build_message
from hermes.telegram.client import TelegramClient

HELP_TEXT = """*Hermes commands*
/status — daemon health + last-24h counts
/digest — on-demand morning report
/approve <id> — send queued draft/invoice
/reject <id> — discard
/invoice <client> [YYYY-MM] — generate invoice
/client list — show clients
/reclassify <uid> <label> — fix a wrong classification
/retry <id> — retry failed outbound
"""


def _do_send(cfg: Config, to_addr: str, subject: str, body: str) -> bool:
    """Hook so tests can patch SMTP without monkeypatching smtplib."""
    msg = build_message(cfg.gmail, to_addr, subject, body, [])
    try:
        SmtpSender(cfg.gmail).send(msg)
        return True
    except Exception as e:
        logger.error("send failed: {}", e)
        return False


def handle_update(
    update: dict,
    cfg: Config,
    db: sqlite3.Connection,
    tg: TelegramClient,
) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    if msg.get("chat", {}).get("id") != cfg.telegram.chat_id:
        logger.warning("ignoring update from chat_id={}", msg.get("chat", {}).get("id"))
        return
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        tg.send_message(HELP_TEXT)
        return
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    handlers = {
        "/status": _cmd_status,
        "/digest": _cmd_digest,
        "/approve": _cmd_approve,
        "/reject": _cmd_reject,
        "/help": lambda *a: tg.send_message(HELP_TEXT),
        "/start": lambda *a: tg.send_message(HELP_TEXT),
    }
    fn = handlers.get(cmd)
    if not fn:
        tg.send_message(HELP_TEXT)
        return
    fn(args, cfg, db, tg)


def _cmd_status(args, cfg, db, tg):
    last_24h = db.execute(
        "SELECT classification, COUNT(*) FROM messages "
        "WHERE received_at > datetime('now','-1 day') "
        "GROUP BY classification"
    ).fetchall()
    pending = db.execute("SELECT COUNT(*) FROM outbox WHERE status='queued'").fetchone()[0]
    failed = db.execute("SELECT COUNT(*) FROM outbox WHERE status='failed'").fetchone()[0]
    counts = ", ".join(f"{c}: {n}" for c, n in last_24h) or "no messages yet"
    tg.send_message(
        f"*Hermes status*\nLast 24h messages → {counts}\n"
        f"Pending approval: {pending}\nFailed sends: {failed}"
    )


def _cmd_digest(args, cfg, db, tg):
    from hermes.digest.build import build_morning_digest  # local import to avoid cycle
    text = build_morning_digest(db, cfg)
    tg.send_message(text)


def _cmd_approve(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /approve <outbox_id>")
        return
    try:
        oid = int(args[0])
    except ValueError:
        tg.send_message("Outbox id must be an integer")
        return
    row = db.execute(
        "SELECT to_addr, subject, body, status FROM outbox WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        tg.send_message(f"No outbox entry with id={oid}")
        return
    to_addr, subject, body, status = row
    if status != "queued":
        tg.send_message(f"Outbox {oid} status={status}, cannot send")
        return
    ok = _do_send(cfg, to_addr, subject, body)
    if ok:
        db.execute(
            "UPDATE outbox SET status='sent', sent_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), oid),
        )
        tg.send_message(f"Sent #{oid} to {to_addr}")
    else:
        db.execute("UPDATE outbox SET status='failed', last_error='send error' WHERE id=?", (oid,))
        tg.send_message(f"Failed to send #{oid} — see logs")


def _cmd_reject(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /reject <outbox_id>")
        return
    try:
        oid = int(args[0])
    except ValueError:
        tg.send_message("Outbox id must be an integer")
        return
    res = db.execute("DELETE FROM outbox WHERE id=? AND status='queued'", (oid,))
    tg.send_message(f"Rejected #{oid}" if res.rowcount else f"Nothing to reject for id={oid}")
