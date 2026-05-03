import pytest
from email.message import Message
from datetime import date
from hermes.extract.renewal import RenewalInfo, extract_renewal, _RENEWAL_VENDOR_DOMAINS


def test_renewal_info_fields():
    r = RenewalInfo(vendor="Cloudflare", resource="ranger-beers.com",
                    expires_at=date(2027, 5, 1), auto_renew=True,
                    cost_cents=995)
    assert r.vendor == "Cloudflare"
    assert r.resource == "ranger-beers.com"
    assert r.auto_renew is True
    assert r.cost_cents == 995


def test_extract_renewal_not_implemented_yet():
    with pytest.raises(NotImplementedError, match="v1.2"):
        extract_renewal(Message())


def test_known_vendor_domains_present():
    """Sanity-check that the vendor-domain seed table has the obvious players."""
    assert "cloudflare.com" in _RENEWAL_VENDOR_DOMAINS
    assert "namecheap.com" in _RENEWAL_VENDOR_DOMAINS
