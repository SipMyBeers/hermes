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
