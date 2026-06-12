"""Pipeline store: lead -> replied -> quoted -> active -> invoiced -> closed."""
import pytest


def _db(tmp_path):
    from hermes.db import connect, migrate
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    return conn


def test_upsert_lead_inserts_and_is_idempotent(tmp_path):
    from hermes.pipeline.store import upsert_lead, board
    db = _db(tmp_path)
    upsert_lead(db, "Max@Gmail.com", display_name="Max Crofton", source="partner")
    upsert_lead(db, "max@gmail.com", display_name="Max Crofton", source="partner")
    rows = board(db)
    assert len(rows) == 1
    assert rows[0]["contact_email"] == "max@gmail.com"
    assert rows[0]["stage"] == "lead"


def test_upsert_does_not_regress_stage(tmp_path):
    from hermes.pipeline.store import upsert_lead, set_stage, board
    db = _db(tmp_path)
    upsert_lead(db, "max@gmail.com", display_name="Max", source="direct")
    set_stage(db, "max@gmail.com", "active")
    upsert_lead(db, "max@gmail.com", display_name="Max", source="direct")
    assert board(db)[0]["stage"] == "active"


def test_touch_outbound_advances_lead_to_replied_only(tmp_path):
    from hermes.pipeline.store import upsert_lead, touch_outbound, set_stage, board
    db = _db(tmp_path)
    upsert_lead(db, "a@b.com", display_name="A", source="direct")
    assert touch_outbound(db, "a@b.com") is True
    assert board(db)[0]["stage"] == "replied"
    set_stage(db, "a@b.com", "active")
    touch_outbound(db, "a@b.com")
    assert board(db)[0]["stage"] == "active"  # no regression
    assert board(db)[0]["last_outbound"] is not None


def test_touch_unknown_contact_is_noop(tmp_path):
    from hermes.pipeline.store import touch_inbound, touch_outbound
    db = _db(tmp_path)
    assert touch_inbound(db, "nobody@x.com") is False
    assert touch_outbound(db, "nobody@x.com") is False


def test_set_stage_by_fragment_and_ambiguity(tmp_path):
    from hermes.pipeline.store import upsert_lead, set_stage
    db = _db(tmp_path)
    upsert_lead(db, "todd@tbn.net", display_name="Todd Rodeghiero", source="partner", project="tbn")
    upsert_lead(db, "max@gmail.com", display_name="Max Crofton", source="partner", project="maxcrofton")
    assert set_stage(db, "todd", "quoted") == "todd@tbn.net"
    with pytest.raises(ValueError):
        set_stage(db, "zzz-nomatch", "active")
    upsert_lead(db, "todd2@tbn.net", display_name="Todd Two", source="direct")
    with pytest.raises(ValueError):
        set_stage(db, "todd", "active")  # ambiguous now


def test_set_stage_validates_stage(tmp_path):
    from hermes.pipeline.store import upsert_lead, set_stage
    db = _db(tmp_path)
    upsert_lead(db, "a@b.com", display_name="A", source="direct")
    with pytest.raises(ValueError):
        set_stage(db, "a@b.com", "bogus-stage")
