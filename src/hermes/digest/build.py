from __future__ import annotations
import email
import sqlite3
from datetime import datetime, timezone
from email.header import decode_header, make_header
from typing import Callable
from hermes.config import Config
from hermes.digest.enrich import EmailBrief


def _decode(value: object) -> str:
    """Decode RFC 2047 encoded-word headers — defensive cover for any rows
    written before the daemon learned to decode at write-time."""
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)

# Cap how many emails we enrich in one digest. Each gemma4 call is ~3-8s,
# so 15 puts the worst-case digest job at a couple minutes — fine for cron.
_MAX_INTERESTING = 15

# Classifications worth surfacing in the brief — skip promo and transactional
# (the user explicitly does not want to see receipts or junk).
_INTERESTING_CLASSES = ("client", "review", "personal", "other")


def _interesting_messages(
    db: sqlite3.Connection, since_hours: int = 24
) -> list[tuple[int, str, str, bytes | None]]:
    """Recent (uid, sender, subject, raw_email) for messages worth a per-email block.

    Excludes the bootstrap sentinel row (sender '<bootstrap>', message_id
    'hermes-bootstrap-*') which the mail daemon writes on first run to anchor
    the IMAP UID range without fetching the historic backlog."""
    placeholders = ",".join("?" for _ in _INTERESTING_CLASSES)
    rows = db.execute(
        f"SELECT uid, sender, subject, raw_email FROM messages "
        f"WHERE classification IN ({placeholders}) "
        f"  AND received_at > datetime('now', ?) "
        f"  AND sender != '<bootstrap>' "
        f"  AND message_id NOT LIKE 'hermes-bootstrap-%' "
        f"ORDER BY received_at DESC",
        (*_INTERESTING_CLASSES, f"-{since_hours} hour"),
    ).fetchall()
    return [
        (int(uid), _decode(s), _decode(sub), raw)
        for uid, s, sub, raw in rows
    ]


def _default_enricher() -> Callable[[bytes | None], EmailBrief | None]:
    """Real enrichment path: re-parse persisted RFC822 bytes and call gemma4.
    Returns None when there's no raw_email to work with."""
    from hermes.digest.enrich import summarize_for_brief

    def _enrich(raw: bytes | None) -> EmailBrief | None:
        if not raw:
            return None
        try:
            msg = email.message_from_bytes(raw)
        except (TypeError, ValueError):
            return None
        return summarize_for_brief(msg)

    return _enrich


def build_morning_digest(
    db: sqlite3.Connection,
    cfg: Config,
    enrich_one: Callable[[bytes | None], EmailBrief | None] | None = None,
) -> str:
    """Build the morning Telegram brief.

    enrich_one: optional injection point for tests so the digest can be
    rendered without calling Ollama. None => use the real gemma4 path.
    """
    if enrich_one is None:
        enrich_one = _default_enricher()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    counts = dict(db.execute(
        "SELECT classification, COUNT(*) FROM messages "
        "WHERE received_at > datetime('now','-1 day') "
        "GROUP BY classification"
    ).fetchall())

    pending_rows = db.execute(
        "SELECT id, kind, to_addr, subject FROM outbox WHERE status='queued' ORDER BY id"
    ).fetchall()

    failed_count = db.execute(
        "SELECT COUNT(*) FROM outbox WHERE status='failed'"
    ).fetchone()[0]

    ledger_rows = db.execute(
        "SELECT direction, SUM(amount_cents) FROM ledger "
        "WHERE month = strftime('%Y-%m','now') GROUP BY direction"
    ).fetchall()
    ledger = {d: amt or 0 for d, amt in ledger_rows}

    lines: list[str] = [f"*Hermes morning report* — {now}", ""]

    classes = ["promo", "client", "personal", "transactional", "review", "other"]
    if not counts:
        lines.append("No messages in the last 24h.")
    else:
        lines.append("*Last 24h*")
        for c in classes:
            n = counts.get(c, 0)
            if n:
                lines.append(f"  {c}: {n}")

    interesting = _interesting_messages(db, since_hours=24)
    if interesting:
        lines.append("")
        lines.append(f"*Overnight ({len(interesting)} to review)*")
        for uid, sender, subject, raw in interesting[:_MAX_INTERESTING]:
            brief = enrich_one(raw)
            lines.append("")
            lines.append(f"• `[{uid}]` *{subject}* — {sender}")
            if brief and brief.summary:
                lines.append(f"  {brief.summary}")
            if brief and brief.actions:
                lines.append("  *Actions:*")
                for a in brief.actions:
                    lines.append(f"    – {a}")
            if brief and brief.tools:
                lines.append("  *Tools mentioned:*")
                for t in brief.tools:
                    if t.description:
                        lines.append(f"    – *{t.name}* — {t.description}")
                    else:
                        lines.append(f"    – *{t.name}*")
        if len(interesting) > _MAX_INTERESTING:
            lines.append("")
            lines.append(f"  …and {len(interesting) - _MAX_INTERESTING} more (truncated).")

    if pending_rows:
        lines.append("")
        lines.append(f"*Pending approval* ({len(pending_rows)})")
        for oid, kind, to_addr, subject in pending_rows[:10]:
            lines.append(f"  /approve {oid} — {kind} → {to_addr}: {subject}")

    if failed_count:
        lines.append("")
        lines.append(f"⚠ Failed sends: {failed_count} — `/retry <id>`")

    if ledger:
        lines.append("")
        lines.append("*This month*")
        out_dollars = ledger.get("out", 0) / 100
        in_dollars = ledger.get("in", 0) / 100
        lines.append(f"  Billed out: ${out_dollars:,.2f}")
        lines.append(f"  Spent: ${in_dollars:,.2f}")

    return "\n".join(lines)
