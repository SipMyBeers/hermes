from hermes.telegram.keyboards import (
    actions_card, snooze_choices, approval_card, dashboard,
)


def test_actions_card_has_three_buttons():
    """One row with Done / Snooze / Dismiss carrying the action id."""
    kb = actions_card(23)
    rows = kb["inline_keyboard"]
    assert len(rows) == 1
    btns = rows[0]
    assert len(btns) == 3
    labels = [b["text"] for b in btns]
    assert any("Done" in l for l in labels)
    assert any("Snooze" in l for l in labels)
    assert any("Dismiss" in l for l in labels)
    callbacks = [b["callback_data"] for b in btns]
    assert "done:23" in callbacks
    assert "snooze:23" in callbacks
    assert "dismiss:23" in callbacks


def test_snooze_choices_has_back_row():
    kb = snooze_choices(7)
    rows = kb["inline_keyboard"]
    assert len(rows) == 2
    durations = [b["callback_data"] for b in rows[0]]
    assert durations == ["snooze:7:1d", "snooze:7:3d", "snooze:7:1w"]
    assert rows[1][0]["callback_data"] == "unsnooze:7"
    assert "Back" in rows[1][0]["text"]


def test_approval_card_send_then_reject():
    kb = approval_card(99)
    rows = kb["inline_keyboard"]
    assert len(rows) == 1
    cbs = [b["callback_data"] for b in rows[0]]
    assert cbs == ["approve:99", "reject:99"]


def test_dashboard_has_actions_and_digest():
    kb = dashboard()
    rows = kb["inline_keyboard"]
    cbs = [btn["callback_data"] for row in rows for btn in row]
    assert "actions" in cbs
    assert "digest" in cbs


def test_callback_data_under_64_bytes():
    """Telegram's hard limit is 64 bytes for callback_data — protect it."""
    for kb in (actions_card(999_999_999), snooze_choices(999_999_999),
               approval_card(999_999_999), dashboard()):
        for row in kb["inline_keyboard"]:
            for btn in row:
                assert len(btn["callback_data"].encode()) <= 64, btn
