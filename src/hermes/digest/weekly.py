"""Build the weekly Sunday recap.

Aggregates the last 7 days of Hermes activity into a one-message Telegram
post: actions closed/dismissed/snoozed/stale, mail volume by class, ledger
in/out, bookmarks saved, URL ingestions. Nothing actionable — just a
diagnostic glance at how the week went."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone


def _count(db: sqlite3.Connection, sql: str, *args) -> int:
    row = db.execute(sql, args).fetchone()
    return int(row[0] or 0) if row else 0


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def build_weekly_recap(db: sqlite3.Connection) -> str:
    """Compose the Sunday recap as a Markdown string ready for Telegram."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    week_clause = "datetime('now', '-7 days')"

    # Action lifecycle in the last 7 days.
    closed = _count(
        db,
        f"SELECT COUNT(*) FROM actions WHERE done_at > {week_clause} AND status='done'",
    )
    dismissed = _count(
        db,
        f"SELECT COUNT(*) FROM actions WHERE status='dismissed'"
        f" AND created_at > {week_clause}",
    )
    snoozed_now = _count(
        db,
        "SELECT COUNT(*) FROM actions WHERE status='snoozed'",
    )
    stale_now = _count(
        db,
        "SELECT COUNT(*) FROM actions WHERE status='stale'",
    )
    open_now = _count(
        db,
        "SELECT COUNT(*) FROM actions WHERE status='open'",
    )

    # Mail volume by classification, last 7 days.
    rows = db.execute(
        f"SELECT classification, COUNT(*) FROM messages "
        f"WHERE received_at > {week_clause} GROUP BY classification"
    ).fetchall()
    mail_counts = {c: int(n) for c, n in rows}

    # Ledger.
    led_in = _count(
        db,
        f"SELECT SUM(amount_cents) FROM ledger "
        f"WHERE direction='in' AND paid_at > {week_clause}",
    )
    led_out = _count(
        db,
        f"SELECT SUM(amount_cents) FROM ledger "
        f"WHERE direction='out' AND sent_at > {week_clause}",
    )

    # Bookmarks + URL ingestions.
    bm = _count(
        db,
        f"SELECT COUNT(*) FROM bookmarks WHERE saved_at > {week_clause}",
    )
    url_ingests = _count(
        db,
        f"SELECT COUNT(*) FROM digestions WHERE ingested_at > {week_clause}",
    )

    # Senders learned as promo this week.
    promos_learned = _count(
        db,
        f"SELECT COUNT(*) FROM learned_promos "
        f"WHERE last_dismissed_at > {week_clause}",
    )

    lines: list[str] = [f"*Hermes — week of {now}*", ""]

    lines.append("*Actions*")
    lines.append(f"  Closed: {closed} · Dismissed: {dismissed}")
    lines.append(f"  Open now: {open_now} · Snoozed: {snoozed_now} · Stale: {stale_now}")

    if mail_counts:
        lines.append("")
        lines.append("*Mail*")
        for c in ("client", "review", "personal", "transactional", "promo", "other"):
            n = mail_counts.get(c, 0)
            if n:
                lines.append(f"  {c}: {n}")

    if led_in or led_out:
        lines.append("")
        lines.append("*Ledger*")
        if led_out:
            lines.append(f"  Billed out: {_money(led_out)}")
        if led_in:
            lines.append(f"  Spent (receipts): {_money(led_in)}")

    if bm or url_ingests:
        lines.append("")
        lines.append("*Saved*")
        if url_ingests:
            lines.append(f"  URLs ingested: {url_ingests}")
        if bm:
            lines.append(f"  Bookmarks: {bm}")

    if promos_learned:
        lines.append("")
        lines.append(f"*Learning*: {promos_learned} sender(s) added to the auto-trash list this week.")

    if all(v == 0 for v in (closed, dismissed, len(mail_counts), led_in, led_out, bm, url_ingests, promos_learned)):
        lines.append("")
        lines.append("Quiet week. Nothing notable.")

    return "\n".join(lines)
