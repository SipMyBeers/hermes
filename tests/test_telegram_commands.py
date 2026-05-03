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


def test_approve_passes_attachments_through(tmp_path):
    """Regression: invoice /approve must include the PDF attachment."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    db.execute(
        "INSERT INTO outbox (kind, client, to_addr, subject, body, attachments, status, created_at) "
        "VALUES ('invoice', 'boarhog', 'a@b.com', 's', 'b', ?, 'queued', '2026-05-02')",
        (str(pdf),),
    )
    tg = MagicMock()
    import hermes.telegram.commands as cmd
    captured: dict = {}
    def fake_send(cfg, to_addr, subject, body, attachments=None):
        captured["attachments"] = list(attachments or [])
        return True
    cmd._do_send = fake_send
    update = {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/approve 1"}}
    handle_update(update, _cfg(), db, tg)
    assert len(captured["attachments"]) == 1
    assert captured["attachments"][0].name == "invoice.pdf"


def test_parse_attachments_handles_shapes():
    from hermes.telegram.commands import _parse_attachments
    from pathlib import Path
    assert _parse_attachments(None) == []
    assert _parse_attachments("") == []
    assert _parse_attachments("/tmp/x.pdf") == [Path("/tmp/x.pdf")]
    assert _parse_attachments('["/tmp/a.pdf","/tmp/b.pdf"]') == [Path("/tmp/a.pdf"), Path("/tmp/b.pdf")]
    assert _parse_attachments("[malformed") == [Path("[malformed")]  # falls back to single-path


def test_save_command_inserts_bookmark(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 10, "message": {"chat": {"id": 42},
              "text": "/save https://example.com/article reading later"}}
    handle_update(update, _cfg(), db, tg)
    rows = list(db.execute("SELECT url, notes FROM bookmarks"))
    assert rows == [("https://example.com/article", "reading later")]
    tg.send_message.assert_called_once()
    assert "Saved bookmark" in tg.send_message.call_args.args[0]


def test_save_rejects_non_url(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/save justtext"}}
    handle_update(update, _cfg(), db, tg)
    rows = list(db.execute("SELECT COUNT(*) FROM bookmarks"))
    assert rows[0][0] == 0
    assert "doesn't look like a URL" in tg.send_message.call_args.args[0]


def test_save_with_no_args_shows_usage(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 12, "message": {"chat": {"id": 42}, "text": "/save"}}
    handle_update(update, _cfg(), db, tg)
    assert "Usage:" in tg.send_message.call_args.args[0]


def test_bare_url_triggers_read(tmp_path):
    """A plain URL (no /read prefix) should be ingested."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    captured = {}
    def fake_ingest(url, db, source_chat_id=None):
        captured["url"] = url
        from hermes.ingest.url import Digestion
        return Digestion(id=1, url=url, kind="web", title="T", summary="S",
                         actions=(), tools=(), error="")
    import hermes.telegram.commands as cmd
    cmd.ingest = fake_ingest
    update = {"update_id": 50, "message": {"chat": {"id": 42},
              "text": "https://example.com/article"}}
    handle_update(update, _cfg(), db, tg)
    assert captured.get("url") == "https://example.com/article"


def test_read_command_with_no_args_shows_usage(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 51, "message": {"chat": {"id": 42}, "text": "/read"}}
    handle_update(update, _cfg(), db, tg)
    assert "Usage:" in tg.send_message.call_args.args[0]
