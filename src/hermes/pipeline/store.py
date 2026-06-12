"""Pipeline store: track each prospect from lead to invoiced.

One row per contact email. Stage moves forward via explicit /stage commands
plus two automatic transitions: outbound mail advances lead->replied, and a
sent invoice sets invoiced. Timestamps are fed by the mail daemon (inbound)
and the Sent watcher (outbound).
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone

STAGES = ("lead", "replied", "quoted", "active", "invoiced", "closed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_lead(
    db: sqlite3.Connection,
    contact_email: str,
    display_name: str,
    source: str,
    project: str | None = None,
) -> None:
    """Insert a new lead; existing rows keep their stage (never regress)."""
    email = contact_email.lower()
    now = _now()
    db.execute(
        "INSERT INTO pipeline (contact_email, display_name, project, stage, source, created, updated) "
        "VALUES (?, ?, ?, 'lead', ?, ?, ?) "
        "ON CONFLICT(contact_email) DO UPDATE SET "
        "  display_name = COALESCE(NULLIF(excluded.display_name, ''), pipeline.display_name), "
        "  project = COALESCE(excluded.project, pipeline.project), "
        "  updated = excluded.updated",
        (email, display_name, project, source, now, now),
    )
    db.commit()


def touch_inbound(db: sqlite3.Connection, contact_email: str) -> bool:
    """Record inbound mail from a pipeline contact. False if not tracked."""
    cur = db.execute(
        "UPDATE pipeline SET last_inbound = ?, updated = ? WHERE contact_email = ?",
        (_now(), _now(), contact_email.lower()),
    )
    db.commit()
    return cur.rowcount > 0


def touch_outbound(db: sqlite3.Connection, contact_email: str) -> bool:
    """Record outbound mail to a pipeline contact; lead auto-advances to
    replied (you answered the prospect). False if not tracked."""
    now = _now()
    cur = db.execute(
        "UPDATE pipeline SET last_outbound = ?, updated = ?, "
        "stage = CASE WHEN stage = 'lead' THEN 'replied' ELSE stage END "
        "WHERE contact_email = ?",
        (now, now, contact_email.lower()),
    )
    db.commit()
    return cur.rowcount > 0


def set_stage(db: sqlite3.Connection, contact_or_fragment: str, stage: str) -> str:
    """Set the stage by exact email or a unique fragment of email/name/project.
    Returns the matched contact_email; raises ValueError on no/ambiguous match
    or an invalid stage."""
    if stage not in STAGES:
        raise ValueError(f"invalid stage {stage!r}; one of {', '.join(STAGES)}")
    frag = contact_or_fragment.lower()
    exact = db.execute(
        "SELECT contact_email FROM pipeline WHERE contact_email = ?", (frag,)
    ).fetchall()
    if exact:
        matches = exact
    else:
        like = f"%{frag}%"
        matches = db.execute(
            "SELECT contact_email FROM pipeline WHERE contact_email LIKE ? "
            "OR LOWER(COALESCE(display_name,'')) LIKE ? OR LOWER(COALESCE(project,'')) LIKE ?",
            (like, like, like),
        ).fetchall()
    if not matches:
        raise ValueError(f"no pipeline contact matches {contact_or_fragment!r}")
    if len(matches) > 1:
        opts = ", ".join(m[0] for m in matches)
        raise ValueError(f"ambiguous: {contact_or_fragment!r} matches {opts}")
    email = matches[0][0]
    db.execute(
        "UPDATE pipeline SET stage = ?, updated = ? WHERE contact_email = ?",
        (stage, _now(), email),
    )
    db.commit()
    return email


def board(db: sqlite3.Connection) -> list[dict]:
    """All pipeline rows ordered by stage progression then recency."""
    order = {s: i for i, s in enumerate(STAGES)}
    rows = db.execute(
        "SELECT contact_email, display_name, project, stage, source, "
        "last_inbound, last_outbound, created, updated, notes FROM pipeline"
    ).fetchall()
    cols = ("contact_email", "display_name", "project", "stage", "source",
            "last_inbound", "last_outbound", "created", "updated", "notes")
    dicts = [dict(zip(cols, r)) for r in rows]
    return sorted(dicts, key=lambda d: (order[d["stage"]], d["updated"]), reverse=False)
