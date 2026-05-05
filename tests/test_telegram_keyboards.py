from hermes.telegram.keyboards import (
    actions_card, snooze_choices, approval_card, dashboard,
    lead_card, urgent_card,
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
               approval_card(999_999_999), dashboard(),
               lead_card(999_999_999),
               urgent_card(999_999_999, 999_999_999)):
        for row in kb["inline_keyboard"]:
            for btn in row:
                assert len(btn["callback_data"].encode()) <= 64, btn


def test_lead_card_has_draft_button():
    """Single-row card with one Draft-reply button bound to a uid."""
    kb = lead_card(42)
    rows = kb["inline_keyboard"]
    assert len(rows) == 1
    assert len(rows[0]) == 1
    btn = rows[0][0]
    assert "Draft" in btn["text"]
    assert btn["callback_data"] == "draft:42"


def test_urgent_card_has_action_row_and_draft_row():
    """Two-row layout: action triad + Draft button. Action id and uid
    bind to different parts of the payload (callback verbs)."""
    kb = urgent_card(action_id=7, uid=100)
    rows = kb["inline_keyboard"]
    assert len(rows) == 2
    # Row 1: Done / Snooze / Dismiss bound to action id.
    cbs_row1 = [b["callback_data"] for b in rows[0]]
    assert cbs_row1 == ["done:7", "snooze:7", "dismiss:7"]
    # Row 2: single Draft button bound to message uid.
    assert len(rows[1]) == 1
    assert rows[1][0]["callback_data"] == "draft:100"
