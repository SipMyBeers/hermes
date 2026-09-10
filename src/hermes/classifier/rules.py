"""Tier-1 rule classifier.

Rules are evaluated in priority order; the first match wins:

  0. Subject matches a transactional pattern -> transactional. Runs FIRST, ahead
     of every promo signal, because a one-time code buried under a marketing
     header locks the user out. See the note in classify().
  1. List-Unsubscribe header  -> promo  (subscription marketing otherwise wins)
  2. Sender domain is a known newsletter platform (skool, substack, etc.)
     -> promo
  3. Sender in personal allowlist -> personal  (allowlist is more specific
     than domain)
  4. Sender domain matches a client -> client
  5. Sender accumulated Dismiss taps via the action log -> promo
     (Phase L stage 2 dismiss-learning loop). Placed AFTER client so a
     client who somehow ended up in learned_promos still wins.
  6. Subject matches a transactional pattern -> transactional (confidence 0.9
     bypasses the LLM tier-2 threshold of 0.75 by design). Covers commerce
     (receipts, orders, invoices) AND authentication (one-time codes, password
     resets, magic links, security alerts). The auth half was missing, which sent
     every sign-in code to the LLM tier and from there into promo.
  7. Otherwise -> unknown (caller's tier-2 LLM handles)

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
from typing import Callable
import re

_TX_SUBJECT_PATTERNS = [
    # Commerce.
    r"\breceipt\b",
    r"\border\s*#",
    r"\binvoice\s+(received|paid|#)",
    r"\bpayment\s+confirmation\b",
    r"\byour\s+(order|receipt|payment)\b",
    # ⛔ AUTHENTICATION. These were absent entirely, so every sign-in code from every service fell
    # through to "unknown" and was handed to the LLM tier, which files them as promo. Observed on
    # real mail: LootLens sign-in codes landed under Hermes/Promo and were archived out of the
    # inbox, which is why they could not be found when needed.
    #
    # ⚠ THIS IS THE WORST CATEGORY TO MISFILE AND THE ONLY ONE THAT LOCKS YOU OUT. A misfiled
    # newsletter is noise; a misfiled one-time code means you cannot get into the account. It is
    # also the category most exposed to `delete_promos`, which defaults True and sends anything
    # classified promo to Trash. A code that expires in ten minutes does not survive a round trip
    # through the bin.
    #
    # ⚠ DELIBERATELY SUBJECT-ONLY AND DELIBERATELY NARROW. Matching the body would catch every
    # marketing mail that says "verify your email to claim 20% off". Every pattern here needs a
    # code/verification noun, so "Sign in to see what's new" does not match while "Your sign-in
    # code" does.
    r"\b(verification|security|confirmation|one[-\s]?time|sign[-\s]?in|login|access|passcode|authentication)\s+code\b",
    r"\bcode\s*:?\s*\d{4,8}\b",
    r"\byour\s+\S+\s+code\b",
    r"\b(2fa|two[-\s]?factor|otp)\b",
    r"\bpassword\s+reset\b",
    r"\breset\s+your\s+password\b",
    r"\bmagic\s+link\b",
    r"\bverify\s+your\s+(email|account|phone|number|identity)\b",
    r"\bsecurity\s+alert\b",
    r"\bnew\s+sign[-\s]?in\b",
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
    learned_promo_lookup: Callable[[str], bool] | None = None,
    client_contacts: dict[str, ClientHint] | None = None,
    partner_addrs: set[str] | None = None,
) -> RuleResult:
    """Tier-1 classification.

    learned_promo_lookup: optional callable bound to the live DB so the rule
    chain can ask 'has the user Dismissed mail from this sender N+ times?'
    without coupling this module to sqlite. Pure callers (most tests) leave
    it None and the lookup is skipped."""
    _, sender_addr = _sender(msg)

    # ⛔ AUTHENTICATION OUTRANKS EVERYTHING, INCLUDING List-Unsubscribe. Plenty of services attach
    # an unsubscribe header to their whole outbound stream, transactional mail included, and one of
    # them doing so must not be able to bury a one-time code. Misfiling a newsletter costs nothing;
    # misfiling a sign-in code locks the user out, and with `delete_promos` defaulting True it can
    # put a ten-minute code in the Trash.
    #
    # ⚠ SAFE TO PUT FIRST ONLY BECAUSE THE PATTERNS ARE SUBJECT-ONLY AND NARROW. Each requires a
    # code/verification noun, so "Use code SAVE20 at checkout" and "Sign in to see what's new" do
    # not reach here. Widening _TX_SUBJECT_PATTERNS means re-checking this precedence.
    subject_now = str(msg.get("Subject", "") or "")
    if _TX_RE.search(subject_now):
        return RuleResult(
            label="transactional",
            confidence=0.95,
            reason=f"transactional subject outranks promo signals: {subject_now!r}",
        )

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

    # Phase 1: exact contact-address match — catches clients on shared
    # domains (gmail.com) where domain matching would be a disaster.
    if client_contacts and sender_addr in client_contacts:
        hint = client_contacts[sender_addr]
        return RuleResult(
            label="client",
            confidence=1.0,
            reason=f"sender address is client contact for {hint.client}",
            client=hint.client,
        )

    # Phase 1: partner senders (relationship-holders who CC/forward client
    # mail). The daemon resolves which client the thread belongs to.
    if partner_addrs and sender_addr in {a.lower() for a in partner_addrs}:
        return RuleResult(
            label="partner",
            confidence=1.0,
            reason=f"sender {sender_addr} is a partner",
        )

    if sender_domain in client_domains:
        hint = client_domains[sender_domain]
        return RuleResult(
            label="client",
            confidence=1.0,
            reason=f"sender domain matches client {hint.client}",
            client=hint.client,
        )

    # Phase L stage 2: learned-promo feedback. Placed AFTER client so a
    # client who somehow ended up in learned_promos still gets the client
    # label. None-safe so pure callers (tests, no-DB paths) skip it.
    if learned_promo_lookup is not None and sender_addr and learned_promo_lookup(sender_addr):
        return RuleResult(
            label="promo",
            confidence=1.0,
            reason=f"learned promo: {sender_addr} previously dismissed",
        )

    subject = str(msg.get("Subject", "") or "")
    if _TX_RE.search(subject):
        return RuleResult(label="transactional", confidence=0.9, reason=f"subject pattern: {subject!r}")

    return RuleResult(label="unknown", confidence=0.0, reason="no rule matched")
