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
        f" source_subject, source_sender"
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
