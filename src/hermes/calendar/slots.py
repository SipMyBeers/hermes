"""Compute next available time slots from availability.toml."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

_WEEKDAY_KEYS = ["monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday", "sunday"]


@dataclass(frozen=True)
class TimeSlot:
    start: datetime
    end: datetime


def _parse_hhmm(s: str) -> time:
    h, m = s.strip().split(":", 1)
    return time(int(h), int(m))


def _windows_for_weekday(cfg: dict, weekday: int) -> list[tuple[time, time]]:
    """Returns the list of (start, end) windows for the given weekday (0=Mon).

    Empty / missing days get an empty list — the walker just moves on to the
    next day.
    """
    key = _WEEKDAY_KEYS[weekday]
    raw = cfg.get(key, [])
    out: list[tuple[time, time]] = []
    for span in raw:
        if "-" not in span:
            continue
        s, e = span.split("-", 1)
        try:
            out.append((_parse_hhmm(s), _parse_hhmm(e)))
        except (ValueError, IndexError):
            # Malformed entry — skip rather than blow up the whole walk.
            continue
    return out


def next_slots(
    availability_path: Path,
    n: int = 3,
    after: datetime | None = None,
    duration_minutes: int = 30,
) -> list[TimeSlot]:
    """Read availability.toml, compute the next `n` open slots starting after
    `after` (default: now in the configured timezone).

    Slots are `duration_minutes` long; a window of length W produces
    floor(W / duration_minutes) slots. Today's first slot snaps forward to
    the next slot-aligned boundary >= `after`; later days start at the
    window's beginning.

    The walker bails after 14 days to avoid an infinite loop on a fully
    empty config. Returns `[]` if the file is missing, unreadable, or
    the TOML is malformed.

    No external calendar (Google Calendar) integration — that's v1.3.
    """
    availability_path = Path(availability_path)
    if not availability_path.exists():
        return []
    try:
        with availability_path.open("rb") as f:
            cfg = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    tz_name = str(cfg.get("timezone", "UTC"))
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    windows_cfg = cfg.get("windows", {}) or {}

    if after is None:
        after = datetime.now(tz)
    elif after.tzinfo is None:
        # Naive input is interpreted in the configured timezone — that's
        # the common case for tests passing literal datetimes.
        after = after.replace(tzinfo=tz)
    else:
        after = after.astimezone(tz)

    slots: list[TimeSlot] = []
    dur = timedelta(minutes=duration_minutes)
    day_offset = 0
    # Walk forward up to 14 days; bail early once we have n slots. 14 days
    # is plenty even for a sparse weekly schedule (one window/week → 2
    # slots in 14 days), and caps the loop on an empty config.
    while len(slots) < n and day_offset < 14:
        cursor = after + timedelta(days=day_offset)
        d = cursor.date()
        weekday = cursor.weekday()
        for win_start, win_end in _windows_for_weekday(windows_cfg, weekday):
            slot_start = datetime.combine(d, win_start, tzinfo=tz)
            slot_end_window = datetime.combine(d, win_end, tzinfo=tz)
            # Snap today's first slot forward to the next slot boundary
            # >= `after`. Later days start at the window's open.
            if day_offset == 0 and slot_start < after:
                if after >= slot_end_window:
                    # Whole window is in the past — skip it.
                    continue
                delta_min = (after - slot_start).total_seconds() / 60.0
                bumps = int(delta_min // duration_minutes) + 1
                slot_start = slot_start + timedelta(
                    minutes=bumps * duration_minutes,
                )
            while slot_start + dur <= slot_end_window and len(slots) < n:
                slots.append(TimeSlot(start=slot_start, end=slot_start + dur))
                slot_start += dur
            if len(slots) >= n:
                break
        day_offset += 1
    return slots[:n]
