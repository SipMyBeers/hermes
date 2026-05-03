from email import message_from_bytes
from pathlib import Path
import pytest
from hermes.extract.lead import detect_lead, LeadSignal

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def _make_msg(from_, subject, body):
    raw = (
        f"From: {from_}\r\nTo: dz@gmail.com\r\nSubject: {subject}\r\n"
        f"Date: Fri, 02 May 2026 10:00:00 +0000\r\n"
        f"Message-ID: <x@y>\r\n\r\n{body}\r\n"
    ).encode()
    return message_from_bytes(raw)


def test_known_client_is_not_lead():
    msg = _make_msg("Sam <sam@boarhog.example>", "Question",
                    "Looking to hire help with our project, budget around $5k.")
    r = detect_lead(msg, known_client_domains={"boarhog.example"})
    assert r.is_lead is False
    assert "known client" in r.reason


def test_unknown_with_outreach_signals_is_lead():
    msg = _make_msg("Casey <casey@unknown.io>", "Project inquiry",
                    "Saw your portfolio. Looking to hire for a project. Have a budget.")
    r = detect_lead(msg, known_client_domains=set())
    assert r.is_lead is True
    assert len(r.matches) > 0


def test_unknown_without_signals_is_not_lead():
    msg = _make_msg("Alex <alex@somesite.com>", "Hi", "Just saying hello.")
    r = detect_lead(msg, known_client_domains=set())
    assert r.is_lead is False


def test_no_sender_domain_is_not_lead():
    msg = _make_msg("", "Subject", "Body")
    r = detect_lead(msg, known_client_domains=set())
    assert r.is_lead is False
