"""Compute a stable thread_key from RFC 5322 headers.

A 'thread' here = all messages connected by In-Reply-To / References /
Message-ID chains. The thread_key is the root Message-ID of the chain,
falling back to the message's own Message-ID for thread-of-one.

This works without any Gmail-specific extensions (no X-GM-THRID fetch
required), so it's portable across IMAP providers.
"""
from __future__ import annotations
from email.message import Message
import re

# Message-IDs in headers can be ' < x@y > ' or '<x@y> <a@b>' for References.
_MSGID_RE = re.compile(r"<([^>]+)>")


def _extract_msgids(header_value: str | None) -> list[str]:
    if not header_value:
        return []
    return [m.lower().strip() for m in _MSGID_RE.findall(str(header_value))]


def thread_key_for(msg: Message) -> str:
    """Return the canonical thread_key for this message.

    Priority:
      1. First id in References (the conversation root, per RFC 5322 sec 3.6.4 —
         References lists ancestors oldest-first).
      2. In-Reply-To (single-hop fallback when no References).
      3. Own Message-ID (a thread-of-one).
      4. Empty string if all are missing (shouldn't happen, but be safe).
    """
    refs = _extract_msgids(msg.get("References"))
    if refs:
        return refs[0]   # First in References is the root.
    irt = _extract_msgids(msg.get("In-Reply-To"))
    if irt:
        return irt[0]
    own = _extract_msgids(msg.get("Message-ID"))
    if own:
        return own[0]
    return ""
