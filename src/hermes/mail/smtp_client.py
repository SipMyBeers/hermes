from __future__ import annotations
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
import mimetypes
import smtplib
from loguru import logger
from hermes.config import GmailConfig

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def build_message(
    gmail: GmailConfig,
    to_addr: str,
    subject: str,
    body_text: str,
    attachments: list[Path],
    body_html: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = gmail.from_address
    msg["Reply-To"] = gmail.reply_to
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="ranger-beers.com")
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    for path in attachments:
        ctype, _ = mimetypes.guess_type(str(path))
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )
    return msg


class SmtpSender:
    def __init__(self, gmail: GmailConfig):
        self.gmail = gmail

    def send(self, message: EmailMessage) -> str:
        """Send and return the Message-ID set by build_message().

        Raises smtplib exceptions on connect/auth/send failure.
        """
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(self.gmail.smtp_user, self.gmail.smtp_app_password)
            smtp.send_message(message)
            logger.info("smtp.send to={} subject={!r}", message["To"], message["Subject"])
        return str(message.get("Message-ID") or "")
