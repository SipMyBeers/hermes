from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from loguru import logger
from hermes.actions.store import (
    list_open, count_open, mark_done, dismiss, snooze,
    expire_snoozed, parse_when,
)
from hermes.bookmarks.store import save_bookmark
from hermes.config import Config
from hermes.ingest.url import ingest, find_url, URL_RE  # noqa: F401  (URL_RE re-exported)
from hermes.mail.smtp_client import SmtpSender, build_message
from hermes.telegram.client import TelegramClient

HELP_TEXT = """*Hermes Commands*

/status — Daemon status and 24-hour message counts.
/digest — Generate the morning report on demand.
/approve <id> — Send the queued draft or invoice.
/reject <id> — Discard a queued item.
/retry <id> — Retry a failed send.
/save <url> [notes...] — Save a bookmark for the weekly digest.
/actions — Open action items, urgent first.
/done <id> — Close an action.
/dismiss <id> — Soft-delete (won't show again).
/snooze <id> <when> — 'tomorrow', '+3d', 'monday', etc.
/read <url> — Ingest a URL and reply with summary + actions + tools.
/invoice <client> [YYYY-MM] — Render an invoice for a client.
/client list — List configured clients.
/reclassify <uid> <label> — Correct a message classification.
"""


def _do_send(
    cfg: Config,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list[Path] | None = None,
) -> bool:
    """Hook so tests can patch SMTP without monkeypatching smtplib."""
    paths = list(attachments or [])
    msg = build_message(cfg.gmail, to_addr, subject, body, paths)
    try:
        SmtpSender(cfg.gmail).send(msg)
        return True
    except Exception as e:
        logger.error("send failed: {}", e)
        return False


def _parse_attachments(raw: str | None) -> list[Path]:
    """Parse outbox.attachments column into a list[Path].

    Handles None, empty string, single-path string, and JSON-encoded list.
    Falls back to single-path interpretation if JSON parsing fails.
    """
    if not raw:
        return []
    raw = raw.strip()
    # Try JSON first (future-proof for multi-attachment)
    if raw.startswith("["):
        try:
            paths = json.loads(raw)
            return [Path(p) for p in paths if p]
        except (json.JSONDecodeError, TypeError):
            pass
    # Fall back: single path string (Task 17's current shape)
    return [Path(raw)]


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
        # Bare URL? Treat as /read.
        url = find_url(text)
        if url:
            _ingest_and_reply(url, cfg, db, tg)
            return
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
        "/save": _cmd_save,
        "/read": _cmd_read,
        "/actions": _cmd_actions,
        "/done": _cmd_done,
        "/dismiss": _cmd_dismiss,
        "/snooze": _cmd_snooze,
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
        "SELECT to_addr, subject, body, status, attachments FROM outbox WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        tg.send_message(f"No outbox entry with id={oid}")
        return
    to_addr, subject, body, status, attachments_raw = row
    if status != "queued":
        tg.send_message(f"Outbox {oid} status={status}, cannot send")
        return
    ok = _do_send(cfg, to_addr, subject, body, _parse_attachments(attachments_raw))
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


def _cmd_save(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /save <url> [notes...]")
        return
    url = args[0]
    if not url.startswith(("http://", "https://")):
        tg.send_message(f"That doesn't look like a URL: {url}")
        return
    notes = " ".join(args[1:]) if len(args) > 1 else None
    bid = save_bookmark(db, url=url, notes=notes)
    msg = f"Saved bookmark #{bid} — {url}"
    if notes:
        msg += f"\n{notes}"
    tg.send_message(msg)


def _cmd_read(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /read <url>")
        return
    url = args[0]
    if not url.startswith(("http://", "https://")):
        tg.send_message(f"That doesn't look like a URL: {url}")
        return
    _ingest_and_reply(url, cfg, db, tg)


def _cmd_actions(args, cfg, db, tg):
    # Reopen anything whose snooze expired since the last call so the
    # listing reflects current reality without waiting for the cron.
    expire_snoozed(db)
    rows = list_open(db, include_low=True, limit=20)
    if not rows:
        tg.send_message("No open actions. Nice.")
        return
    lines = [f"*Open actions ({len(rows)})*", ""]
    last_kind = None
    for a in rows:
        if a.source_kind != last_kind:
            lines.append("")
            label = {"email": "✉️ Email", "digestion": "🔖 URL",
                     "manual": "📝 Manual"}.get(a.source_kind, a.source_kind)
            lines.append(f"*{label}*")
            last_kind = a.source_kind
        badge = "🔴 " if a.importance == "urgent" else (
            "⚪ " if a.importance == "low" else ""
        )
        ctx = ""
        if a.source_subject:
            ctx = f"  _{a.source_subject[:60]}_\n"
        lines.append(f"{badge}*[{a.id}]* {a.text}\n{ctx}".rstrip())
    lines.append("")
    lines.append("`/done <id>` · `/dismiss <id>` · `/snooze <id> <when>`")
    tg.send_message("\n".join(lines))


def _cmd_done(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /done <action_id>")
        return
    try:
        aid = int(args[0])
    except ValueError:
        tg.send_message("Action id must be an integer")
        return
    if mark_done(db, aid):
        tg.send_message(f"Done #{aid} ✓")
    else:
        tg.send_message(
            f"Nothing to close for action {aid} (already done or unknown)"
        )


def _cmd_dismiss(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /dismiss <action_id>")
        return
    try:
        aid = int(args[0])
    except ValueError:
        tg.send_message("Action id must be an integer")
        return
    if dismiss(db, aid):
        tg.send_message(f"Dismissed #{aid}")
    else:
        tg.send_message(f"Nothing to dismiss for action {aid}")


def _cmd_snooze(args, cfg, db, tg):
    if len(args) < 2:
        tg.send_message(
            "Usage: /snooze <action_id> <when>\n"
            "When: 'tomorrow', '+3d', 'monday', '2h', 'next week'"
        )
        return
    try:
        aid = int(args[0])
    except ValueError:
        tg.send_message("Action id must be an integer")
        return
    when_str = " ".join(args[1:])
    until = parse_when(when_str)
    if until is None:
        tg.send_message(
            f"Couldn't parse '{when_str}'. Try '+3d', 'tomorrow', 'monday'."
        )
        return
    if snooze(db, aid, until):
        tg.send_message(
            f"Snoozed #{aid} until {until.strftime('%a %b %d %H:%M UTC')}"
        )
    else:
        tg.send_message(f"Nothing to snooze for action {aid}")


def _ingest_and_reply(url: str, cfg, db, tg) -> None:
    """Shared ingest path used by /read and the bare-URL detector."""
    tg.send_message(f"📥 Reading {url} …")
    d = ingest(
        url,
        db,
        source_chat_id=cfg.telegram.chat_id,
        cookies_path=cfg.ingest.cookies_path,
        cookies_from_browser=cfg.ingest.cookies_from_browser,
    )
    if d.error:
        tg.send_message(f"⚠ Couldn't ingest #{d.id}\n{url}\n{d.error}")
        return
    lines = [f"🔖 *#{d.id}* {d.title or url}"]
    if d.summary:
        lines.append(d.summary)
    if d.actions:
        lines.append("")
        lines.append("*Actions:*")
        for a in d.actions:
            lines.append(f"  – {a}")
    if d.tools:
        lines.append("")
        lines.append("*Tools mentioned:*")
        for t in d.tools:
            name = t.get("name", "")
            desc = t.get("description", "")
            if name and desc:
                lines.append(f"  – *{name}* — {desc}")
            elif name:
                lines.append(f"  – *{name}*")
    tg.send_message("\n".join(lines))
