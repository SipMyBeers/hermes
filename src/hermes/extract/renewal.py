"""Detect and extract resource-renewal emails (domain, SSL, SaaS license)."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from email.message import Message

# Sender-domain hints. Future: extend, possibly via clients.toml-style
# vendor config so users can add their own.
_RENEWAL_VENDOR_DOMAINS: dict[str, str] = {
    "cloudflare.com": "Cloudflare",
    "godaddy.com": "GoDaddy",
    "namecheap.com": "Namecheap",
    "namesilo.com": "NameSilo",
    "porkbun.com": "Porkbun",
    "letsencrypt.org": "Let's Encrypt",
    "google.com": "Google Workspace",  # often workspace billing
}


@dataclass(frozen=True)
class RenewalInfo:
    vendor: str
    resource: str            # e.g. "ranger-beers.com", "*.ranger-beers.com"
    expires_at: date | None
    auto_renew: bool
    cost_cents: int | None


def extract_renewal(msg: Message) -> RenewalInfo | None:
    """Return RenewalInfo if this looks like a renewal email; else None.

    v1.2 implementation:
      - Sender-domain match against _RENEWAL_VENDOR_DOMAINS for the vendor.
      - Subject pattern match: 'expir(es|ing|ed)', 'renew(s|ed|al)', 'auto-renew'.
      - Body extraction via gemma4 for resource name, expires_at, cost.
      - Return None if neither sender nor subject matches.
    """
    raise NotImplementedError("v1.2: vendor-aware extraction (see _RENEWAL_VENDOR_DOMAINS)")
