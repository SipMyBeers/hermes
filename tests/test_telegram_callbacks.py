from unittest.mock import MagicMock
from hermes.config import Config, GmailConfig, TelegramConfig
from hermes.db import connect, migrate
from hermes.telegram.commands import handle_callback


def _cfg() -> Config:
    return Config(
        gmail=GmailConfig(
            imap_user="u@g.com", imap_app_password="p",
            smtp_user="u@g.com", smtp_app_password="p",
            from_address="U <u@g.com>", reply_to="U <u@x.com>",
        ),
        telegram=TelegramConfig(bot_token="1:A", chat_id=42),
        digest_evening_report=False,
        clients={},
    )


def _cb(data: str, *, chat_id: int = 42, msg_id: int = 555,
        text: str = "Reply to Casey") -> dict:
    """Build a callback_query update."""
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "message": {
                "message_id": msg_id,
                "chat": {"id": chat_id},
                "text": text,
            },
        },
    }


def _seed_action(db, text="Reply to Casey", source_kind="email", source_id=1):
    from hermes.actions.store import add_actions
    return add_actions(db, source_kind=source_kind, source_id=source_id,
                       texts=[text])[0]


def test_callback_done_marks_action(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    aid = _seed_action(db)
    tg = MagicMock()
    handle_callback(_cb(f"done:{aid}"), _cfg(), db, tg)
    row = db.execute("SELECT status FROM actions WHERE id=?", (aid,)).fetchone()
    assert row[0] == "done"
    tg.answer_callback_query.assert_called_once()
    flash = tg.answer_callback_query.call_args.args[1]
    assert "done" in flash.lower()
    # Message edited to show the new state, with no keyboard left.
    tg.edit_message_text.assert_called_once()
    edit_kwargs = tg.edit_message_text.call_args.kwargs
    assert edit_kwargs.get("reply_markup") is None
    assert "✓" in edit_kwargs["text"]


def test_callback_done_on_already_closed_flashes_no_edit(tmp_path):
    """A second tap on an already-done action gets a flash but no edit."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    aid = _seed_action(db)
    db.execute("UPDATE actions SET status='done' WHERE id=?", (aid,))
    tg = MagicMock()
    handle_callback(_cb(f"done:{aid}"), _cfg(), db, tg)
    flash = tg.answer_callback_query.call_args.args[1]
    assert "already closed" in flash
    tg.edit_message_text.assert_not_called()


def test_callback_dismiss_marks_dismissed(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    aid = _seed_action(db)
    tg = MagicMock()
    handle_callback(_cb(f"dismiss:{aid}"), _cfg(), db, tg)
    row = db.execute("SELECT status FROM actions WHERE id=?", (aid,)).fetchone()
    assert row[0] == "dismissed"
    tg.edit_message_text.assert_called_once()


def test_callback_snooze_root_offers_choices_no_db_write(tmp_path):
    """First tap on Snooze opens the sub-keyboard but does not change state."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    aid = _seed_action(db)
    tg = MagicMock()
    handle_callback(_cb(f"snooze:{aid}"), _cfg(), db, tg)
    row = db.execute("SELECT status FROM actions WHERE id=?", (aid,)).fetchone()
    assert row[0] == "open"  # untouched
    tg.edit_message_text.assert_called_once()
    kb = tg.edit_message_text.call_args.kwargs["reply_markup"]
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r]
    assert f"snooze:{aid}:1d" in cbs
    assert f"snooze:{aid}:3d" in cbs
    assert f"snooze:{aid}:1w" in cbs
    assert f"unsnooze:{aid}" in cbs


def test_callback_snooze_with_duration_writes_db(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    aid = _seed_action(db)
    tg = MagicMock()
    handle_callback(_cb(f"snooze:{aid}:3d"), _cfg(), db, tg)
    row = db.execute(
        "SELECT status, snoozed_until FROM actions WHERE id=?", (aid,)
    ).fetchone()
    assert row[0] == "snoozed"
    assert row[1] is not None
    flash = tg.answer_callback_query.call_args.args[1]
    assert "3d" in flash
    # Card was decorated to its terminal state, keyboard removed.
    edit_kwargs = tg.edit_message_text.call_args.kwargs
    assert edit_kwargs.get("reply_markup") is None
    assert "🌙" in edit_kwargs["text"]


def test_callback_snooze_strips_pick_a_snooze_line(tmp_path):
    """When the duration is chosen the card text should not retain the
    intermediate '_pick a snooze:_' line that the sub-keyboard added."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    aid = _seed_action(db)
    tg = MagicMock()
    update = _cb(f"snooze:{aid}:1d",
                 text="Reply to Casey\n\n_pick a snooze:_")
    handle_callback(update, _cfg(), db, tg)
    edit_text = tg.edit_message_text.call_args.kwargs["text"]
    assert "_pick a snooze:_" not in edit_text
    assert "snoozed until" in edit_text


def test_callback_unsnooze_restores_original_card(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    aid = _seed_action(db)
    tg = MagicMock()
    update = _cb(f"unsnooze:{aid}",
                 text="Reply to Casey\n\n_pick a snooze:_")
    handle_callback(update, _cfg(), db, tg)
    edit_text = tg.edit_message_text.call_args.kwargs["text"]
    assert "_pick a snooze:_" not in edit_text
    kb = tg.edit_message_text.call_args.kwargs["reply_markup"]
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r]
    assert f"done:{aid}" in cbs
    assert f"snooze:{aid}" in cbs
    assert f"dismiss:{aid}" in cbs


def test_callback_approve_invokes_approve_handler(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO outbox (kind, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 'a@b.com', 's', 'b', 'queued', '2026-05-02')"
    )
    tg = MagicMock()
    import hermes.telegram.commands as cmd
    cmd._do_send = MagicMock(return_value=True)
    handle_callback(_cb("approve:1", text="Invoice ready"), _cfg(), db, tg)
    row = db.execute("SELECT status FROM outbox WHERE id=1").fetchone()
    assert row[0] == "sent"
    flash = tg.answer_callback_query.call_args.args[1]
    assert "send" in flash.lower()


def test_callback_reject_invokes_reject_handler(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO outbox (kind, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 'a@b.com', 's', 'b', 'queued', '2026-05-02')"
    )
    tg = MagicMock()
    handle_callback(_cb("reject:1"), _cfg(), db, tg)
    row = db.execute("SELECT COUNT(*) FROM outbox").fetchone()
    assert row[0] == 0  # /reject deletes


def test_callback_actions_runs_actions_command(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    handle_callback(_cb("actions"), _cfg(), db, tg)
    # No actions seeded -> "No open actions" message sent.
    assert tg.send_message.call_count >= 1
    flash = tg.answer_callback_query.call_args.args[1]
    assert flash == ""


def test_callback_other_chat_id_ignored(tmp_path):
    """Callback from a chat we don't own gets a 'not allowed' flash and no
    DB writes / edits."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    aid = _seed_action(db)
    tg = MagicMock()
    handle_callback(_cb(f"done:{aid}", chat_id=99), _cfg(), db, tg)
    row = db.execute("SELECT status FROM actions WHERE id=?", (aid,)).fetchone()
    assert row[0] == "open"  # untouched
    tg.edit_message_text.assert_not_called()
    tg.answer_callback_query.assert_called_once()
    # Other-chat path passes flash as kwarg (text=), not positional.
    call = tg.answer_callback_query.call_args
    flash = call.kwargs.get("text") or (call.args[1] if len(call.args) > 1 else "")
    assert "not allowed" in flash


def test_callback_unknown_verb_acks_with_question(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    handle_callback(_cb("nonsense:1"), _cfg(), db, tg)
    flash = tg.answer_callback_query.call_args.args[1]
    assert flash == "?"


def test_decorate_does_not_double_prefix(tmp_path):
    """If the card already has a ✓ prefix (rare race), don't stack a second one."""
    from hermes.telegram.commands import _decorate
    tg = MagicMock()
    msg = {"message_id": 1, "text": "✓ Reply to Casey"}
    _decorate(tg, msg, prefix="✓ ", suffix="\n\n_done_", remove_keyboard=True)
    edit_text = tg.edit_message_text.call_args.kwargs["text"]
    assert edit_text.count("✓") == 1
