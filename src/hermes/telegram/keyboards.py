"""Inline keyboard builders. Buttons are 2-column rows max for narrow screens.

callback_data on every button stays well under Telegram's 64-byte limit:
verbs are <=8 chars, ids are integers, optional arg suffixes ('1d', '3d',
'1w', etc) are <=3 chars. The longest realistic payload is 'snooze:9999999:1w'
(~17 bytes) — comfortable headroom.
"""
from __future__ import annotations


def actions_card(action_id: int, gmail_url: str = "") -> dict:
    """Three-button row for an open action: Done, Snooze, Dismiss.

    Phase N.2: when the action came from an email and a Gmail deep-link
    URL is known, append a second 1-button row carrying that URL so the
    user can jump straight into the thread in Gmail web. Empty
    `gmail_url` keeps the back-compat single-row shape — every existing
    caller that passes only `action_id` keeps working unchanged."""
    rows = [[
        {"text": "✅ Done", "callback_data": f"done:{action_id}"},
        {"text": "🌙 Snooze", "callback_data": f"snooze:{action_id}"},
        {"text": "🚫 Dismiss", "callback_data": f"dismiss:{action_id}"},
    ]]
    if gmail_url:
        rows.append([{"text": "📬 Open in Gmail", "url": gmail_url}])
    return {"inline_keyboard": rows}


def snooze_choices(action_id: int) -> dict:
    """Sub-keyboard shown after the user taps the root Snooze button."""
    return {"inline_keyboard": [[
        {"text": "+1d", "callback_data": f"snooze:{action_id}:1d"},
        {"text": "+3d", "callback_data": f"snooze:{action_id}:3d"},
        {"text": "+1w", "callback_data": f"snooze:{action_id}:1w"},
    ], [
        {"text": "← Back", "callback_data": f"unsnooze:{action_id}"},
    ]]}


def approval_card(outbox_id: int) -> dict:
    """Invoice / draft outbox approval — Send or Discard."""
    return {"inline_keyboard": [[
        {"text": "✉️ Send", "callback_data": f"approve:{outbox_id}"},
        {"text": "🗑️ Discard", "callback_data": f"reject:{outbox_id}"},
    ]]}


def dashboard() -> dict:
    """`/start` dashboard — one tap to the action list."""
    return {"inline_keyboard": [[
        {"text": "📋 What do I need to do?", "callback_data": "actions"},
    ], [
        {"text": "📊 Today's digest", "callback_data": "digest"},
    ]]}


def lead_card(uid: int, gmail_url: str = "") -> dict:
    """Single-button card attached to '🆕 New lead' pings.

    Tapping fires the draft callback with the originating message uid so
    the user can go from notification → reviewable Send/Discard card in
    one tap, no slash commands. Phase N.2: optional second row with a
    Gmail jump-link when the message came from a real RFC 822
    Message-ID."""
    rows = [[
        {"text": "✏️ Draft reply", "callback_data": f"draft:{uid}"},
    ]]
    if gmail_url:
        rows.append([{"text": "📬 Open in Gmail", "url": gmail_url}])
    return {"inline_keyboard": rows}


def feed_card(action_id: int, feed_item_id: int, url: str) -> dict:
    """Three-button row for a Phase M.2 feed-item card.

    The 'Read' button is a Telegram url-button — Telegram opens the URL
    natively, no callback round-trip, no DB write. 'Save' stashes the URL
    as a bookmark via the existing bookmarks flow. 'Dismiss' closes the
    matching action row.

    The url-button shape is `{"text": ..., "url": ...}` (no callback_data);
    callback-shaped buttons use `{"text": ..., "callback_data": ...}`. The
    two are mutually exclusive in the Telegram bot API."""
    return {"inline_keyboard": [[
        {"text": "📖 Read", "url": url},
        {"text": "🔖 Save", "callback_data": f"savefeed:{feed_item_id}"},
        {"text": "🚫 Dismiss", "callback_data": f"done:{action_id}"},
    ]]}


def urgent_card(action_id: int, uid: int, gmail_url: str = "") -> dict:
    """Two-row keyboard for urgent inbox pings: action triad + draft.

    The first row is the standard Done/Snooze/Dismiss bound to the
    extracted action id. The second row offers Draft reply bound to the
    message uid so the user can close the email loop without navigating
    away. Two rows keeps the layout readable on narrow screens.

    Phase N.2: when a Gmail deep-link is known, append a third 1-button
    row '📬 Open in Gmail' so the user can pull up the original thread
    in Gmail web with one tap. Back-compat: callers that don't pass
    `gmail_url` still get the original two-row layout."""
    rows = [[
        {"text": "✅ Done", "callback_data": f"done:{action_id}"},
        {"text": "🌙 Snooze", "callback_data": f"snooze:{action_id}"},
        {"text": "🚫 Dismiss", "callback_data": f"dismiss:{action_id}"},
    ], [
        {"text": "✏️ Draft reply", "callback_data": f"draft:{uid}"},
    ]]
    if gmail_url:
        rows.append([{"text": "📬 Open in Gmail", "url": gmail_url}])
    return {"inline_keyboard": rows}
