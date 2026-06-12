"""Find client mail that we haven't replied to within N days."""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class StaleThread:
    uid: int
    sender: str
    subject: str
    received_at: str       # ISO 8601
    days_stale: int


def find_stale_threads(db: sqlite3.Connection, days: int = 5) -> list[StaleThread]:
    """Return client messages older than `days` with no outbox reply.

    A 'reply' is an outbox row with status='sent' AND related_uid=messages.uid.
    """
    cutoff = f"datetime('now', '-{int(days)} days')"
    rows = db.execute(f"""
        SELECT m.uid, m.sender, m.subject, m.received_at,
               CAST((julianday('now') - julianday(m.received_at)) AS INTEGER) AS days_stale
        FROM messages m
        WHERE m.classification = 'client'
          AND m.received_at < {cutoff}
          AND NOT EXISTS (
              SELECT 1 FROM outbox o
              WHERE o.related_uid = m.uid AND o.status = 'sent'
          )
        ORDER BY m.received_at ASC
    """).fetchall()
    return [
        StaleThread(uid=r[0], sender=r[1], subject=r[2] or "",
                    received_at=r[3], days_stale=int(r[4]))
        for r in rows
    ]


@dataclass(frozen=True)
class StalePipelineRow:
    contact_email: str
    display_name: str
    stage: str
    days_waiting: int
    last_inbound_uid: int | None   # most recent inbound uid, for /draft


def find_stale_pipeline(db: sqlite3.Connection, days: int = 4) -> list[StalePipelineRow]:
    """Prospects we wrote to last who have gone quiet for N+ days.

    Scope: lead/replied/quoted only — active/invoiced/closed have their own
    rhythms. 'We wrote last' = last_outbound set AND (no inbound or inbound
    older than outbound).
    """
    rows = db.execute(f"""
        SELECT p.contact_email, COALESCE(p.display_name, ''), p.stage,
               CAST((julianday('now') - julianday(p.last_outbound)) AS INTEGER)
        FROM pipeline p
        WHERE p.stage IN ('lead','replied','quoted')
          AND p.last_outbound IS NOT NULL
          AND p.last_outbound < datetime('now', '-{int(days)} days')
          AND (p.last_inbound IS NULL OR p.last_inbound < p.last_outbound)
        ORDER BY p.last_outbound ASC
    """).fetchall()
    out: list[StalePipelineRow] = []
    for email, name, stage, days_waiting in rows:
        uid_row = db.execute(
            "SELECT uid FROM messages WHERE LOWER(sender) LIKE ? "
            "ORDER BY received_at DESC LIMIT 1",
            (f"%{email.lower()}%",),
        ).fetchone()
        out.append(StalePipelineRow(
            contact_email=email, display_name=name, stage=stage,
            days_waiting=int(days_waiting),
            last_inbound_uid=int(uid_row[0]) if uid_row else None,
        ))
    return out
