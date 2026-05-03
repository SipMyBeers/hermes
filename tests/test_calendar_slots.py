import sys
import pytest
from pathlib import Path
from datetime import datetime
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


def test_next_slots_not_implemented_yet(tmp_path: Path):
    # Use the example file as a stand-in path; the function should raise before
    # touching the file.
    with pytest.raises(NotImplementedError, match="v1.2"):
        next_slots(AVAILABILITY_EXAMPLE)


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
