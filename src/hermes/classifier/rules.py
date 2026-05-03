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

    if sender_addr in {a.lower() for a in personal_allowlist}:
        return RuleResult(label="personal", confidence=1.0, reason="sender in personal_allowlist")

    sender_domain = _domain_of(sender_addr)
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
