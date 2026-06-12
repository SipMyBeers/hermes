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


def test_imap_client_move_to_trash_uses_gmail_trash_label():
    """move_to_trash adds \\Trash and removes \\Inbox via Gmail X-GM-LABELS."""
    fake_box = MagicMock()
    ic = ImapClient(_gmail())
    ic._box = fake_box
    ic.move_to_trash(uid=99)
    # Two STORE calls: +Trash then -Inbox
    calls = fake_box.client.uid.call_args_list
    assert len(calls) == 2
    add_args = calls[0].args
    rm_args = calls[1].args
    assert add_args[0] == "STORE"
    assert add_args[1] == "99"
    assert add_args[2] == "+X-GM-LABELS"
    assert "Trash" in add_args[3]
    assert rm_args[0] == "STORE"
    assert rm_args[2] == "-X-GM-LABELS"
    assert "Inbox" in rm_args[3]


class _FakeIdle:
    def __init__(self, responses):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def poll(self, timeout):
        return self._responses


class _FakeBox:
    def __init__(self, responses):
        self.idle = _FakeIdle(responses)


def test_idle_handles_bytes_responses():
    """idle.poll() returns bytes lines from imaplib; idle() must not TypeError."""
    ic = ImapClient(_gmail())
    ic._box = _FakeBox([b"* 42 EXISTS", b"* 1 RECENT"])
    assert ic.idle(timeout=1) is True


def test_idle_handles_str_responses():
    """Defensive: also accept str lines (imap_tools version drift)."""
    ic = ImapClient(_gmail())
    ic._box = _FakeBox(["* 42 EXISTS"])
    assert ic.idle(timeout=1) is True


def test_idle_no_new_mail_returns_false():
    ic = ImapClient(_gmail())
    ic._box = _FakeBox([b"* OK still here"])
    assert ic.idle(timeout=1) is False
