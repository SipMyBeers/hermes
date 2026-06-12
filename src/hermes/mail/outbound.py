"""Sent-folder watcher: file Dylan's outbound mail under client labels.

Called once per daemon cycle. Switches the IMAP connection to the Sent
folder, labels anything addressed to a known client, advances a kv anchor,
and always restores the INBOX folder. First run anchors to the current max
Sent UID and processes nothing — no backlog spam.

Phase 2 hook: this is also where outbound updates pipeline.last_outbound.
"""
from __future__ import annotations
import sqlite3
from email.utils import getaddresses
from loguru import logger

from hermes.config import Config
from hermes.db import kv_get, kv_set
from hermes.mail.imap_client import ImapClient

SENT_FOLDER = "[Gmail]/Sent Mail"
_ANCHOR_KEY = "sent_anchor"


def _client_for_recipients(msg, cfg: Config) -> str | None:
    # NB: getaddresses() in Python 3.12 strict mode collapses the whole
    # result to [('','')] if ANY list element is an empty string — filter first.
    headers = [str(msg.get(h, "")) for h in ("To", "Cc", "Bcc")]
    headers = [h for h in headers if h]
    for _, addr in getaddresses(headers):
        addr = addr.lower()
        if not addr:
            continue
        domain = addr.split("@", 1)[1] if "@" in addr else ""
        for key, c in cfg.clients.items():
            if c.contact_email and addr == c.contact_email.lower():
                return key
            if c.domain and domain == c.domain.lower():
                return key
    return None


def process_sent(db: sqlite3.Connection, cfg: Config, imap: ImapClient) -> int:
    """Label new Sent mail addressed to known clients. Returns count filed."""
    filed = 0
    imap.set_folder(SENT_FOLDER)
    try:
        anchor = int(kv_get(db, _ANCHOR_KEY, "0"))
        if anchor == 0:
            top = imap.current_max_uid()
            kv_set(db, _ANCHOR_KEY, str(top))
            logger.info("sent watcher: first run, anchored at uid={}", top)
            return 0
        max_seen = anchor
        for fetched in imap.fetch_recent(since_uid=anchor, limit=100):
            if fetched.uid is None:
                continue
            uid = int(fetched.uid)
            max_seen = max(max_seen, uid)
            client = _client_for_recipients(fetched.obj, cfg)
            if client:
                try:
                    imap.apply_label(uid, f"Hermes/Client/{client}")
                    filed += 1
                    logger.debug("sent watcher: filed uid={} -> {}", uid, client)
                except Exception as e:
                    logger.error("sent watcher: label failed uid={} err={}", uid, e)
        if max_seen != anchor:
            kv_set(db, _ANCHOR_KEY, str(max_seen))
    finally:
        imap.set_folder("INBOX")
    return filed
