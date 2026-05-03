from __future__ import annotations
from contextlib import contextmanager
from typing import Iterator
from imap_tools import MailBox, AND
from loguru import logger
from hermes.config import GmailConfig

IMAP_HOST = "imap.gmail.com"


class ImapClient:
    def __init__(self, gmail: GmailConfig):
        self.gmail = gmail
        self._box: MailBox | None = None

    def connect(self) -> None:
        box = MailBox(IMAP_HOST).login(
            self.gmail.imap_user, self.gmail.imap_app_password, initial_folder="INBOX"
        )
        self._box = box
        logger.info("imap connected as {}", self.gmail.imap_user)

    def disconnect(self) -> None:
        if self._box:
            try:
                self._box.logout()
            except Exception as e:
                logger.warning("imap logout error: {}", e)
        self._box = None

    def fetch_recent(self, since_uid: int = 0, limit: int = 100) -> Iterator:
        assert self._box, "call connect() first"
        criteria = AND(uid=f"{since_uid + 1}:*") if since_uid else AND(all=True)
        for msg in self._box.fetch(criteria, mark_seen=False, limit=limit):
            yield msg

    def current_max_uid(self) -> int:
        """Return the highest UID currently in the watched folder, or 0 if empty.
        Used to anchor first-run state without fetching the whole backlog."""
        assert self._box, "call connect() first"
        typ, data = self._box.client.uid("SEARCH", "ALL")
        if typ != "OK" or not data or not data[0]:
            return 0
        uids = data[0].decode().split()
        return max((int(u) for u in uids), default=0)

    def apply_label(self, uid: int, label: str) -> None:
        """Apply a Gmail label via the X-GM-LABELS IMAP extension."""
        assert self._box, "call connect() first"
        # Gmail-specific: STORE +X-GM-LABELS
        self._box.client.uid("STORE", str(uid), "+X-GM-LABELS", f'"{label}"')
        logger.debug("imap label uid={} label={}", uid, label)

    def archive(self, uid: int) -> None:
        assert self._box, "call connect() first"
        # Remove \\Inbox label = archive
        self._box.client.uid("STORE", str(uid), "-X-GM-LABELS", '"\\\\Inbox"')

    def idle(self, timeout: int = 29 * 60) -> bool:
        """Block until new mail or timeout. Returns True if new mail arrived."""
        assert self._box, "call connect() first"
        # imap_tools idle context
        with self._box.idle as idle:
            responses = idle.poll(timeout=timeout)
        return any("EXISTS" in r for r in responses)


@contextmanager
def imap_session(gmail: GmailConfig) -> Iterator[ImapClient]:
    client = ImapClient(gmail)
    client.connect()
    try:
        yield client
    finally:
        client.disconnect()
