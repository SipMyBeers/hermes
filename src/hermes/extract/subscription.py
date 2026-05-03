"""Detect recurring subscription charges from the ledger.

A subscription is N+ ledger.in rows from the same vendor within roughly
monthly cadence. Surfaces total monthly burn and stale subs (last
charge >45 days ago)."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping


@dataclass(frozen=True)
class Subscription:
    vendor: str
    cadence: str                     # 'monthly' | 'annual' | 'irregular'
    amount_cents: int                # most recent charge
    monthly_estimate_cents: int      # normalized to monthly
    occurrences: int                 # count of charges seen
    first_seen_at: datetime
    last_charge_at: datetime
    status: str                      # 'active' | 'stale'


def detect_subscriptions(ledger_rows: Iterable[Mapping]) -> list[Subscription]:
    """Group ledger 'in' rows by vendor and infer cadence.

    v1.2 implementation:
      - Group by canonicalized vendor name.
      - >=2 charges within 25-35 days => 'monthly'; ~365d => 'annual'; else 'irregular'.
      - Status = 'stale' if last_charge_at older than 45 days for monthly,
        450 days for annual.
      - monthly_estimate_cents normalizes annual amount/12.
    """
    raise NotImplementedError("v1.2: vendor canonicalization + cadence detection")
