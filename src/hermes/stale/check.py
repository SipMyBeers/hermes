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
