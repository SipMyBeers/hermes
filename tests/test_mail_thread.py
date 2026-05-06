from email import message_from_bytes
from email.message import Message
from hermes.mail.thread import thread_key_for


def _msg(headers: dict[str, str]) -> Message:
    """Build a minimal RFC 5322 message with the given headers."""
    raw = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    raw += "\r\nbody\r\n"
    return message_from_bytes(raw.encode("utf-8"))


def test_thread_key_uses_first_reference():
    """References lists ancestors oldest-first; the first id is the root."""
    msg = _msg({
        "Message-ID": "<reply2@x.com>",
        "In-Reply-To": "<reply1@x.com>",
        "References": "<root@x.com> <reply1@x.com>",
    })
    assert thread_key_for(msg) == "root@x.com"


def test_thread_key_falls_back_to_in_reply_to():
    """No References header -> use In-Reply-To (single-hop fallback)."""
    msg = _msg({
        "Message-ID": "<reply@x.com>",
        "In-Reply-To": "<root@x.com>",
    })
    assert thread_key_for(msg) == "root@x.com"


def test_thread_key_falls_back_to_own_message_id():
    """No References / In-Reply-To -> use the message's own Message-ID
    (thread-of-one — first message in a new conversation)."""
    msg = _msg({
        "Message-ID": "<solo@x.com>",
    })
    assert thread_key_for(msg) == "solo@x.com"


def test_thread_key_empty_when_no_headers():
    """Pathological message with no Message-ID at all -> empty string,
    never None. Caller is responsible for substituting a synthetic key."""
    msg = _msg({"Subject": "no headers"})
    assert thread_key_for(msg) == ""


def test_thread_key_lowercases_and_strips():
    """Message-IDs are case-insensitive and may carry whitespace inside the
    angle brackets — normalize to lowercase + stripped so two writes for
    the same thread hash to the same key."""
    msg = _msg({
        "Message-ID": "< Foo@Bar.COM >",
    })
    assert thread_key_for(msg) == "foo@bar.com"


def test_thread_key_multiple_references_takes_first():
    """RFC 5322 sec 3.6.4: References is oldest-first. The very first id
    is the conversation root, regardless of how many replies have been
    chained on top of it."""
    msg = _msg({
        "Message-ID": "<reply5@x.com>",
        "References": (
            "<root@x.com> <r1@x.com> <r2@x.com> <r3@x.com> <r4@x.com>"
        ),
    })
    assert thread_key_for(msg) == "root@x.com"
