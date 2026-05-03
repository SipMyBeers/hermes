from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from hermes.config import Config


def build_morning_digest(db: sqlite3.Connection, cfg: Config) -> str:
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

    client_rows = db.execute(
        "SELECT sender, subject FROM messages "
        "WHERE classification='client' AND received_at > datetime('now','-1 day') "
        "ORDER BY received_at DESC LIMIT 5"
    ).fetchall()
    if client_rows:
        lines.append("")
        lines.append("*Client mail*")
        for sender, subject in client_rows:
            lines.append(f"  • {sender} — {subject}")

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
