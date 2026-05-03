from unittest.mock import MagicMock, patch
from hermes.config import GmailConfig
from hermes.mail.imap_client import ImapClient


def _gmail():
    return GmailConfig(
        imap_user="u@gmail.com", imap_app_password="p",
        smtp_user="u@gmail.com", smtp_app_password="p",
        from_address="U <u@gmail.com>", reply_to="U <u@x.com>",
    )


def test_imap_client_connect_logs_in():
    fake_box = MagicMock()
    with patch("hermes.mail.imap_client.MailBox") as mb:
        mb.return_value.login.return_value = fake_box
        ic = ImapClient(_gmail())
        ic.connect()
    mb.assert_called_once_with("imap.gmail.com")
    mb.return_value.login.assert_called_once_with("u@gmail.com", "p", initial_folder="INBOX")


def test_imap_client_apply_label_uses_gmail_extension():
    fake_box = MagicMock()
    ic = ImapClient(_gmail())
    ic._box = fake_box
    ic.apply_label(uid=42, label="Hermes/Promo")
    fake_box.client.uid.assert_called()
    args = fake_box.client.uid.call_args.args
    assert args[0] == "STORE"
    assert "X-GM-LABELS" in args[2]
