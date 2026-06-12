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
from hermes.actions.store import add_actions, add_or_consolidate_email_action  # noqa: F401
from hermes.config import Config, load_config
from hermes.classifier.rules import classify as rule_classify, ClientHint
from hermes.classifier.llm import classify_llm, LLMResult
from hermes.classifier.drafter import draft_reply
from hermes.classifier.learned import is_learned_promo
from hermes.classifier.partner import infer_client
from hermes.db import beat, connect, migrate
from hermes.digest.enrich import summarize_for_brief
from hermes.extract.lead import detect_lead, LeadSignal
from hermes.mail.imap_client import ImapClient
from hermes.mail.outbound import process_sent
from hermes.mail.thread import thread_key_for
from hermes.telegram.client import TelegramClient
from hermes.telegram.keyboards import lead_card, urgent_card

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


def _client_contacts(cfg: Config) -> dict[str, ClientHint]:
    return {
        c.contact_email.lower(): ClientHint(client=c.name)
        for c in cfg.clients.values() if c.contact_email
    }


def _partner_addrs(cfg: Config) -> set[str]:
    return {a for addrs in cfg.partners.values() for a in addrs}


def classify_message(
    msg: Message,
    cfg: Config,
    llm_classifier: Callable[[Message], LLMResult] | None,
    db: sqlite3.Connection | None = None,
) -> Classification:
    learned: Callable[[str], bool] | None = None
    if db is not None:
        learned = lambda addr: is_learned_promo(db, addr)
    rule = rule_classify(
        msg, _client_domains(cfg), personal_allowlist=set(),
        learned_promo_lookup=learned,
        client_contacts=_client_contacts(cfg),
        partner_addrs=_partner_addrs(cfg),
    )
    if rule.label == "partner":
        # Phase 1: a partner (Josh) thread belongs to whichever client its
        # participants/forward/subject point at. No match -> review, so it
        # stays in the inbox and downstream lead detection still sees it.
        client_key = infer_client(msg, cfg.clients)
        if client_key:
            return Classification(
                label="client",
                confidence=1.0,
                classifier="rule",
                reason=f"partner thread resolved to client {client_key}",
                client=client_key,
            )
        return Classification(
            label="review",
            confidence=0.0,
            classifier="rule",
            reason="partner thread, no client match",
        )
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
    classification = classify_message(msg, cfg, llm_classifier, db=db)
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
    # Phase N.1: derive a deterministic thread_key from the RFC 5322
    # headers so we can consolidate same-thread mail into one open action.
    # thread_key_for() never returns None — falls back to empty string,
    # which we coerce to the lowercased Message-ID so this column is
    # never NULL on real inbound mail.
    tkey = thread_key_for(msg) or message_id.lower()

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
        "confidence, label, draft_text, drafted_at, raw_email, thread_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uid, message_id, received, sender, subject,
            classification.label, classification.classifier, classification.confidence,
            label, draft, datetime.now(timezone.utc).isoformat() if draft else None,
            raw,
            tkey,
        ),
    )
    try:
        imap.apply_label(uid, label)
        if classification.label == "promo":
            if cfg.mail.delete_promos:
                imap.move_to_trash(uid)
            else:
                imap.archive(uid)
        elif classification.label == "client":
            # Phase 1: client threads skip the inbox. They stay reachable
            # under Hermes/Client/<name>; urgent enrichment still pings.
            imap.archive(uid)
    except Exception as e:
        logger.error("imap label failed uid={} err={}", uid, e)

    # Phase L stage 2: receipts → ledger. When a transactional email rolls
    # in, run a gemma4 extraction over the body and append a direction='in'
    # ledger row when the result is confident enough. Wrapped broadly —
    # extractor / ledger failures must never break the daemon's primary
    # classification + label flow.
    if classification.label == "transactional":
        try:
            from hermes.extract.receipt import extract_receipt
            from hermes.extract.ledger_writer import maybe_write_receipt
            receipt = extract_receipt(msg)
            maybe_write_receipt(db, receipt, message_id=message_id)
        except Exception as e:
            logger.warning("receipt pipeline failed for uid={}: {}", uid, e)

    # Phase J.2: enrich worth-reading classes and persist actions to the
    # cross-source action log. Wrapped so any failure leaves the messages
    # row intact — the INSERT above already committed.
    if classification.label in ("client", "review", "personal", "other"):
        try:
            brief = summarize_for_brief(msg, user=cfg.user)
            new_ids: list[int] = []
            if brief.actions:
                # 'noise' importance maps to 'low' for the actions CHECK
                # constraint (urgent|normal|low only).
                imp = brief.importance if brief.importance != "noise" else "low"
                # Phase N.1: every email action goes through the
                # thread-aware consolidator. If a prior open action exists
                # for the same thread_key, its thread_count is bumped and
                # source_id repointed at this uid; otherwise a fresh row
                # is inserted. Returning the action id either way so the
                # urgent-ping keyboard still wires up correctly.
                for action_text in brief.actions:
                    aid, _was_new = add_or_consolidate_email_action(
                        db,
                        thread_key=tkey,
                        source_id=uid,
                        text=action_text,
                        importance=imp,
                        source_subject=subject,
                        source_sender=sender,
                    )
                    if aid:
                        new_ids.append(aid)
            # Real-time ping for urgent items — the user explicitly wants
            # these surfaced now, not buried in the morning brief.
            if brief.importance == "urgent" and brief.summary:
                try:
                    ping_text = (
                        f"🔴 *Urgent inbox*\n"
                        f"From: {sender}\n"
                        f"Subject: {subject[:140]}\n\n"
                        f"{brief.summary}"
                    )
                    # Phase N.2: build a Gmail jump-link off the
                    # persisted RFC 822 Message-ID so the urgent-ping
                    # card can carry a 📬 Open in Gmail row. Falls back
                    # to "" for the bootstrap-sentinel id, which
                    # gmail_url_for() filters out.
                    from hermes.mail.gmail_link import gmail_url_for
                    gmail_url = gmail_url_for(message_id)
                    if new_ids:
                        # Two-row keyboard: action triad (Done/Snooze/
                        # Dismiss bound to the first action id) + Draft
                        # reply bound to the message uid. Lets the user
                        # close the todo AND queue a reply from one ping.
                        _telegram(cfg).send_message(
                            ping_text,
                            reply_markup=urgent_card(
                                new_ids[0], uid, gmail_url=gmail_url,
                            ),
                        )
                    else:
                        # No action ids extracted but the LLM still flagged
                        # this as urgent — surface the Draft button alone
                        # so the user can move on it.
                        _telegram(cfg).send_message(
                            ping_text,
                            reply_markup=lead_card(uid, gmail_url=gmail_url),
                        )
                except Exception as e:
                    logger.warning("urgent ping failed: {}", e)
        except Exception as e:
            logger.warning("enrichment + action persist failed for uid={}: {}", uid, e)

    # Lead detection runs alongside classification, not in place of it.
    # A message can be both classified as 'review' AND a lead — both labels apply.
    # Skipped for promos since they're being trashed/archived anyway.
    # Skipped for client threads too (Phase 1): a resolved project thread is
    # ongoing work, not a prospect — "quote"/"proposal" words are routine there.
    if classification.label not in ("promo", "client"):
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
                # Phase N.2: lead pings carry a Gmail jump-link too so
                # the user can scan the original thread in Gmail web
                # before deciding whether to draft a reply.
                from hermes.mail.gmail_link import gmail_url_for
                gmail_url = gmail_url_for(message_id)
                _telegram(cfg).send_message(
                    f"🆕 *New lead*\n"
                    f"From: {lead.sender}\n"
                    f"Subject: {subj}\n"
                    f"Why: {lead.reason}",
                    reply_markup=lead_card(uid, gmail_url=gmail_url),
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
        # thread_key is explicitly NULL — the bootstrap row isn't real
        # mail and shouldn't ever match a thread lookup.
        db.execute(
            "INSERT OR IGNORE INTO messages "
            "(uid, message_id, received_at, sender, subject, classification, "
            " classifier, confidence, raw_email, thread_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                anchor, f"hermes-bootstrap-{anchor}",
                datetime.now(timezone.utc).isoformat(),
                "<bootstrap>", "<initial anchor>",
                "other", "user", 1.0,
                None,
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
            beat(db, "hermes-mail")
            since = _bootstrap_anchor(db, imap)
            batch = 0
            for fetched in imap.fetch_recent(since_uid=since, limit=200):
                _process_fetched(fetched, db, cfg, imap, dry_run)
                batch += 1
            # Phase 1: file outbound mail by client. Wrapped — Sent filing
            # must never take down inbound triage.
            try:
                process_sent(db, cfg, imap)
            except Exception as e:
                logger.warning("sent watcher failed: {}", e)
            beat(db, "hermes-mail")
            if batch >= 200:
                # Full batch means there's likely more backlog behind it.
                # Loop immediately instead of IDLE-blocking until NEW mail
                # arrives — otherwise a big backlog drains 200 per inbound.
                logger.info("full batch ({}); continuing drain without idle", batch)
                continue
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
