"""Watchdog: alert + kickstart when hermes-mail's heartbeat goes stale."""
from datetime import datetime, timedelta, timezone


def _db(tmp_path):
    from hermes.db import connect, migrate
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    return conn


def _set_beat(conn, age_minutes: int):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    conn.execute(
        "INSERT INTO heartbeats (component, ts) VALUES ('hermes-mail', ?) "
        "ON CONFLICT(component) DO UPDATE SET ts=excluded.ts", (ts,))
    conn.commit()


def test_fresh_heartbeat_no_alert(tmp_path):
    from hermes.watchdog import check
    conn = _db(tmp_path)
    _set_beat(conn, age_minutes=5)
    sent = []
    assert check(conn, notify=sent.append, kickstart=lambda: None, max_age_minutes=60) is True
    assert sent == []


def test_stale_heartbeat_alerts_and_kickstarts(tmp_path):
    from hermes.watchdog import check
    conn = _db(tmp_path)
    _set_beat(conn, age_minutes=90)
    sent, kicks = [], []
    assert check(conn, notify=sent.append, kickstart=lambda: kicks.append(1), max_age_minutes=60) is False
    assert len(sent) == 1 and "hermes-mail" in sent[0] and len(kicks) == 1


def test_missing_heartbeat_row_alerts(tmp_path):
    from hermes.watchdog import check
    conn = _db(tmp_path)
    sent = []
    assert check(conn, notify=sent.append, kickstart=lambda: None, max_age_minutes=60) is False
    assert len(sent) == 1


def test_notify_failure_still_kickstarts(tmp_path):
    from hermes.watchdog import check
    conn = _db(tmp_path)
    _set_beat(conn, age_minutes=90)
    kicks = []

    def broken_notify(_):
        raise RuntimeError("telegram down")

    assert check(conn, notify=broken_notify, kickstart=lambda: kicks.append(1), max_age_minutes=60) is False
    assert len(kicks) == 1
