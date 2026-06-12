"""Resolve which client a partner (relationship-holder) thread belongs to.

Partners like uncle Josh CC/forward client mail. The sender alone says
nothing about the project, so we look at (in priority order):

  1. participant addresses (To/Cc/From) matching a client contact or domain;
  2. an original-sender line inside a forwarded block in the body;
  3. the client key or bill_to_company appearing in the subject.

Deterministic and cheap — no LLM. A miss returns None and the caller falls
back to review/lead handling.
"""
from __future__ import annotations
import re
from email.message import Message
from email.utils import getaddresses
from typing import Mapping

from hermes.config import ClientConfig

_BODY_SCAN_CHARS = 3000
# Addresses in a forwarded block: "From: Max Crofton <maxcrofton@gmail.com>"
_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _body_head(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(
                    part.get_content_charset() or "utf-8", errors="replace"
                )[:_BODY_SCAN_CHARS]
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")[
        :_BODY_SCAN_CHARS
    ]


def _match_addr(addr: str, clients: Mapping[str, ClientConfig]) -> str | None:
    addr = addr.lower()
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    for key, c in clients.items():
        if c.contact_email and addr == c.contact_email.lower():
            return key
        if c.domain and domain == c.domain.lower():
            return key
    return None


def infer_client(msg: Message, clients: Mapping[str, ClientConfig]) -> str | None:
    """Return the client key this partner thread belongs to, or None."""
    # 1. Participants.
    # NB: getaddresses() in Python 3.12 strict mode collapses the whole
    # result to [('','')] if ANY list element is an empty string — filter first.
    participant_headers = [str(msg.get(h, "")) for h in ("To", "Cc", "From")]
    participant_headers = [h for h in participant_headers if h]
    for _, addr in getaddresses(participant_headers):
        if not addr:
            continue
        hit = _match_addr(addr, clients)
        if hit:
            return hit

    # 2. Addresses inside the body head (forwarded blocks).
    for addr in _ADDR_RE.findall(_body_head(msg)):
        hit = _match_addr(addr, clients)
        if hit:
            return hit

    # 3. Subject tokens: client key or bill_to_company, word-bounded.
    subject = str(msg.get("Subject", "") or "")
    for key, c in clients.items():
        tokens = [key]
        if c.bill_to_company:
            tokens.append(c.bill_to_company)
        for token in tokens:
            if re.search(rf"\b{re.escape(token)}\b", subject, re.IGNORECASE):
                return key
    return None
