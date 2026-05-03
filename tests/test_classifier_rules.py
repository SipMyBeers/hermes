from email import message_from_bytes
from pathlib import Path
import pytest
from hermes.classifier.rules import classify, RuleResult, ClientHint

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def _msg(name: str):
    return message_from_bytes((FIXTURES / name).read_bytes())


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
