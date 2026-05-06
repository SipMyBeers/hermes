"""Open-action persistence + lifecycle for Hermes.

Each LLM-extracted action (from email enrichment or URL ingest) lands here
as a row that can be queried, marked done, snoozed, or dismissed via
Telegram. Source rows in messages/digestions stay immutable; this is the
mutable workflow surface.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import sqlite3
from loguru import logger  # noqa: F401  (kept for future warn paths)


@dataclass(frozen=True)
class Action:
    id: int
    source_kind: str
    source_id: int | None
    text: str
    importance: str
    status: str
    due_at: str | None
    snoozed_until: str | None
    created_at: str
    done_at: str | None
    source_subject: str | None
    source_sender: str | None
    # Phase N.1 thread consolidation. Defaults preserve back-compat for
    # any callsite still constructing Action directly without these.
    thread_count: int = 1
    thread_key: str | None = None


def add_actions(
    db: sqlite3.Connection,
    *,
    source_kind: str,
    source_id: int | None,
    texts: list[str],
    importance: str = "normal",
    source_subject: str | None = None,
    source_sender: str | None = None,
) -> list[int]:
    """Insert one row per non-empty text. Returns the new ids."""
    now = datetime.now(timezone.utc).isoformat()
    ids: list[int] = []
    for text in texts:
        text = (text or "").strip()
        if not text:
            continue
        cur = db.execute(
            "INSERT INTO actions"
            "(source_kind, source_id, text, importance, status, created_at,"
            " source_subject, source_sender)"
            " VALUES (?, ?, ?, ?, 'open', ?, ?, ?)",
            (source_kind, source_id, text, importance, now,
             source_subject, source_sender),
        )
        ids.append(cur.lastrowid)
    return ids


def add_or_consolidate_email_action(
    db: sqlite3.Connection,
    *,
    thread_key: str,
    source_id: int,                   # the new message's uid
    text: str,
    importance: str = "normal",
    source_subject: str | None = None,
    source_sender: str | None = None,
) -> tuple[int, bool]:
    """Phase N.1 thread-aware insert.

    If an open (or snoozed) email-source action already exists for this
    thread_key, bump its thread_count, point source_id at the latest uid,
    refresh subject/sender snapshots, and return (existing_id, False).
    Otherwise insert a new row with thread_count=1 and return (new_id, True).

    Behaviour notes:
      - Done / dismissed threads are NOT consolidated — a new reply on a
        closed thread starts a fresh action so the user actually sees it.
      - Importance is monotonic upward: once a thread is urgent, a follow-up
        normal/low message can't downgrade it. A normal thread can still be
        promoted to urgent if a later reply escalates.
      - Empty thread_key always falls through to a fresh INSERT (we never
        consolidate on the empty string — that would collapse unrelated mail).
      - Empty text is a no-op returning (0, False), matching add_actions().
    """
    text = (text or "").strip()
    if not text:
        return (0, False)
    if thread_key:
        existing = db.execute(
            "SELECT id, thread_count FROM actions "
            "WHERE thread_key=? AND source_kind='email' "
            "  AND status IN ('open','snoozed') "
            "ORDER BY created_at DESC LIMIT 1",
            (thread_key,),
        ).fetchone()
        if existing:
            aid, count = existing
            db.execute(
                "UPDATE actions SET source_id=?, thread_count=?, "
                "  source_subject=?, source_sender=?, "
                "  importance=CASE WHEN importance='urgent' THEN importance ELSE ? END "
                "WHERE id=?",
                (source_id, int(count or 1) + 1, source_subject, source_sender,
                 importance, aid),
            )
            return (aid, False)
    # No prior thread row — insert fresh.
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "INSERT INTO actions"
        "(source_kind, source_id, text, importance, status, created_at,"
        " source_subject, source_sender, thread_key, thread_count)"
        " VALUES ('email', ?, ?, ?, 'open', ?, ?, ?, ?, 1)",
        (source_id, text, importance, now,
         source_subject, source_sender, thread_key or None),
    )
    return (cur.lastrowid, True)


def list_open(
    db: sqlite3.Connection,
    *,
    include_low: bool = True,
    limit: int = 20,
) -> list[Action]:
    """Open actions, ordered urgent → normal → low, then oldest first.

    Excludes snoozed-with-future-snoozed_until (callers should run
    expire_snoozed first if they care about that)."""
    rank = "CASE importance WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END"
    where = "status='open'"
    if not include_low:
        where += " AND importance IN ('urgent','normal')"
    rows = db.execute(
        f"SELECT id, source_kind, source_id, text, importance, status,"
        f" due_at, snoozed_until, created_at, done_at,"
        f" source_subject, source_sender,"
        f" COALESCE(thread_count, 1), thread_key"
        f" FROM actions WHERE {where} "
        f"ORDER BY {rank}, created_at ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [Action(*r) for r in rows]


def count_open(db: sqlite3.Connection) -> int:
    return db.execute("SELECT COUNT(*) FROM actions WHERE status='open'").fetchone()[0]


def mark_done(db: sqlite3.Connection, action_id: int) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    res = db.execute(
        "UPDATE actions SET status='done', done_at=? "
        "WHERE id=? AND status IN ('open','snoozed','stale')",
        (now, action_id),
    )
    return res.rowcount > 0


def dismiss(db: sqlite3.Connection, action_id: int) -> bool:
    res = db.execute(
        "UPDATE actions SET status='dismissed' "
        "WHERE id=? AND status IN ('open','snoozed','stale')",
        (action_id,),
    )
    return res.rowcount > 0


def snooze(db: sqlite3.Connection, action_id: int, until: datetime) -> bool:
    iso = until.astimezone(timezone.utc).isoformat()
    res = db.execute(
        "UPDATE actions SET status='snoozed', snoozed_until=? "
        "WHERE id=? AND status IN ('open','stale')",
        (iso, action_id),
    )
    return res.rowcount > 0


def expire_snoozed(db: sqlite3.Connection) -> int:
    """Reopen snoozed actions whose snoozed_until is in the past. Returns
    the number reopened."""
    now = datetime.now(timezone.utc).isoformat()
    res = db.execute(
        "UPDATE actions SET status='open', snoozed_until=NULL "
        "WHERE status='snoozed' AND snoozed_until <= ?",
        (now,),
    )
    return res.rowcount


def decay_stale(db: sqlite3.Connection, days: int = 7) -> int:
    """Move 'open' actions older than `days` to 'stale'. Returns count moved.

    'stale' is still queryable but doesn't appear in the default
    /actions view — the assumption is that if you ignored it for a week,
    it wasn't actually important."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    res = db.execute(
        "UPDATE actions SET status='stale' "
        "WHERE status='open' AND created_at < ?",
        (cutoff,),
    )
    return res.rowcount


# --- natural-language "when" parsing for /snooze --------------------------

_REL_RE = re.compile(r"^\+?(\d+)\s*([dh])$", re.IGNORECASE)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday",
             "friday", "saturday", "sunday"]


def parse_when(spec: str, *, now: datetime | None = None) -> datetime | None:
    """Parse 'tomorrow', 'monday', '+3d', '2h', 'next week', 'friday morning'
    into an absolute UTC datetime. Returns None if unparseable.

    Hours/days only — nothing finer-grained than 1h."""
    spec = (spec or "").strip().lower()
    if not spec:
        return None
    now = now or datetime.now(timezone.utc)

    # +Nd / Nd / +Nh / Nh
    m = _REL_RE.match(spec)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
        return now + delta

    if spec in ("tomorrow", "tmrw"):
        return now + timedelta(days=1)
    if spec in ("today",):
        return now + timedelta(hours=4)   # later today = +4h
    if spec in ("next week", "nextweek"):
        return now + timedelta(days=7)

    # weekday names — "monday", "monday morning" (morning ignored beyond day)
    parts = spec.split()
    if parts and parts[0] in _WEEKDAYS:
        target = _WEEKDAYS.index(parts[0])
        days_ahead = (target - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return now + timedelta(days=days_ahead)

    return None
