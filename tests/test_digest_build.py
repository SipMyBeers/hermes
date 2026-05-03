from datetime import datetime, timedelta, timezone
from pathlib import Path
from hermes.config import Config, GmailConfig, TelegramConfig
from hermes.db import connect, migrate
from hermes.digest.build import build_morning_digest


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


def test_digest_with_no_data(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    text = build_morning_digest(db, _cfg())
    assert "Hermes morning report" in text
    assert "no messages" in text.lower() or "0 promo" in text.lower()


def test_digest_counts_messages_by_class(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (1, "m1", now, "a@b.com", "Hi", "promo", "rule"),
        (2, "m2", now, "c@d.com", "Hi", "promo", "rule"),
        (3, "m3", now, "e@f.com", "Question", "client", "rule"),
    ]
    db.executemany(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    text = build_morning_digest(db, _cfg())
    assert "promo: 2" in text.lower()
    assert "client: 1" in text.lower()


def test_digest_lists_pending_outbox(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO outbox(kind, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 'a@b.com', 'Re: Q', 'b', 'queued', '2026-05-02')"
    )
    text = build_morning_digest(db, _cfg())
    assert "pending approval" in text.lower()
    assert "Re: Q" in text
