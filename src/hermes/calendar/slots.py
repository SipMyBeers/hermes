"""Compute next available time slots from availability.toml."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class TimeSlot:
    start: datetime
    end: datetime


def next_slots(
    availability_path: Path,
    n: int = 3,
    after: datetime | None = None,
    duration_minutes: int = 30,
) -> list[TimeSlot]:
    """Read availability.toml and return next `n` open slots.

    Expected availability.toml shape:
        [windows]
        monday    = ["09:00-12:00", "14:00-17:00"]
        tuesday   = ["09:00-12:00"]
        # ... weekday => list of HH:MM-HH:MM ranges
        timezone  = "America/Los_Angeles"

    v1.2 implementation:
      - Parse weekday => time ranges.
      - Walk forward from `after` (default now()), splitting each window
        into duration-sized slots, skipping past-times.
      - Return first `n`.
      - No external calendar (Google Calendar) integration — that's v1.3.
    """
    raise NotImplementedError("v1.2: parse availability.toml + walk windows")
