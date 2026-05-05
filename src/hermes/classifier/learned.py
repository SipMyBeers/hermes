"""Persistent feedback for the rule classifier — every Dismiss tap on an
email-source action records the sender. Senders accumulating dismiss counts
get auto-classified as promo on the next inbound mail."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

# How many times a sender must be Dismissed before the classifier promotes
# them to auto-promo. 1 = trust the user immediately; 2 = require corroboration.
PROMO_THRESHOLD = 2


@dataclass(frozen=True)
class LearnedPromo:
    sender_email: str
    sender_domain: str
    dismissed_count: int
    first_dismissed_at: str
    last_dismissed_at: str


def _extract_addr(sender_email: str) -> str:
    """Pull the bare address out of a possibly-fancy From header.

    Accepts 'Casey <casey@x.com>', 'casey@x.com', or '<casey@x.com>'. Returns
    the lowercased address or '' on anything unparseable / missing '@'."""
    from email.utils import getaddresses
    addrs = getaddresses([sender_email or ""])
    addr = (addrs[0][1] if addrs else "").lower().strip()
    if not addr or "@" not in addr:
        return ""
    return addr


def record_dismiss(db: sqlite3.Connection, sender_email: str) -> int:
    """Increment dismiss count for a sender; returns the new count.

    sender_email may be a full From header ('Casey <casey@x.com>') or a bare
    address. We extract and lowercase the address part."""
    addr = _extract_addr(sender_email)
    if not addr:
        return 0
    domain = addr.split("@", 1)[1]
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO learned_promos(sender_email, sender_domain, "
        "dismissed_count, first_dismissed_at, last_dismissed_at) "
        "VALUES (?, ?, 1, ?, ?) "
        "ON CONFLICT(sender_email) DO UPDATE SET "
        "dismissed_count = dismissed_count + 1, last_dismissed_at = excluded.last_dismissed_at",
        (addr, domain, now, now),
    )
    row = db.execute(
        "SELECT dismissed_count FROM learned_promos WHERE sender_email=?",
        (addr,),
    ).fetchone()
    return int(row[0]) if row else 0


def is_learned_promo(db: sqlite3.Connection, sender_email: str) -> bool:
    """Return True if this sender's address has been Dismissed >= PROMO_THRESHOLD."""
    addr = _extract_addr(sender_email)
    if not addr:
        return False
    row = db.execute(
        "SELECT dismissed_count FROM learned_promos WHERE sender_email=?",
        (addr,),
    ).fetchone()
    if row and int(row[0]) >= PROMO_THRESHOLD:
        return True
    return False
