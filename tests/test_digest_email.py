"""Morning email digest: HTML build + self-exemption marker."""
from email import message_from_bytes
from unittest.mock import MagicMock
from hermes.digest.email_job import build_digest_email


def _db(tmp_path):
    from hermes.db import connect, migrate
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    return conn


def _seed(db):
    from hermes.pipeline.store import upsert_lead, set_stage
    upsert_lead(db, "todd@tbn.net", display_name="Todd Rodeghiero", source="partner", project="tbn")
    set_stage(db, "todd@tbn.net", "active")
    upsert_lead(db, "p@dentist.com", display_name="New Prospect", source="form")
    db.execute(
        "INSERT INTO leads(uid, sender, sender_domain, reason, matches, detected_at) "
        "VALUES (1, 'New Prospect <p@dentist.com>', 'dentist.com', 'contact-form submission', 'form', datetime('now'))"
    )
    db.execute(
        "INSERT INTO actions(source_kind, source_id, text, importance, status, created_at) "
        "VALUES ('email', 1, 'Reply to Todd about assets', 'normal', 'open', datetime('now'))"
    )
    db.commit()


def test_build_digest_email_has_sections_and_marker(tmp_path):
    from hermes.config import GmailConfig
    db = _db(tmp_path)
    _seed(db)
    gmail = GmailConfig(
        imap_user="dz@gmail.com", imap_app_password="p",
        smtp_user="dz@gmail.com", smtp_app_password="p",
        from_address="Hermes <dz@gmail.com>", reply_to="D <d@x.com>",
    )
    msg = build_digest_email(db, gmail)
    assert msg["X-Hermes-Digest"] == "1"
    assert msg["To"] == "dz@gmail.com"
    body = msg.as_bytes().decode("utf-8", errors="replace")
    assert "Todd Rodeghiero" in body
    assert "New Prospect" in body
    assert "Reply to Todd about assets" in body


def test_daemon_skips_own_digest(tmp_path, monkeypatch):
    from hermes.mail.daemon import process_message
    from hermes.config import Config, GmailConfig, TelegramConfig, MailConfig
    db = _db(tmp_path)
    cfg = Config(
        gmail=GmailConfig(
            imap_user="u@g.com", imap_app_password="p",
            smtp_user="u@g.com", smtp_app_password="p",
            from_address="U <u@g.com>", reply_to="U <u@x.com>",
        ),
        telegram=TelegramConfig(bot_token="1:A", chat_id=0),
        digest_evening_report=False,
        mail=MailConfig(delete_promos=False),
    )
    raw = (
        b"From: Hermes <u@g.com>\r\n"
        b"To: u@g.com\r\n"
        b"Subject: Morning digest\r\n"
        b"Message-ID: <digest-1@ranger-beers.com>\r\n"
        b"X-Hermes-Digest: 1\r\n"
        b"\r\n"
        b"pipeline stuff\r\n"
    )
    fake_imap = MagicMock()
    fake_tg = MagicMock()
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: fake_tg)
    process_message(uid=900, msg=message_from_bytes(raw), db=db, cfg=cfg,
                    imap=fake_imap, llm_classifier=None, drafter=None)
    labels = [c.args[1] for c in fake_imap.apply_label.call_args_list]
    assert labels == ["Hermes/Digest"]
    fake_imap.star.assert_called_once_with(900)
    fake_imap.archive.assert_not_called()
    fake_tg.send_message.assert_not_called()
