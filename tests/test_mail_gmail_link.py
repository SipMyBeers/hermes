"""Tests for hermes.mail.gmail_link.gmail_url_for."""
from hermes.mail.gmail_link import gmail_url_for, GMAIL_BASE


def test_gmail_url_strips_angle_brackets():
    """RFC 822 Message-IDs are typically delivered as '<id@host>'. The
    bracket characters break Gmail's search syntax, so we strip them."""
    url = gmail_url_for("<abc@x>")
    assert url == f"{GMAIL_BASE}rfc822msgid:abc@x"


def test_gmail_url_empty_for_none_or_empty():
    """No id → empty URL. Caller decides whether to render the button."""
    assert gmail_url_for("") == ""
    assert gmail_url_for(None) == ""  # type: ignore[arg-type]
    assert gmail_url_for("   ") == ""
    assert gmail_url_for("<>") == ""


def test_gmail_url_empty_for_hermes_bootstrap():
    """The bootstrap sentinel rows aren't real mail — never link to them."""
    assert gmail_url_for("hermes-bootstrap-44385") == ""
    assert gmail_url_for("hermes-12345") == ""


def test_gmail_url_quotes_unsafe_chars():
    """Spaces, slashes, fragment chars, etc. get percent-encoded so the
    fragment-search URL parses cleanly. '@' and '.' are kept legible."""
    url = gmail_url_for("CAH+/special id@host.com")
    assert url.startswith(f"{GMAIL_BASE}rfc822msgid:")
    # Unsafe chars get encoded.
    assert " " not in url
    assert "/" not in url.split("rfc822msgid:")[1]
    # @ and . preserved for readability.
    assert "@host.com" in url


def test_gmail_url_idempotent():
    """Defensive: round-tripping the output URL through gmail_url_for() again
    must not crash. Real callers won't do this but a misuse shouldn't blow up."""
    first = gmail_url_for("abc@x")
    # Feeding a URL back in is nonsense but should not raise.
    second = gmail_url_for(first)
    assert isinstance(second, str)
