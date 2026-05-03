from pathlib import Path
from unittest.mock import MagicMock
from hermes.config import Config, GmailConfig, TelegramConfig
from hermes.db import connect, migrate
from hermes.telegram.commands import handle_update


def _cfg():
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


def test_status_command(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/status"}}
    handle_update(update, _cfg(), db, tg)
    tg.send_message.assert_called_once()
    text = tg.send_message.call_args.args[0]
    assert "Hermes" in text
    assert "messages" in text.lower()


def test_unknown_command_replies_help(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/wat"}}
    handle_update(update, _cfg(), db, tg)
    tg.send_message.assert_called_once()
    assert "/status" in tg.send_message.call_args.args[0]


def test_approve_marks_outbox_sent(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO outbox (kind, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 'a@b.com', 's', 'b', 'queued', '2026-05-02')"
    )
    tg = MagicMock()
    # Patch the actual sender to avoid SMTP
    import hermes.telegram.commands as cmd
    cmd._do_send = MagicMock(return_value=True)
    update = {"update_id": 3, "message": {"chat": {"id": 42}, "text": "/approve 1"}}
    handle_update(update, _cfg(), db, tg)
    row = db.execute("SELECT status FROM outbox WHERE id=1").fetchone()
    assert row[0] == "sent"


def test_other_chat_id_ignored(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 4, "message": {"chat": {"id": 99}, "text": "/status"}}
    handle_update(update, _cfg(), db, tg)
    tg.send_message.assert_not_called()
