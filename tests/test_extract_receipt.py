from email import message_from_bytes
from email.message import Message
from datetime import date
from unittest.mock import MagicMock
import json
import httpx
import pytest
from hermes.extract.receipt import ReceiptInfo, extract_receipt


def _msg(subject="Receipt for your order", body="Total: $29.00"):
    raw = (
        f"From: Stripe <noreply@stripe.com>\r\n"
        f"To: dz@gmail.com\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Fri, 02 May 2026 10:00:00 +0000\r\n"
        f"Message-ID: <r@s>\r\n\r\n{body}\r\n"
    ).encode()
    return message_from_bytes(raw)


def _ollama_response(payload: dict) -> MagicMock:
    """Build a mock httpx Response with a JSON body whose 'response' field is
    the LLM's stringified JSON output (matching Ollama's wire shape)."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"response": json.dumps(payload)})
    return resp


def test_receipt_info_fields():
    r = ReceiptInfo(vendor="Stripe", amount_cents=2900, currency="USD",
                    line_items=("Pro plan",), date=date(2026, 5, 1), confidence=0.95)
    assert r.vendor == "Stripe"
    assert r.amount_cents == 2900
    assert r.line_items == ("Pro plan",)


def test_extract_receipt_parses_response(monkeypatch):
    """Happy path: gemma4 returns clean JSON, all fields populate."""
    monkeypatch.setattr(
        "hermes.extract.receipt.httpx.post",
        lambda *a, **kw: _ollama_response({
            "vendor": "Stripe",
            "amount_cents": 2900,
            "currency": "USD",
            "line_items": ["Pro plan"],
            "date": "2026-05-01",
            "confidence": 0.92,
        }),
    )
    r = extract_receipt(_msg())
    assert r.vendor == "Stripe"
    assert r.amount_cents == 2900
    assert r.currency == "USD"
    assert r.line_items == ("Pro plan",)
    assert r.date == date(2026, 5, 1)
    assert r.confidence == pytest.approx(0.92)


def test_extract_receipt_handles_low_confidence(monkeypatch):
    """Low-confidence extractions are still returned — caller's confidence
    gate is the policy layer, not the extractor."""
    monkeypatch.setattr(
        "hermes.extract.receipt.httpx.post",
        lambda *a, **kw: _ollama_response({
            "vendor": "Stripe", "amount_cents": 0, "currency": "USD",
            "line_items": [], "date": "", "confidence": 0.1,
        }),
    )
    r = extract_receipt(_msg())
    assert r.confidence == pytest.approx(0.1)
    assert r.amount_cents == 0


def test_extract_receipt_handles_http_error(monkeypatch):
    """Transport failure -> empty ReceiptInfo, no crash."""
    def boom(*a, **kw):
        raise httpx.HTTPError("connection refused")
    monkeypatch.setattr("hermes.extract.receipt.httpx.post", boom)
    r = extract_receipt(_msg())
    assert r.vendor == ""
    assert r.amount_cents == 0
    assert r.confidence == 0.0
    assert r.date is None


def test_extract_receipt_handles_unparseable_date(monkeypatch):
    """A bad date string must not blow up the extractor."""
    monkeypatch.setattr(
        "hermes.extract.receipt.httpx.post",
        lambda *a, **kw: _ollama_response({
            "vendor": "Cloudflare", "amount_cents": 500, "currency": "USD",
            "line_items": [], "date": "not-a-date", "confidence": 0.8,
        }),
    )
    r = extract_receipt(_msg())
    assert r.vendor == "Cloudflare"
    assert r.amount_cents == 500
    assert r.date is None


def test_extract_receipt_clamps_confidence(monkeypatch):
    """Confidence outside [0,1] is clamped — model occasionally returns 1.5
    or negative when it gets confused."""
    monkeypatch.setattr(
        "hermes.extract.receipt.httpx.post",
        lambda *a, **kw: _ollama_response({
            "vendor": "Anthropic", "amount_cents": 2000, "currency": "USD",
            "line_items": [], "date": "", "confidence": 1.5,
        }),
    )
    r = extract_receipt(_msg())
    assert r.confidence == 1.0

    monkeypatch.setattr(
        "hermes.extract.receipt.httpx.post",
        lambda *a, **kw: _ollama_response({
            "vendor": "Anthropic", "amount_cents": 2000, "currency": "USD",
            "line_items": [], "date": "", "confidence": -0.2,
        }),
    )
    r2 = extract_receipt(_msg())
    assert r2.confidence == 0.0


def test_extract_receipt_default_currency_when_blank(monkeypatch):
    """Empty currency falls back to USD per the prompt rule."""
    monkeypatch.setattr(
        "hermes.extract.receipt.httpx.post",
        lambda *a, **kw: _ollama_response({
            "vendor": "Stripe", "amount_cents": 100, "currency": "",
            "line_items": [], "date": "", "confidence": 0.9,
        }),
    )
    r = extract_receipt(_msg())
    assert r.currency == "USD"


def test_extract_receipt_handles_unparseable_json(monkeypatch):
    """LLM occasionally returns prose instead of JSON; degrade quietly."""
    bad = MagicMock()
    bad.raise_for_status = MagicMock()
    bad.json = MagicMock(return_value={"response": "not json at all"})
    monkeypatch.setattr(
        "hermes.extract.receipt.httpx.post", lambda *a, **kw: bad
    )
    r = extract_receipt(_msg())
    assert r.confidence == 0.0
