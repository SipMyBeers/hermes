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


def test_skool_domain_classified_as_promo():
    """skool.com is on the newsletter-platform allowlist."""
    msg = _make_msg("Skool <noreply@skool.com>", "New post", "body")
    r = classify(msg, client_domains={}, personal_allowlist=set())
    assert r.label == "promo"
    assert "newsletter platform" in r.reason


def test_substack_domain_classified_as_promo():
    msg = _make_msg("Author <author@substack.com>", "Weekly post", "body")
    r = classify(msg, client_domains={}, personal_allowlist=set())
    assert r.label == "promo"
    assert "newsletter platform" in r.reason


def test_github_notifications_not_classified_as_promo():
    """Regression: notifications@github.com is a real ops signal (CI failures,
    PR reviews) and must NOT be auto-classified as promo by a bare-prefix rule.
    Falls through to the LLM tier."""
    msg = _make_msg("GitHub <notifications@github.com>",
                    "[user/repo] Run failed: Deploy", "stack trace here")
    r = classify(msg, client_domains={}, personal_allowlist=set())
    assert r.label == "unknown"


def test_cointracker_transactional_not_classified_as_promo():
    """Regression: hello@mail.cointracker.io is transactional (portfolio sync
    notifications), not marketing. Must fall through to the LLM tier rather
    than being mis-flagged by a bare-prefix rule."""
    msg = _make_msg("CoinTracker <hello@mail.cointracker.io>",
                    "Crypto portfolio update", "your portfolio synced")
    r = classify(msg, client_domains={}, personal_allowlist=set())
    assert r.label == "unknown"


def test_client_domain_with_noreply_local_still_classifies_as_client():
    """A SaaS-style client that happens to send from noreply@<theirdomain>
    must still be classified as client, not promo."""
    msg = _make_msg("Acme <noreply@acme.example>", "Update", "body")
    hints = {"acme.example": ClientHint(client="acme")}
    r = classify(msg, client_domains=hints, personal_allowlist=set())
    assert r.label == "client"
    assert r.client == "acme"
