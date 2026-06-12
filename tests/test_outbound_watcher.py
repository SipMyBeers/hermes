"""Sent-folder watcher: file Dylan's outbound mail under client labels."""
from email.message import EmailMessage
from unittest.mock import MagicMock, call
from hermes.config import Config, GmailConfig, TelegramConfig, ClientConfig
from hermes.mail.outbound import process_sent


def _cfg() -> Config:
    return Config(
        gmail=GmailConfig(
            imap_user="u@g.com", imap_app_password="p",
            smtp_user="u@g.com", smtp_app_password="p",
            from_address="U <u@g.com>", reply_to="U <u@x.com>",
        ),
        telegram=TelegramConfig(bot_token="1:A", chat_id=0),
        digest_evening_report=False,
        clients={
            "tbn": ClientConfig(
                name="tbn", domain="tbn.net", contact_email="todd@tbn.net", active=True,
            ),
            "maxcrofton": ClientConfig(
                name="maxcrofton", contact_email="maxcrofton@gmail.com", active=True,
            ),
        },
    )


def _db(tmp_path):
    from hermes.db import connect, migrate
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    return conn


class _Fetched:
    def __init__(self, uid, to):
        self.uid = uid
        m = EmailMessage()
        m["From"] = "U <u@g.com>"
        m["To"] = to
        m["Subject"] = "re: stuff"
        m.set_content("hi")
        self.obj = m


def test_first_run_anchors_without_processing(tmp_path):
    from hermes.db import kv_get
    db = _db(tmp_path)
    imap = MagicMock()
    imap.current_max_uid.return_value = 500
    n = process_sent(db, _cfg(), imap)
    assert n == 0
    imap.apply_label.assert_not_called()
    assert kv_get(db, "sent_anchor", "0") == "500"
    # folder switched to Sent and restored to INBOX
    imap.set_folder.assert_has_calls([call("[Gmail]/Sent Mail"), call("INBOX")])


def test_outbound_to_client_gets_filed(tmp_path):
    from hermes.db import kv_set, kv_get
    db = _db(tmp_path)
    kv_set(db, "sent_anchor", "500")
    imap = MagicMock()
    imap.fetch_recent.return_value = iter([
        _Fetched(501, "Todd <todd@tbn.net>"),
        _Fetched(502, "stranger@nowhere.com"),
        _Fetched(503, "Max Crofton <maxcrofton@gmail.com>"),
    ])
    n = process_sent(db, _cfg(), imap)
    assert n == 2
    imap.apply_label.assert_has_calls([
        call(501, "Hermes/Client/tbn"),
        call(503, "Hermes/Client/maxcrofton"),
    ])
    assert kv_get(db, "sent_anchor", "0") == "503"


def test_folder_restored_on_exception(tmp_path):
    from hermes.db import kv_set
    db = _db(tmp_path)
    kv_set(db, "sent_anchor", "500")
    imap = MagicMock()
    imap.fetch_recent.side_effect = RuntimeError("imap blew up")
    try:
        process_sent(db, _cfg(), imap)
    except RuntimeError:
        pass
    imap.set_folder.assert_has_calls([call("[Gmail]/Sent Mail"), call("INBOX")])
