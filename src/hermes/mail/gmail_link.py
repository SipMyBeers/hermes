"""Build a Gmail web URL that deep-links to a specific thread.

Gmail's web client supports the URL pattern:
    https://mail.google.com/mail/u/0/#search/rfc822msgid:<id>

The Message-ID does NOT need URL-encoding for the visible-search syntax —
Gmail's frontend handles raw chars in the fragment. We url-encode it anyway
so non-ASCII or angle-bracketed ids don't break.

Caveats:
  - The recipient's Google account must be the FIRST signed-in profile (the
    /u/0/ in the path). If the user has multiple Google accounts, this URL
    might land in the wrong one. We don't fix that today.
  - If the message has been Trashed (e.g. learned-promo flow auto-trashes
    promos), the link won't surface in default search. User can still search
    All Mail.
"""
from __future__ import annotations
from urllib.parse import quote

GMAIL_BASE = "https://mail.google.com/mail/u/0/#search/"


def gmail_url_for(message_id: str) -> str:
    """Return a deep-link URL for the given RFC 822 Message-ID, or empty
    string when the input is unusable (None, empty, our own bootstrap
    sentinel)."""
    if not message_id:
        return ""
    msgid = message_id.strip().strip("<>").strip()
    if not msgid or msgid.startswith("hermes-"):
        return ""
    return f"{GMAIL_BASE}rfc822msgid:{quote(msgid, safe='@.')}"
