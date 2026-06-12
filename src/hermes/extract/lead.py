"""Detect new business prospects from inbound mail.

A lead is a sender from a domain not in clients.toml whose body or subject
contains business-outreach signals."""
from __future__ import annotations
from dataclasses import dataclass
from email.message import Message
from email.utils import getaddresses
import json
import re
import httpx
from loguru import logger

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


def detect_lead(
    msg: Message,
    known_client_domains: set[str],
    ignore_domains: set[str] = frozenset(),
) -> LeadSignal:
    """Return LeadSignal(is_lead=True) when sender is unknown AND body/subject
    matches >=1 outreach pattern."""
    sender = str(msg.get("From", ""))
    addrs = getaddresses([sender])
    addr = (addrs[0][1] if addrs else "").lower()
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    if domain and domain in {d.lower() for d in ignore_domains}:
        return LeadSignal(is_lead=False, sender=sender, sender_domain=domain,
                          reason="sender domain on the lead ignore list", matches=())
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


def detect_form_lead(msg: Message, form_address: str) -> bool:
    """Deterministic contact-form rule: the dedicated leads address appears
    among the delivery headers. No LLM, no patterns."""
    if not form_address:
        return False
    target = form_address.lower()
    for header in ("To", "Cc", "Delivered-To", "X-Forwarded-To", "X-Original-To"):
        for value in msg.get_all(header, []):
            if target in str(value).lower():
                return True
    return False


# "From: Name <addr>" line inside a forwarded block.
_FWD_FROM_RE = re.compile(
    r"^From:\s*(?:\"?([^\"<\n]*?)\"?\s*)?<?([\w.+-]+@[\w-]+\.[\w.-]+)>?\s*$",
    re.MULTILINE,
)


def extract_forward_original(msg: Message) -> tuple[str, str] | None:
    """First (name, addr) From-line found in the body head (forwarded block)."""
    body = _body_text_safe(msg)[:3000]
    m = _FWD_FROM_RE.search(body)
    if not m:
        return None
    name = (m.group(1) or "").strip()
    return (name, m.group(2).lower())


def detect_partner_lead(
    msg: Message,
    clients,
    partner_addrs: set[str],
    ignore_domains: set[str],
) -> LeadSignal:
    """A partner forward whose original sender is a stranger = a lead the
    partner is handing off. Known clients, partners themselves, and ignored
    domains never fire."""
    sender = str(msg.get("From", ""))
    original = extract_forward_original(msg)
    if not original:
        return LeadSignal(is_lead=False, sender=sender, sender_domain="",
                          reason="no forwarded original sender found", matches=())
    name, addr = original
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    if addr in {a.lower() for a in partner_addrs}:
        return LeadSignal(is_lead=False, sender=sender, sender_domain=domain,
                          reason="forward originates from the partner", matches=())
    if domain in {d.lower() for d in ignore_domains}:
        return LeadSignal(is_lead=False, sender=sender, sender_domain=domain,
                          reason="forwarded sender domain on the ignore list", matches=())
    for c in getattr(clients, "values", lambda: [])():
        if c.contact_email and addr == c.contact_email.lower():
            return LeadSignal(is_lead=False, sender=sender, sender_domain=domain,
                              reason="forwarded sender is a known client", matches=())
        if c.domain and domain == c.domain.lower():
            return LeadSignal(is_lead=False, sender=sender, sender_domain=domain,
                              reason="forwarded sender is a known client domain", matches=())
    display = f"{name} <{addr}>" if name else addr
    return LeadSignal(is_lead=True, sender=display, sender_domain=domain,
                      reason="partner forwarded a new prospect", matches=("partner-forward",))


_CONFIRM_PROMPT = """Is this email from a prospective client asking about website,
web design, hosting, or development work? Answer ONLY with JSON:
{{"is_lead": true/false, "why": "<short reason>"}}

From: {sender}
Subject: {subject}
Body (first 1200 chars):
{body}
"""


def confirm_lead_llm(
    msg: Message,
    model: str = "qwen3:4b",
    host: str = "http://localhost:11434",
    timeout: float = 20.0,
) -> bool:
    """LLM yes/no gate for regex-detected direct leads. Defaults to True on
    any infra failure — losing a real lead costs more than a false ping."""
    prompt = _CONFIRM_PROMPT.format(
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        body=_body_text_safe(msg)[:1200],
    )
    payload = {"model": model, "prompt": prompt, "stream": False,
               "format": "json", "think": False}
    try:
        resp = httpx.post(f"{host}/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        data = json.loads(resp.json().get("response", ""))
        return bool(data.get("is_lead", True))
    except Exception as e:
        logger.warning("lead confirm LLM failed ({}); defaulting to lead=yes", e)
        return True
