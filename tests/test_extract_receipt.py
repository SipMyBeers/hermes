import pytest
from hermes.extract.receipt import ReceiptInfo, extract_receipt
from email.message import Message
from datetime import date


def test_receipt_info_fields():
    r = ReceiptInfo(vendor="Stripe", amount_cents=2900, currency="USD",
                    line_items=("Pro plan",), date=date(2026, 5, 1), confidence=0.95)
    assert r.vendor == "Stripe"
    assert r.amount_cents == 2900
    assert r.line_items == ("Pro plan",)


def test_extract_receipt_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="v1.2"):
        extract_receipt(Message())
