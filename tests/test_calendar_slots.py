import sys
import pytest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from hermes.calendar.slots import TimeSlot, next_slots

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]
AVAILABILITY_EXAMPLE = REPO_ROOT / "availability.toml.example"


def test_time_slot_fields():
    s = TimeSlot(start=datetime(2026, 5, 4, 9, 0), end=datetime(2026, 5, 4, 9, 30))
    assert s.start.hour == 9
    assert s.end.minute == 30


def test_availability_example_parses_as_valid_toml():
    """The bundled template must be valid TOML so users can copy-and-edit it."""
    assert AVAILABILITY_EXAMPLE.exists(), f"missing {AVAILABILITY_EXAMPLE}"
    data = tomllib.loads(AVAILABILITY_EXAMPLE.read_text())
    assert data["timezone"] == "America/Los_Angeles"
    assert "windows" in data
    assert isinstance(data["windows"]["monday"], list)
    # Each entry should look like "HH:MM-HH:MM"
    for span in data["windows"]["monday"]:
        assert "-" in span and ":" in span


# --- Phase N.3 walker tests --------------------------------------------------


def _write_avail(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "availability.toml"
    p.write_text(body)
    return p


def test_next_slots_finds_first_three_in_morning_window(tmp_path: Path):
    """A simple Monday morning window should yield three back-to-back
    30-minute slots from the window's start."""
    p = _write_avail(tmp_path, """
timezone = "UTC"
[windows]
monday = ["09:00-12:00"]
""")
    # 2026-05-04 was a Monday.
    after = datetime(2026, 5, 4, 8, 0, tzinfo=ZoneInfo("UTC"))
    slots = next_slots(p, n=3, after=after)
    assert len(slots) == 3
    assert slots[0].start.hour == 9 and slots[0].start.minute == 0
    assert slots[1].start.hour == 9 and slots[1].start.minute == 30
    assert slots[2].start.hour == 10 and slots[2].start.minute == 0
    # Each slot is 30 minutes long.
    assert (slots[0].end - slots[0].start).total_seconds() == 30 * 60


def test_next_slots_skips_already_started_slots_today(tmp_path: Path):
    """`after` lands mid-window on Monday at 09:45; the first slot
    returned is the next slot-aligned boundary, i.e. 10:00."""
    p = _write_avail(tmp_path, """
timezone = "UTC"
[windows]
monday = ["09:00-12:00"]
""")
    after = datetime(2026, 5, 4, 9, 45, tzinfo=ZoneInfo("UTC"))
    slots = next_slots(p, n=3, after=after)
    assert len(slots) == 3
    assert slots[0].start.hour == 10 and slots[0].start.minute == 0


def test_next_slots_walks_to_next_day_when_today_full(tmp_path: Path):
    """If `after` is past the last window on Monday, the walker rolls
    forward to Tuesday 09:00 — not just to the same day's afternoon."""
    p = _write_avail(tmp_path, """
timezone = "UTC"
[windows]
monday = ["09:00-12:00", "14:00-17:00"]
tuesday = ["09:00-12:00"]
""")
    after = datetime(2026, 5, 4, 17, 30, tzinfo=ZoneInfo("UTC"))  # Mon 17:30
    slots = next_slots(p, n=1, after=after)
    assert len(slots) == 1
    # Tuesday 2026-05-05 at 09:00 UTC.
    assert slots[0].start.month == 5 and slots[0].start.day == 5
    assert slots[0].start.hour == 9 and slots[0].start.minute == 0


def test_next_slots_handles_empty_availability_file(tmp_path: Path):
    """A non-existent path returns [] (not a crash)."""
    missing = tmp_path / "does-not-exist.toml"
    assert next_slots(missing) == []


def test_next_slots_handles_malformed_toml(tmp_path: Path):
    """A file that exists but isn't valid TOML returns [] rather than
    propagating the parse error to the drafter."""
    p = _write_avail(tmp_path, "this is not = valid TOML [[[")
    assert next_slots(p) == []


def test_next_slots_no_windows_for_today(tmp_path: Path):
    """Saturday is empty; the walker rolls forward to the next non-empty
    weekday (Monday 09:00 in this fixture)."""
    p = _write_avail(tmp_path, """
timezone = "UTC"
[windows]
monday = ["09:00-12:00"]
saturday = []
sunday = []
""")
    after = datetime(2026, 5, 2, 10, 0, tzinfo=ZoneInfo("UTC"))  # Saturday
    slots = next_slots(p, n=1, after=after)
    assert len(slots) == 1
    # Should land on Monday 2026-05-04 09:00.
    assert slots[0].start.weekday() == 0  # Monday
    assert slots[0].start.hour == 9


def test_next_slots_unknown_timezone_falls_back_to_utc(tmp_path: Path):
    """A bogus timezone name doesn't crash — we fall back to UTC."""
    p = _write_avail(tmp_path, """
timezone = "Mars/Bradbury"
[windows]
monday = ["09:00-12:00"]
""")
    after = datetime(2026, 5, 4, 8, 0, tzinfo=ZoneInfo("UTC"))
    slots = next_slots(p, n=1, after=after)
    assert len(slots) == 1
    assert slots[0].start.tzinfo is not None


def test_next_slots_respects_duration_60_minutes(tmp_path: Path):
    """duration_minutes=60 in a 09:00-12:00 window yields 09:00, 10:00,
    11:00 — three 60-minute slots that fully fit inside the window."""
    p = _write_avail(tmp_path, """
timezone = "UTC"
[windows]
monday = ["09:00-12:00"]
""")
    after = datetime(2026, 5, 4, 8, 0, tzinfo=ZoneInfo("UTC"))
    slots = next_slots(p, n=3, after=after, duration_minutes=60)
    assert len(slots) == 3
    starts = [s.start.hour for s in slots]
    assert starts == [9, 10, 11]
    # Each is 60 minutes long.
    for s in slots:
        assert (s.end - s.start).total_seconds() == 60 * 60


def test_next_slots_default_after_uses_now_in_configured_tz(tmp_path: Path):
    """No explicit `after` should not crash; it defaults to now() in the
    configured timezone, and we always get a slot back within 14 days
    given a reasonable schedule."""
    p = _write_avail(tmp_path, """
timezone = "America/Los_Angeles"
[windows]
monday = ["09:00-12:00"]
tuesday = ["09:00-12:00"]
wednesday = ["09:00-12:00"]
thursday = ["09:00-12:00"]
friday = ["09:00-12:00"]
""")
    slots = next_slots(p, n=1)
    assert len(slots) == 1


def test_next_slots_returns_aware_datetimes(tmp_path: Path):
    """All returned datetimes are timezone-aware so callers can format
    them with %Z without surprises."""
    p = _write_avail(tmp_path, """
timezone = "America/Los_Angeles"
[windows]
monday = ["09:00-12:00"]
""")
    after = datetime(2026, 5, 4, 8, 0)  # naive — should be interpreted in tz
    slots = next_slots(p, n=1, after=after)
    assert len(slots) == 1
    assert slots[0].start.tzinfo is not None
    assert slots[0].end.tzinfo is not None
