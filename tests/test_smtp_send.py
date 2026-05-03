from email.message import EmailMessage
from unittest.mock import MagicMock, patch
from hermes.config import GmailConfig
from hermes.mail.smtp_client import SmtpSender, build_message


def _gmail() -> GmailConfig:
    return GmailConfig(
        imap_user="u@gmail.com",
        imap_app_password="p",
        smtp_user="u@gmail.com",
        smtp_app_password="p",
        from_address="U <u@gmail.com>",
        reply_to="U <u@ranger-beers.com>",
    )


def test_build_message_sets_headers():
    msg = build_message(
        gmail=_gmail(),
        to_addr="client@example.com",
        subject="Invoice",
        body_text="Hello",
        attachments=[],
    )
    assert msg["From"] == "U <u@gmail.com>"
    assert msg["Reply-To"] == "U <u@ranger-beers.com>"
    assert msg["To"] == "client@example.com"
    assert msg["Subject"] == "Invoice"
    assert msg.get_content().strip() == "Hello"


def test_build_message_sets_message_id():
    msg = build_message(
        gmail=_gmail(),
        to_addr="c@e.com",
        subject="s",
        body_text="b",
        attachments=[],
    )
    mid = msg["Message-ID"]
    assert mid is not None
    assert mid.startswith("<") and mid.endswith(">")
    assert "@ranger-beers.com" in mid


def test_build_message_html_alternative():
    msg = build_message(
        gmail=_gmail(),
        to_addr="c@e.com",
        subject="s",
        body_text="plain",
        attachments=[],
        body_html="<p>html</p>",
    )
    assert msg.is_multipart()
    types = {p.get_content_type() for p in msg.walk()}
    assert "text/plain" in types
    assert "text/html" in types


def test_build_message_attaches_pdf(tmp_path):
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    msg = build_message(
        gmail=_gmail(),
        to_addr="c@e.com",
        subject="s",
        body_text="b",
        attachments=[pdf],
    )
    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "invoice.pdf"
    assert parts[0].get_content_type() == "application/pdf"


def test_smtp_sender_send_calls_smtp(tmp_path):
    sender = SmtpSender(_gmail())
    fake_smtp = MagicMock()
    msg = build_message(_gmail(), "c@e.com", "s", "b", [])
    with patch("hermes.mail.smtp_client.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = fake_smtp
        result = sender.send(msg)
    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once_with("u@gmail.com", "p")
    fake_smtp.send_message.assert_called_once_with(msg)
    assert result.startswith("<") and "@ranger-beers.com" in result
