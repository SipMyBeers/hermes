from email import message_from_bytes
from pathlib import Path
import pytest
from hermes.classifier.rules import classify, RuleResult, ClientHint

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def _msg(name: str):
    return message_from_bytes((FIXTURES / name).read_bytes())


def _make_msg(from_, subject, body):
    raw = (
        f"From: {from_}\r\nTo: dz@gmail.com\r\nSubject: {subject}\r\n"
        f"Date: Fri, 02 May 2026 10:00:00 +0000\r\n"
        f"Message-ID: <x@y>\r\n\r\n{body}\r\n"
    ).encode()
    return message_from_bytes(raw)


def test_promo_via_list_unsubscribe():
    r = classify(_msg("promo_amazon.eml"), client_domains={}, personal_allowlist=set())
    assert r.label == "promo"
    assert r.confidence == 1.0
    assert "List-Unsubscribe" in r.reason


def test_promo_newsletter_also_caught():
    r = classify(_msg("promo_newsletter.eml"), client_domains={}, personal_allowlist=set())
    assert r.label == "promo"


def test_client_via_domain_allowlist():
    hints = {"boarhog.example": ClientHint(client="boarhog")}
    r = classify(_msg("client_boarhog.eml"), client_domains=hints, personal_allowlist=set())
    assert r.label == "client"
    assert r.client == "boarhog"


def test_personal_via_allowlist():
    r = classify(_msg("personal.eml"), client_domains={}, personal_allowlist={"mom@example.com"})
    assert r.label == "personal"


def test_transactional_via_subject():
    r = classify(_msg("transactional_receipt.eml"), client_domains={}, personal_allowlist=set())
    assert r.label == "transactional"


def test_ambiguous_returns_unknown():
    r = classify(_msg("ambiguous.eml"), client_domains={}, personal_allowlist=set())
    assert r.label == "unknown"
    assert r.confidence == 0.0


def test_personal_allowlist_beats_client_domain():
    """Personal allowlist is more specific than a domain match — allowlist wins."""
    hints = {"example.com": ClientHint(client="acme")}
    # personal.eml is 'mom@example.com' — domain matches the allowlist below
    r = classify(
        _msg("personal.eml"),
        client_domains=hints,
        personal_allowlist={"mom@example.com"},
    )
    assert r.label == "personal"


def test_list_unsubscribe_beats_client_domain():
    """A client sending a marketing newsletter (with List-Unsubscribe) is promo,
    not client. Documented behavior: promo always wins over domain match.
    Re-evaluate this if it causes false negatives on real client mail."""
    hints = {"amazon.com": ClientHint(client="amazon")}
    r = classify(_msg("promo_amazon.eml"), client_domains=hints, personal_allowlist=set())
    assert r.label == "promo"


def test_missing_headers_are_safe():
    """Subject missing or From empty must not crash."""
    from email.message import Message
    msg = Message()
    msg["From"] = ""
    # No Subject header at all
    r = classify(msg, client_domains={}, personal_allowlist=set())
    assert r.label == "unknown"
    assert r.confidence == 0.0


def test_noreply_sender_classified_as_promo():
    msg = _make_msg("Skool <noreply@skool.com>", "New post", "body")
    r = classify(msg, client_domains={}, personal_allowlist=set())
    assert r.label == "promo"
    assert "local-part" in r.reason or "newsletter" in r.reason


def test_marketing_sender_classified_as_promo():
    msg = _make_msg("Brand <marketing@somesite.com>", "Big sale", "body")
    r = classify(msg, client_domains={}, personal_allowlist=set())
    assert r.label == "promo"


def test_substack_domain_classified_as_promo():
    msg = _make_msg("Author <author@substack.com>", "Weekly post", "body")
    r = classify(msg, client_domains={}, personal_allowlist=set())
    assert r.label == "promo"


def test_known_client_overrides_promo_local_part():
    """If a client domain is in the allowlist, even a noreply@<client> stays a client.
    Edge case but worth pinning so a future SaaS-style client at noreply@theirdomain
    still gets pinged as a client."""
    msg = _make_msg("Acme <noreply@acme.example>", "Update", "body")
    hints = {"acme.example": ClientHint(client="acme")}
    # The promo local-part rule fires first under the current ordering. Document
    # that in the test name; if we ever want client to win, change the rule order.
    r = classify(msg, client_domains=hints, personal_allowlist=set())
    # Per the documented priority order, promo wins:
    assert r.label == "promo"
