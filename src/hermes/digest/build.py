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


def _short_sender(raw: str) -> str:
    """'Casey <casey@unknown.io>' -> 'Casey'; 'casey@unknown.io' -> 'casey@unknown.io'."""
    s = (raw or "").strip()
    if not s:
        return ""
    # 'Display <addr@host>' shape: take everything before the first <
    if "<" in s:
        name = s.split("<", 1)[0].strip().strip('"').strip("'")
        if name:
            return name
        # No display name; fall back to addr
        return s.split("<", 1)[1].split(">", 1)[0]
    return s


def _short_subject(raw: str, max_len: int = 80) -> str:
    """Collapse internal whitespace and truncate to max_len with a single
    Unicode ellipsis (NOT three dots)."""
    s = (raw or "").strip().replace("\n", " ").replace("\r", " ")
    s = " ".join(s.split())  # collapse internal whitespace
    if len(s) > max_len:
        return s[: max_len - 1].rstrip() + "…"
    return s

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


def _default_enricher(cfg: Config) -> Callable[[bytes | None], EmailBrief | None]:
    """Real enrichment path: re-parse persisted RFC822 bytes and call gemma4.
    Threads cfg.user through so the LLM has identity context.
    Returns None when there's no raw_email to work with."""
    from hermes.digest.enrich import summarize_for_brief

    def _enrich(raw: bytes | None) -> EmailBrief | None:
        if not raw:
            return None
        try:
            msg = email.message_from_bytes(raw)
        except (TypeError, ValueError):
            return None
        return summarize_for_brief(msg, user=cfg.user)

    return _enrich


def _importance_rank(b: EmailBrief | None) -> int:
    """Sort key — lower is more important. None/unknown sit between
    'normal' and 'low' so missing-brief rows don't leapfrog real urgent ones."""
    if b is None:
        return 2
    return {"urgent": 0, "normal": 1, "low": 2, "noise": 3}.get(b.importance, 1)


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
        enrich_one = _default_enricher(cfg)

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

    # Phase J.2: surface the open-action queue depth in the brief itself
    # so the user sees "what do I need to do today" without a separate
    # /actions roundtrip. Lazily imported so the digest module doesn't
    # hard-couple to the actions package at import time.
    from hermes.actions.store import expire_snoozed, count_open, list_open
    expire_snoozed(db)
    n_open = count_open(db)
    if n_open:
        lines.append("")
        lines.append(f"*Open actions ({n_open})* — `/actions` for full list")
        # Show top 3 urgent (and normal as fallback) so the brief stays
        # self-contained for the most pressing items.
        top = list_open(db, include_low=False, limit=3)
        for a in top:
            if a.importance == "urgent":
                lines.append(f"  🔴 *[{a.id}]* {a.text[:120]}")
            else:
                lines.append(f"  *[{a.id}]* {a.text[:120]}")

    interesting = _interesting_messages(db, since_hours=24)
    if interesting:
        # Two-pass: enrich + filter, then sort by importance, then render.
        # Rows the LLM marked as noise (or that returned an empty summary)
        # are suppressed from the per-email block — they've already been
        # counted in the daily totals above and don't need to pollute review.
        enriched: list[tuple[int, str, str, EmailBrief]] = []
        suppressed = 0
        for uid, sender, subject, raw in interesting[:_MAX_INTERESTING]:
            brief = enrich_one(raw)
            if brief is None:
                suppressed += 1
                continue
            if brief.importance == "noise" or not brief.summary:
                suppressed += 1
                continue
            enriched.append((uid, sender, subject, brief))
        enriched.sort(key=lambda row: _importance_rank(row[3]))

        if enriched:
            suffix = f" · {suppressed} noise filtered" if suppressed else ""
            lines.append("")
            lines.append(f"*Overnight ({len(enriched)} to review{suffix})*")
            for uid, sender, subject, brief in enriched:
                badge = "🔴 " if brief.importance == "urgent" else ""
                lines.append("")
                lines.append(
                    f"• `[{uid}]` {badge}*{_short_subject(subject)}* — {_short_sender(sender)}"
                )
                if brief.summary:
                    lines.append(f"  {brief.summary}")
                if brief.actions:
                    lines.append("  *Actions:*")
                    for a in brief.actions:
                        lines.append(f"    – {a}")
                if brief.tools:
                    # Skip tools whose name already appears in the subject — if
                    # the email IS about that tool, listing it as "mentioned" is
                    # redundant noise. Case-insensitive substring match.
                    subj_lower = (subject or "").lower()
                    interesting_tools = [
                        t for t in brief.tools
                        if (t.name or "").lower() not in subj_lower
                    ]
                    if interesting_tools:
                        lines.append("  *Tools mentioned:*")
                        for t in interesting_tools:
                            if t.description:
                                lines.append(f"    – *{t.name}* — {t.description}")
                            else:
                                lines.append(f"    – *{t.name}*")
            if len(interesting) > _MAX_INTERESTING:
                lines.append("")
                lines.append(
                    f"  …and {len(interesting) - _MAX_INTERESTING} more (truncated)."
                )
        elif suppressed:
            lines.append("")
            lines.append(
                f"*Overnight* — {suppressed} messages, all filtered as routine."
            )

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
