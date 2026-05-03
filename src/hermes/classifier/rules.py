"""Tier-1 rule classifier.

Rules are evaluated in priority order; the first match wins:

  1. List-Unsubscribe header  -> promo  (subscription marketing always wins)
  2. Sender domain is a known newsletter platform (skool, substack, etc.)
     -> promo
  3. Sender in personal allowlist -> personal  (allowlist is more specific
     than domain)
  4. Sender domain matches a client -> client
  5. Subject matches a transactional pattern -> transactional (confidence 0.9
     bypasses the LLM tier-2 threshold of 0.75 by design)
  6. Otherwise -> unknown (caller's tier-2 LLM handles)

Note: bare-prefix sender heuristics (noreply@, notifications@, hello@) were
tried in an earlier draft but produced false positives on real ops signals
(GitHub Actions failures, Cloudflare alerts, transactional bank notifications).
We rely on List-Unsubscribe + newsletter-domain allowlist instead, and let
the LLM tier handle the ambiguous middle.
"""
from __future__ import annotations
from dataclasses import dataclass
from email.message import Message
from email.utils import getaddresses
import re

_TX_SUBJECT_PATTERNS = [
    r"\breceipt\b",
    r"\border\s*#",
    r"\binvoice\s+(received|paid|#)",
    r"\bpayment\s+confirmation\b",
    r"\byour\s+(order|receipt|payment)\b",
]
_TX_RE = re.compile("|".join(_TX_SUBJECT_PATTERNS), re.IGNORECASE)

# Domains that are exclusively newsletter / community / drip platforms.
_NEWSLETTER_DOMAINS = {
    "skool.com",
    "substack.com",
    "convertkit.com",
    "beehiiv.com",
    "mailerlite.com",
    "mailchimp.com", "mc.us", "list-manage.com",
    "constantcontact.com", "ccsend.com",
    "kit.com",
    "buttondown.email", "buttondown.com",
    "ghost.io",
    "circle.so",
    "klaviyomail.com", "sendgrid.net",
    "campaignmonitor.com",
}


@dataclass(frozen=True)
class ClientHint:
    client: str   # client_key from clients.toml


@dataclass(frozen=True)
class RuleResult:
    label: str    # promo|client|personal|transactional|unknown
    confidence: float
    reason: str
    client: str | None = None


def _sender(msg: Message) -> tuple[str, str]:
    """Return (display_name, email_addr) lowercased."""
    addrs = getaddresses([msg.get("From", "")])
    if not addrs:
        return ("", "")
    name, addr = addrs[0]
    return (name, addr.lower())


def _domain_of(addr: str) -> str:
    return addr.split("@", 1)[1] if "@" in addr else ""


def classify(
    msg: Message,
    client_domains: dict[str, ClientHint],
    personal_allowlist: set[str],
) -> RuleResult:
    _, sender_addr = _sender(msg)

    if msg.get("List-Unsubscribe"):
        return RuleResult(label="promo", confidence=1.0, reason="List-Unsubscribe header present")

    sender_domain = _domain_of(sender_addr)
    if sender_domain in _NEWSLETTER_DOMAINS:
        return RuleResult(
            label="promo",
            confidence=1.0,
            reason=f"sender domain {sender_domain} is a newsletter platform",
        )

    if sender_addr in {a.lower() for a in personal_allowlist}:
        return RuleResult(label="personal", confidence=1.0, reason="sender in personal_allowlist")

    if sender_domain in client_domains:
        hint = client_domains[sender_domain]
        return RuleResult(
            label="client",
            confidence=1.0,
            reason=f"sender domain matches client {hint.client}",
            client=hint.client,
        )

    subject = str(msg.get("Subject", "") or "")
    if _TX_RE.search(subject):
        return RuleResult(label="transactional", confidence=0.9, reason=f"subject pattern: {subject!r}")

    return RuleResult(label="unknown", confidence=0.0, reason="no rule matched")
