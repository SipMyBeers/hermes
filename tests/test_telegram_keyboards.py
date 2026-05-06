from hermes.telegram.keyboards import (
    actions_card, snooze_choices, approval_card, dashboard,
    lead_card, urgent_card, feed_card,
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
    """Telegram's hard limit is 64 bytes for callback_data — protect it.

    feed_card is excluded here because its 'Read' button uses a `url`
    field rather than `callback_data` (Telegram's url-button shape).
    A dedicated test below asserts the url-button shape; the other two
    buttons on feed_card have callback_data and are sanity-checked
    separately."""
    for kb in (actions_card(999_999_999), snooze_choices(999_999_999),
               approval_card(999_999_999), dashboard(),
               lead_card(999_999_999),
               urgent_card(999_999_999, 999_999_999)):
        for row in kb["inline_keyboard"]:
            for btn in row:
                assert len(btn["callback_data"].encode()) <= 64, btn


def test_feed_card_has_url_button_and_save_button():
    """feed_card surfaces three buttons: a url-button for Read (Telegram
    opens the link natively, no callback round-trip), a savefeed callback
    bound to the feed_item id, and a done callback bound to the action id
    that the rounds job created.

    The url-button shape is `{"text": ..., "url": ...}` — distinct from
    the callback shape `{"text": ..., "callback_data": ...}`. They're
    mutually exclusive in Telegram's bot API."""
    kb = feed_card(action_id=7, feed_item_id=42, url="https://example.com/x")
    rows = kb["inline_keyboard"]
    assert len(rows) == 1
    btns = rows[0]
    assert len(btns) == 3
    # Read: url-button shape.
    read_btn = btns[0]
    assert "Read" in read_btn["text"]
    assert read_btn["url"] == "https://example.com/x"
    assert "callback_data" not in read_btn
    # Save: callback bound to the feed_item id so we can look up
    # title + url before writing the bookmark.
    save_btn = btns[1]
    assert "Save" in save_btn["text"]
    assert save_btn["callback_data"] == "savefeed:42"
    assert "url" not in save_btn
    # Dismiss: closes the action row by routing through the existing
    # done verb so the existing dismissal/decoration plumbing handles it.
    dismiss_btn = btns[2]
    assert "Dismiss" in dismiss_btn["text"]
    assert dismiss_btn["callback_data"] == "done:7"


def test_feed_card_callback_buttons_under_64_bytes():
    """The two callback buttons on feed_card stay within Telegram's 64-byte
    limit even at extreme ids. The url button is excluded because the
    URL itself can legitimately exceed 64 bytes."""
    kb = feed_card(
        action_id=999_999_999,
        feed_item_id=999_999_999,
        url="https://example.com/" + "x" * 500,
    )
    for row in kb["inline_keyboard"]:
        for btn in row:
            if "callback_data" in btn:
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


# --- Phase N.2: optional Gmail jump-link row ---------------------------------


def test_actions_card_omits_gmail_row_when_url_empty():
    """Default `gmail_url=""` keeps the original single-row shape so
    every existing call site stays exactly as it was."""
    kb = actions_card(23)
    assert len(kb["inline_keyboard"]) == 1
    kb2 = actions_card(23, gmail_url="")
    assert len(kb2["inline_keyboard"]) == 1


def test_actions_card_appends_gmail_url_button_when_provided():
    """Passing a non-empty Gmail URL adds a second row with one
    url-button (no callback_data) labelled '📬 Open in Gmail'."""
    url = "https://mail.google.com/mail/u/0/#search/rfc822msgid:abc@x"
    kb = actions_card(23, gmail_url=url)
    rows = kb["inline_keyboard"]
    assert len(rows) == 2
    # First row preserved.
    cbs_row1 = [b["callback_data"] for b in rows[0]]
    assert cbs_row1 == ["done:23", "snooze:23", "dismiss:23"]
    # Second row: single url-button.
    assert len(rows[1]) == 1
    btn = rows[1][0]
    assert "Gmail" in btn["text"]
    assert btn["url"] == url
    assert "callback_data" not in btn


def test_urgent_card_appends_gmail_row():
    """Urgent card grows from 2 rows → 3 rows when a Gmail URL is
    provided. The action triad and Draft row are unaffected."""
    url = "https://mail.google.com/mail/u/0/#search/rfc822msgid:abc@x"
    kb = urgent_card(action_id=7, uid=100, gmail_url=url)
    rows = kb["inline_keyboard"]
    assert len(rows) == 3
    # Row 0 + Row 1 unchanged.
    assert [b["callback_data"] for b in rows[0]] == [
        "done:7", "snooze:7", "dismiss:7",
    ]
    assert rows[1][0]["callback_data"] == "draft:100"
    # Row 2: Gmail jump-link.
    assert rows[2][0]["url"] == url
    assert "Gmail" in rows[2][0]["text"]


def test_urgent_card_omits_gmail_row_when_url_empty():
    """No URL → keep the original two-row layout."""
    kb = urgent_card(action_id=7, uid=100)
    assert len(kb["inline_keyboard"]) == 2


def test_lead_card_appends_gmail_row_when_url_provided():
    """The lead-ping card likewise gets a second row when a URL is
    known. Without one, the single-button shape is preserved."""
    bare = lead_card(42)
    assert len(bare["inline_keyboard"]) == 1
    url = "https://mail.google.com/mail/u/0/#search/rfc822msgid:abc@x"
    decorated = lead_card(42, gmail_url=url)
    rows = decorated["inline_keyboard"]
    assert len(rows) == 2
    assert rows[1][0]["url"] == url
