from __future__ import annotations
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
import sqlite3
import time
from typing import Callable
from loguru import logger
from hermes.config import Config, load_config
from hermes.classifier.rules import classify as rule_classify, ClientHint
from hermes.classifier.llm import classify_llm, LLMResult
from hermes.classifier.drafter import draft_reply
from hermes.db import connect, migrate
from hermes.extract.lead import detect_lead, LeadSignal
from hermes.mail.imap_client import ImapClient
from hermes.telegram.client import TelegramClient

LLM_THRESHOLD = 0.75


@dataclass(frozen=True)
class Classification:
    label: str          # promo|client|personal|transactional|review|other
    confidence: float
    classifier: str     # rule|llm|user
    reason: str
    client: str | None = None


def _client_domains(cfg: Config) -> dict[str, ClientHint]:
    return {
        c.domain.lower(): ClientHint(client=c.name)
        for c in cfg.clients.values() if c.domain
    }


def classify_message(
    msg: Message,
    cfg: Config,
    llm_classifier: Callable[[Message], LLMResult] | None,
) -> Classification:
    rule = rule_classify(msg, _client_domains(cfg), personal_allowlist=set())
    if rule.label != "unknown":
        return Classification(
            label=rule.label,
            confidence=rule.confidence,
            classifier="rule",
            reason=rule.reason,
            client=rule.client,
        )
    if llm_classifier is None:
        return Classification(
            label="review", confidence=0.0, classifier="rule", reason="no llm available"
        )
    llm = llm_classifier(msg)
    if llm.label == "unknown" and llm.confidence == 0.0:
        # I/O / parse failure path; LLM never actually classified.
        return Classification(
            label="review", confidence=0.0, classifier="rule",
            reason=f"llm unavailable: {llm.why}",
        )
    label = llm.label if llm.confidence >= LLM_THRESHOLD else "review"
    return Classification(
        label=label, confidence=llm.confidence, classifier="llm", reason=llm.why,
    )


def _label_for(c: Classification) -> str:
    if c.label == "client" and c.client:
        return f"Hermes/Client/{c.client}"
    return f"Hermes/{c.label.capitalize()}"


def _persist_lead(db: sqlite3.Connection, uid: int, lead: LeadSignal) -> None:
    db.execute(
        "INSERT INTO leads(uid, sender, sender_domain, reason, matches, detected_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            uid, lead.sender, lead.sender_domain, lead.reason,
            ", ".join(lead.matches),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _telegram(cfg: Config) -> TelegramClient:
    """Lazy Telegram instance for lead pings. Module-level so tests can rebind."""
    return TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id)


def _decode_header(value: object) -> str:
    """Decode RFC 2047 encoded-word headers (e.g. =?UTF-8?B?...?=) to readable
    text. Handles None, str, and email.header.Header objects."""
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def process_message(
    uid: int,
    msg: Message,
    db: sqlite3.Connection,
    cfg: Config,
    imap: ImapClient,
    llm_classifier: Callable[[Message], LLMResult] | None,
    drafter: Callable[[Message, str], str] | None,
) -> None:
    classification = classify_message(msg, cfg, llm_classifier)
    label = _label_for(classification)
    message_id = str(msg.get("Message-ID", f"hermes-{uid}"))
    date_hdr = msg.get("Date")
    if date_hdr:
        try:
            parsed = parsedate_to_datetime(str(date_hdr))
            # If naive (RFC 2822 -0000 -> no tzinfo on some Python versions), assume UTC.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            received = parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            received = datetime.now(timezone.utc).isoformat()
    else:
        received = datetime.now(timezone.utc).isoformat()
    sender = _decode_header(msg.get("From"))
    subject = _decode_header(msg.get("Subject"))

    draft = ""
    if classification.label == "client" and drafter is not None:
        draft = drafter(msg, "Dylan")

    # Persist the full RFC822 bytes so the morning digest can re-parse the
    # message and run gemma4 enrichment without going back to IMAP. Skipped
    # only for promos (we never resurface them) to keep the DB lean.
    raw = None if classification.label == "promo" else msg.as_bytes()
    db.execute(
        "INSERT OR IGNORE INTO messages "
        "(uid, message_id, received_at, sender, subject, classification, classifier, "
        "confidence, label, draft_text, drafted_at, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uid, message_id, received, sender, subject,
            classification.label, classification.classifier, classification.confidence,
            label, draft, datetime.now(timezone.utc).isoformat() if draft else None,
            raw,
        ),
    )
    try:
        imap.apply_label(uid, label)
        if classification.label == "promo":
            if cfg.mail.delete_promos:
                imap.move_to_trash(uid)
            else:
                imap.archive(uid)
    except Exception as e:
        logger.error("imap label failed uid={} err={}", uid, e)

    # Lead detection runs alongside classification, not in place of it.
    # A message can be both classified as 'review' AND a lead — both labels apply.
    # Skipped for promos since they're being trashed/archived anyway.
    if classification.label != "promo":
        known_domains = {c.domain.lower() for c in cfg.clients.values() if c.domain}
        lead = detect_lead(msg, known_client_domains=known_domains)
        if lead.is_lead:
            _persist_lead(db, uid, lead)
            try:
                imap.apply_label(uid, "Hermes/Lead")
            except Exception as e:
                logger.error("imap lead-label failed uid={} err={}", uid, e)
            try:
                subj = subject[:140]
                _telegram(cfg).send_message(
                    f"🆕 *New lead*\n"
                    f"From: {lead.sender}\n"
                    f"Subject: {subj}\n"
                    f"Why: {lead.reason}\n"
                    f"`/draft {uid}` · `/reclassify {uid} other` to dismiss"
                )
            except Exception as e:
                logger.warning("lead telegram ping failed: {}", e)


def _last_uid(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT MAX(uid) FROM messages").fetchone()
    return int(row[0] or 0)


def _bootstrap_anchor(db: sqlite3.Connection, imap: ImapClient) -> int:
    """Return since_uid for fetch_recent, anchoring to current INBOX state on first run."""
    last = _last_uid(db)
    if last > 0:
        return last
    # Empty DB: anchor to current max UID so we don't reprocess history.
    anchor = imap.current_max_uid()
    if anchor > 0:
        # Persist a sentinel row so subsequent calls don't re-anchor.
        db.execute(
            "INSERT OR IGNORE INTO messages "
            "(uid, message_id, received_at, sender, subject, classification, classifier, confidence, raw_email) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                anchor, f"hermes-bootstrap-{anchor}",
                datetime.now(timezone.utc).isoformat(),
                "<bootstrap>", "<initial anchor>",
                "other", "user", 1.0,
                None,
            ),
        )
        logger.info("first-run anchor set to uid={}; backlog skipped", anchor)
    return anchor


def _process_fetched(fetched, db, cfg, imap, dry_run: bool) -> None:
    if fetched.uid is None:
        logger.warning("imap fetched message with no UID — skipping")
        return
    process_message(
        uid=int(fetched.uid),
        msg=fetched.obj,
        db=db,
        cfg=cfg,
        imap=imap,
        llm_classifier=lambda m: classify_llm(m),
        drafter=draft_reply if not dry_run else None,
    )


def run_loop(cfg: Config, db: sqlite3.Connection, dry_run: bool = False) -> None:
    imap = ImapClient(cfg.gmail)
    imap.connect()
    try:
        while True:
            since = _bootstrap_anchor(db, imap)
            for fetched in imap.fetch_recent(since_uid=since, limit=200):
                _process_fetched(fetched, db, cfg, imap, dry_run)
            try:
                imap.idle(timeout=29 * 60)
            except Exception as e:
                logger.warning("idle dropped: {}; reconnecting in 5s", e)
                imap.disconnect()
                time.sleep(5)
                try:
                    imap.connect()
                except Exception as connect_err:
                    logger.error(
                        "imap reconnect failed: {} — check Gmail app password / network",
                        connect_err,
                    )
                    raise
    finally:
        imap.disconnect()


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-mail")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--once", action="store_true", help="process pending and exit")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    args = p.parse_args()

    cfg = load_config()
    db = connect(Path(args.db))
    migrate(db)
    if args.once:
        imap = ImapClient(cfg.gmail)
        imap.connect()
        try:
            since = _bootstrap_anchor(db, imap)
            for fetched in imap.fetch_recent(since_uid=since, limit=200):
                _process_fetched(fetched, db, cfg, imap, args.dry_run)
        finally:
            imap.disconnect()
    else:
        run_loop(cfg, db, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
