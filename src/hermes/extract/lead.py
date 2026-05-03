"""Detect new business prospects from inbound mail.

A lead is a sender from a domain not in clients.toml whose body or subject
contains business-outreach signals."""
from __future__ import annotations
from dataclasses import dataclass
from email.message import Message
from email.utils import getaddresses
import re

# Non-capturing groups so re.findall returns full match strings, not tuples.
_LEAD_PATTERNS = [
    r"\bproposal\b", r"\bquote\b", r"\bestimate\b",
    r"\binterested in (?:your|the)\b", r"\blooking to (?:hire|work)\b",
    r"\bbudget\b", r"\bproject\b.*\b(?:timeline|scope)\b",
    r"\bcan (?:you|we) (?:build|help|work)\b",
    r"\bsaw your (?:work|portfolio|website)\b",
    r"\breach(?:ed|ing) out\b",
    r"\binquir(?:e|y|ing)\b",
]
_LEAD_RE = re.compile("|".join(_LEAD_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class LeadSignal:
    is_lead: bool
    sender: str           # full From header
    sender_domain: str    # lowercased, "" if missing
    reason: str           # human-readable why
    matches: tuple[str, ...]  # matched pattern fragments


def detect_lead(msg: Message, known_client_domains: set[str]) -> LeadSignal:
    """Return LeadSignal(is_lead=True) when sender is unknown AND body/subject
    matches >=1 outreach pattern."""
    sender = str(msg.get("From", ""))
    addrs = getaddresses([sender])
    addr = (addrs[0][1] if addrs else "").lower()
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    if domain and domain in {d.lower() for d in known_client_domains}:
        return LeadSignal(is_lead=False, sender=sender, sender_domain=domain,
                          reason="sender is a known client domain", matches=())
    if not domain:
        return LeadSignal(is_lead=False, sender=sender, sender_domain="",
                          reason="no sender domain", matches=())
    haystack = f"{msg.get('Subject','')} {_body_text_safe(msg)}"
    matches = tuple(set(_LEAD_RE.findall(haystack)))
    if matches:
        return LeadSignal(is_lead=True, sender=sender, sender_domain=domain,
                          reason="unknown sender + outreach signals",
                          matches=matches)
    return LeadSignal(is_lead=False, sender=sender, sender_domain=domain,
                      reason="unknown sender but no outreach signals", matches=())


def _body_text_safe(msg: Message) -> str:
    try:
        from hermes.classifier.llm import _body_text
        return _body_text(msg)
    except Exception:
        return ""
