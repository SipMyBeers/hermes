import pytest
from hermes.extract.subscription import Subscription, detect_subscriptions
from datetime import datetime


def test_subscription_fields():
    s = Subscription(vendor="Cloudflare", cadence="monthly", amount_cents=2000,
                     monthly_estimate_cents=2000, occurrences=4,
                     first_seen_at=datetime(2026, 1, 1),
                     last_charge_at=datetime(2026, 4, 1), status="active")
    assert s.cadence == "monthly"
    assert s.status == "active"


def test_detect_subscriptions_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="v1.2"):
        detect_subscriptions([])
