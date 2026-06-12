from dataclasses import replace
from email import message_from_bytes
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from hermes.config import Config, GmailConfig, TelegramConfig, ClientConfig, MailConfig
from hermes.classifier.rules import RuleResult, ClientHint
from hermes.classifier.llm import LLMResult
from hermes.digest.enrich import EmailBrief
from hermes.mail.daemon import process_message, classify_message, LLM_THRESHOLD

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch):
    """Default: every test in this module gets a fast empty enricher.

    Without this, any client/review/personal/other message routes through
    `summarize_for_brief`, which falls through to httpx → localhost:11434
    (Ollama) and eats ~5s per test waiting on connection failures. Tests
    that need a specific brief shape override via their own monkeypatch.
    """
    empty = EmailBrief(summary="", actions=(), tools=(), importance="normal")
    monkeypatch.setattr(
        "hermes.mail.daemon.summarize_for_brief",
        lambda msg, user=None, **kwargs: empty,
    )


def _cfg() -> Config:
    # Default to archive-only so existing archive-path tests keep their behaviour;
    # tests exercising the trash path explicitly flip delete_promos=True.
    return Config(
        gmail=GmailConfig(
            imap_user="u@g.com", imap_app_password="p",
            smtp_user="u@g.com", smtp_app_password="p",
            from_address="U <u@g.com>", reply_to="U <u@x.com>",
        ),
        telegram=TelegramConfig(bot_token="1:A", chat_id=0),
        digest_evening_report=False,
        clients={
            "boarhog": ClientConfig(
                name="boarhog", domain="boarhog.example",
                contact_email="billing@boarhog.example",
                billing_amount_cents=50000, billing_terms="Net 30",
                service_description="Hosting fees", active=True,
            )
        },
        mail=MailConfig(delete_promos=False),
    )


def test_classify_message_uses_rules_first():
    msg = message_from_bytes((FIXTURES / "promo_amazon.eml").read_bytes())
    result = classify_message(msg, _cfg(), llm_classifier=None)
    assert result.label == "promo"
    assert result.classifier == "rule"


def test_classify_message_falls_to_llm_when_no_rule():
    msg = message_from_bytes((FIXTURES / "ambiguous.eml").read_bytes())
    fake_llm = MagicMock(return_value=LLMResult(label="client", confidence=0.85, why="x"))
    result = classify_message(msg, _cfg(), llm_classifier=fake_llm)
    assert result.label == "client"
    assert result.classifier == "llm"


def test_classify_message_below_threshold_becomes_review():
    msg = message_from_bytes((FIXTURES / "ambiguous.eml").read_bytes())
    fake_llm = MagicMock(return_value=LLMResult(label="client", confidence=0.5, why="x"))
    result = classify_message(msg, _cfg(), llm_classifier=fake_llm)
    assert result.label == "review"
    assert result.confidence == 0.5


def test_process_message_persists_and_labels(tmp_path):
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    process_message(uid=10, msg=msg, db=db, cfg=_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    rows = list(db.execute("SELECT uid, classification, label, classifier FROM messages"))
    assert rows == [(10, "client", "Hermes/Client/boarhog", "rule")]
    fake_imap.apply_label.assert_called_once_with(10, "Hermes/Client/boarhog")


def test_process_message_archives_promo(tmp_path):
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "promo_amazon.eml").read_bytes())
    fake_imap = MagicMock()
    process_message(uid=11, msg=msg, db=db, cfg=_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    fake_imap.apply_label.assert_called_once_with(11, "Hermes/Promo")
    fake_imap.archive.assert_called_once_with(11)


def test_process_message_trashes_promo_when_delete_promos_true(tmp_path):
    """delete_promos=True routes classified promos to Gmail Trash, not archive."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "promo_amazon.eml").read_bytes())
    fake_imap = MagicMock()
    cfg = replace(_cfg(), mail=MailConfig(delete_promos=True))
    process_message(uid=21, msg=msg, db=db, cfg=cfg, imap=fake_imap, llm_classifier=None, drafter=None)
    fake_imap.apply_label.assert_called_once_with(21, "Hermes/Promo")
    fake_imap.move_to_trash.assert_called_once_with(21)
    fake_imap.archive.assert_not_called()


def test_process_message_archives_promo_when_delete_promos_false(tmp_path):
    """delete_promos=False keeps the legacy archive-only behaviour."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "promo_amazon.eml").read_bytes())
    fake_imap = MagicMock()
    cfg = replace(_cfg(), mail=MailConfig(delete_promos=False))
    process_message(uid=22, msg=msg, db=db, cfg=cfg, imap=fake_imap, llm_classifier=None, drafter=None)
    fake_imap.apply_label.assert_called_once_with(22, "Hermes/Promo")
    fake_imap.archive.assert_called_once_with(22)
    fake_imap.move_to_trash.assert_not_called()


def test_process_message_archives_client(tmp_path):
    """Phase 1 (v2 spec): client threads skip the inbox — label + archive.
    The morning digest and urgent enrichment ping keep them visible."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    process_message(uid=12, msg=msg, db=db, cfg=_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    fake_imap.apply_label.assert_called_once_with(12, "Hermes/Client/boarhog")
    fake_imap.archive.assert_called_once_with(12)


def test_received_at_normalizes_rfc2822_to_iso(tmp_path):
    """The Date header is RFC 2822; we store ISO 8601 so SQLite datetime() works."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    # Sanity: fixture has 'Date: Fri, 02 May 2026 10:00:00 +0000'
    fake_imap = MagicMock()
    process_message(uid=13, msg=msg, db=db, cfg=_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    row = db.execute("SELECT received_at FROM messages WHERE uid=13").fetchone()
    received = row[0]
    # ISO 8601 starts with YYYY-MM-DD
    assert received.startswith("2026-05-02"), f"expected ISO start, got {received!r}"
    # SQLite datetime() must be able to parse it
    parsed = db.execute("SELECT datetime(?)", (received,)).fetchone()[0]
    assert parsed is not None, f"datetime() failed to parse {received!r}"


def test_first_run_anchors_to_current_max_uid(tmp_path):
    """Empty DB + IMAP backlog -> don't fetch everything; anchor to current max UID."""
    from hermes.db import connect, migrate
    from hermes.mail.daemon import _bootstrap_anchor
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    fake_imap = MagicMock()
    fake_imap.current_max_uid.return_value = 9999
    anchor = _bootstrap_anchor(db, fake_imap)
    assert anchor == 9999
    fake_imap.current_max_uid.assert_called_once()
    # The anchor row must persist so a second call doesn't re-anchor
    second = _bootstrap_anchor(db, fake_imap)
    assert second == 9999
    assert fake_imap.current_max_uid.call_count == 1


def test_existing_db_skips_bootstrap(tmp_path):
    """Non-empty DB -> don't query IMAP for anchor."""
    from hermes.db import connect, migrate
    from hermes.mail.daemon import _bootstrap_anchor
    from datetime import datetime, timezone
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
        "VALUES (5, 'mid-5', ?, 'a@b', 'promo', 'rule')",
        (datetime.now(timezone.utc).isoformat(),),
    )
    fake_imap = MagicMock()
    anchor = _bootstrap_anchor(db, fake_imap)
    assert anchor == 5
    fake_imap.current_max_uid.assert_not_called()


def test_classify_message_llm_io_failure_marks_classifier_rule():
    """When classify_llm returns unknown/0.0 (I/O fail), don't claim 'llm' classified it."""
    msg = message_from_bytes((FIXTURES / "ambiguous.eml").read_bytes())
    fake_llm = MagicMock(return_value=LLMResult(label="unknown", confidence=0.0, why="ollama error: timeout"))
    result = classify_message(msg, _cfg(), llm_classifier=fake_llm)
    assert result.label == "review"
    assert result.classifier == "rule"
    assert "llm unavailable" in result.reason


def test_lead_detection_persists_and_pings(tmp_path, monkeypatch):
    """An unknown-sender business inquiry is recorded as a lead and pinged."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    raw = (
        b"From: Casey <casey@unknown.io>\r\n"
        b"To: dz@gmail.com\r\n"
        b"Subject: Project inquiry\r\n"
        b"Date: Fri, 02 May 2026 10:00:00 +0000\r\n"
        b"Message-ID: <lead-1@unknown.io>\r\n"
        b"\r\n"
        b"Saw your portfolio. Looking to hire for a project. Have a budget.\r\n"
    )
    msg = message_from_bytes(raw)
    fake_imap = MagicMock()
    fake_tg = MagicMock()
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: fake_tg)
    process_message(uid=200, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    rows = list(db.execute("SELECT uid, sender_domain, reason FROM leads"))
    assert rows == [(200, "unknown.io", "unknown sender + outreach signals")]
    label_calls = [c.args[1] for c in fake_imap.apply_label.call_args_list]
    assert "Hermes/Lead" in label_calls
    fake_tg.send_message.assert_called_once()
    text = fake_tg.send_message.call_args.args[0]
    assert "New lead" in text
    assert "unknown.io" in text
    # Phase L: lead pings carry a Draft-reply button bound to uid.
    # Phase N.2: lead cards may also carry a url-button for Gmail —
    # extract only callback-shaped buttons here so the assertion stays
    # focused on the Draft callback wiring.
    kb = fake_tg.send_message.call_args.kwargs.get("reply_markup")
    assert kb is not None
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r
           if "callback_data" in b]
    assert "draft:200" in cbs


def test_process_message_persists_actions_for_client(tmp_path, monkeypatch):
    """Client emails get enriched and any extracted action lands in the
    actions table tied back to the message uid.

    Phase N.1: when enrichment produces multiple action items for the SAME
    inbound message, they consolidate by thread_key into a single row whose
    text reflects the latest item and whose thread_count tracks the bumps.
    The user gets one card 'Generate invoice (2 messages)' rather than two
    separate cards for the same conversation."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    fake_brief = EmailBrief(
        summary="Sam wants the renewal quote.",
        actions=("Send renewal terms", "Generate invoice"),
        tools=(),
        importance="normal",
    )
    monkeypatch.setattr(
        "hermes.mail.daemon.summarize_for_brief",
        lambda *a, **kw: fake_brief,
    )
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: MagicMock())
    process_message(uid=300, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    rows = list(db.execute(
        "SELECT source_kind, source_id, text, importance, status, thread_count "
        "FROM actions"
    ))
    # Two enrichment items collapsed into one consolidated row.
    assert len(rows) == 1
    assert rows[0] == ("email", 300, "Send renewal terms", "normal", "open", 2)


def test_process_message_skips_action_persist_for_promo(tmp_path, monkeypatch):
    """Promos must not be enriched — saves an LLM call and avoids polluting
    the action log with junk-mail noise."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "promo_amazon.eml").read_bytes())
    fake_imap = MagicMock()
    enrich_calls = []
    monkeypatch.setattr(
        "hermes.mail.daemon.summarize_for_brief",
        lambda *a, **kw: enrich_calls.append(a) or None,
    )
    process_message(uid=301, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    assert enrich_calls == []
    rows = list(db.execute("SELECT COUNT(*) FROM actions"))
    assert rows[0][0] == 0


def test_process_message_urgent_pings_telegram(tmp_path, monkeypatch):
    """importance='urgent' triggers an inline 🔴 ping with the summary."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    fake_tg = MagicMock()
    fake_brief = EmailBrief(
        summary="Production is down — rollback now.",
        actions=("Rollback last deploy",),
        tools=(),
        importance="urgent",
    )
    monkeypatch.setattr(
        "hermes.mail.daemon.summarize_for_brief",
        lambda *a, **kw: fake_brief,
    )
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: fake_tg)
    process_message(uid=302, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    # At least one send_message call with the urgent badge.
    urgent_calls = [c for c in fake_tg.send_message.call_args_list
                    if "🔴" in c.args[0]]
    assert len(urgent_calls) == 1
    text = urgent_calls[0].args[0]
    assert "Urgent inbox" in text
    assert "Production is down" in text


def test_urgent_inbox_ping_includes_action_button(tmp_path, monkeypatch):
    """Urgent ping with at least one action carries a two-row keyboard:
    Done/Snooze/Dismiss bound to the first action id, and Draft reply
    bound to the message uid."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    fake_tg = MagicMock()
    fake_brief = EmailBrief(
        summary="Down.",
        actions=("Rollback last deploy",),
        tools=(),
        importance="urgent",
    )
    monkeypatch.setattr(
        "hermes.mail.daemon.summarize_for_brief",
        lambda *a, **kw: fake_brief,
    )
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: fake_tg)
    process_message(uid=304, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    urgent_calls = [c for c in fake_tg.send_message.call_args_list
                    if "🔴" in c.args[0]]
    assert len(urgent_calls) == 1
    kb = urgent_calls[0].kwargs.get("reply_markup")
    assert kb is not None
    # Phase N.2: the keyboard may also carry a url-button for Gmail.
    # Filter to callback-shaped buttons so the action/draft callback
    # checks stay focused.
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r
           if "callback_data" in b]
    # First action id was inserted as the only row -> id 1.
    aid = db.execute("SELECT id FROM actions").fetchone()[0]
    assert f"done:{aid}" in cbs
    assert f"snooze:{aid}" in cbs
    assert f"dismiss:{aid}" in cbs
    # Phase L: second-row Draft button wired to the message uid.
    assert "draft:304" in cbs


def test_urgent_inbox_ping_includes_gmail_link(tmp_path, monkeypatch):
    """Phase N.2: the urgent ping carries a 📬 Open in Gmail row built
    from the message's RFC 822 Message-ID. That gives the user a
    one-tap deep-link into Gmail web alongside the action triad and
    Draft button."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    fake_tg = MagicMock()
    fake_brief = EmailBrief(
        summary="Down.",
        actions=("Rollback last deploy",),
        tools=(),
        importance="urgent",
    )
    monkeypatch.setattr(
        "hermes.mail.daemon.summarize_for_brief",
        lambda *a, **kw: fake_brief,
    )
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: fake_tg)
    process_message(uid=350, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    urgent_calls = [c for c in fake_tg.send_message.call_args_list
                    if "🔴" in c.args[0]]
    assert len(urgent_calls) == 1
    kb = urgent_calls[0].kwargs.get("reply_markup")
    assert kb is not None
    rows = kb["inline_keyboard"]
    # Three rows: action triad, Draft, Gmail jump-link.
    assert len(rows) == 3
    gmail_btn = rows[2][0]
    assert "Gmail" in gmail_btn["text"]
    assert gmail_btn["url"].startswith(
        "https://mail.google.com/mail/u/0/#search/rfc822msgid:"
    )
    # The fixture's Message-ID is bh-1@boarhog.example.
    assert "bh-1@boarhog.example" in gmail_btn["url"]


def test_urgent_inbox_ping_without_actions_carries_lead_card(tmp_path, monkeypatch):
    """When no actions were extracted, the urgent ping falls back to a
    single-row lead_card with the Draft button so the user can still
    queue a reply in one tap."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    fake_tg = MagicMock()
    fake_brief = EmailBrief(
        summary="Heads up — outage.", actions=(), tools=(), importance="urgent",
    )
    monkeypatch.setattr(
        "hermes.mail.daemon.summarize_for_brief",
        lambda *a, **kw: fake_brief,
    )
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: fake_tg)
    process_message(uid=305, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    urgent_calls = [c for c in fake_tg.send_message.call_args_list
                    if "🔴" in c.args[0]]
    assert len(urgent_calls) == 1
    kb = urgent_calls[0].kwargs.get("reply_markup")
    assert kb is not None
    # Filter to callback-shaped buttons (url-buttons may be present too).
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r
           if "callback_data" in b]
    assert "draft:305" in cbs
    # No action ids existed so no done/snooze/dismiss buttons either.
    assert not any(cb.startswith("done:") for cb in cbs)


def test_process_message_non_urgent_does_not_ping(tmp_path, monkeypatch):
    """Non-urgent enrichments stay silent — only the morning brief surfaces them."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    fake_tg = MagicMock()
    fake_brief = EmailBrief(
        summary="Routine check-in.", actions=(), tools=(),
        importance="normal",
    )
    monkeypatch.setattr(
        "hermes.mail.daemon.summarize_for_brief",
        lambda *a, **kw: fake_brief,
    )
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: fake_tg)
    process_message(uid=303, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    # The lead-detect path is the only thing that pings for known-client
    # emails, and known-client emails don't trigger it. So no calls.
    urgent_calls = [c for c in fake_tg.send_message.call_args_list
                    if "🔴" in c.args[0]]
    assert urgent_calls == []


def test_process_message_extracts_receipt_when_transactional(tmp_path, monkeypatch):
    """Transactional class triggers gemma4 receipt extraction; a confident
    extraction lands as a direction='in' ledger row."""
    from datetime import date
    from hermes.db import connect, migrate
    from hermes.extract.receipt import ReceiptInfo
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    msg = message_from_bytes((FIXTURES / "transactional_receipt.eml").read_bytes())
    fake_imap = MagicMock()
    fake_receipt = ReceiptInfo(
        vendor="Stripe", amount_cents=2900, currency="USD",
        line_items=("Pro plan",), date=date(2026, 5, 1), confidence=0.92,
    )
    extract_calls: list = []
    def fake_extract(m, *a, **kw):
        extract_calls.append(m)
        return fake_receipt
    monkeypatch.setattr(
        "hermes.extract.receipt.extract_receipt", fake_extract
    )
    process_message(uid=400, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    assert len(extract_calls) == 1
    rows = list(db.execute(
        "SELECT direction, vendor, amount_cents, status FROM ledger"
    ))
    assert rows == [("in", "Stripe", 2900, "paid")]


def test_process_message_skips_receipt_for_non_transactional(tmp_path, monkeypatch):
    """Client mail must NOT trigger the receipt extractor — saves an LLM
    call and avoids polluting the ledger with non-receipts."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    extract_calls: list = []
    def fake_extract(m, *a, **kw):
        extract_calls.append(m)
        raise AssertionError("extract_receipt should not be called for client mail")
    monkeypatch.setattr(
        "hermes.extract.receipt.extract_receipt", fake_extract
    )
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: MagicMock())
    process_message(uid=401, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    assert extract_calls == []
    rows = db.execute("SELECT COUNT(*) FROM ledger").fetchone()
    assert rows[0] == 0


def test_process_message_two_replies_same_thread_consolidate(tmp_path, monkeypatch):
    """Two messages with the same References root produce ONE actions row
    with thread_count=2 — not two separate rows. This is the whole point
    of Phase N.1: collapsing same-thread replies into one updating action
    instead of three urgent pings + three /actions cards."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    fake_brief = EmailBrief(
        summary="Sam asked again.",
        actions=("Reply to Sam",),
        tools=(),
        importance="normal",
    )
    monkeypatch.setattr(
        "hermes.mail.daemon.summarize_for_brief",
        lambda *a, **kw: fake_brief,
    )
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: MagicMock())
    fake_imap = MagicMock()

    def _raw(msg_id: str, in_reply_to: str | None) -> bytes:
        text = (
            "From: Sam Hogarth <sam@boarhog.example>\r\n"
            "To: dz@gmail.com\r\n"
            "Subject: Re: Quick question about hosting\r\n"
            "Date: Fri, 02 May 2026 10:00:00 +0000\r\n"
            f"Message-ID: <{msg_id}>\r\n"
        )
        if in_reply_to:
            text += (
                f"In-Reply-To: <{in_reply_to}>\r\n"
                f"References: <root@boarhog.example> <{in_reply_to}>\r\n"
            )
        else:
            text += "References: <root@boarhog.example>\r\n"
        text += "\r\nbody\r\n"
        return text.encode()

    msg1 = message_from_bytes(_raw("reply1@boarhog.example", None))
    msg2 = message_from_bytes(_raw("reply2@boarhog.example", "reply1@boarhog.example"))

    process_message(uid=600, msg=msg1, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    process_message(uid=601, msg=msg2, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)

    rows = db.execute(
        "SELECT id, thread_count, source_id, thread_key FROM actions"
    ).fetchall()
    assert len(rows) == 1
    aid, count, src_id, tkey = rows[0]
    assert count == 2
    # source_id was repointed to the LATEST message uid.
    assert src_id == 601
    assert tkey == "root@boarhog.example"


def test_process_message_persists_thread_key(tmp_path):
    """Inbound mail with a References header lands with thread_key set to
    the conversation root id, so subsequent replies in the same thread
    can be consolidated into one open action."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    raw = (
        b"From: Sam Hogarth <sam@boarhog.example>\r\n"
        b"To: dz@gmail.com\r\n"
        b"Subject: Re: Re: Quick question about hosting\r\n"
        b"Date: Fri, 02 May 2026 10:05:00 +0000\r\n"
        b"Message-ID: <reply2@boarhog.example>\r\n"
        b"In-Reply-To: <reply1@boarhog.example>\r\n"
        b"References: <root@boarhog.example> <reply1@boarhog.example>\r\n"
        b"\r\n"
        b"Following up on this.\r\n"
    )
    msg = message_from_bytes(raw)
    fake_imap = MagicMock()
    process_message(uid=500, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    row = db.execute(
        "SELECT thread_key FROM messages WHERE uid=500"
    ).fetchone()
    # First id in References is the root and our canonical thread_key.
    assert row[0] == "root@boarhog.example"


def test_process_message_thread_key_falls_back_to_message_id(tmp_path):
    """A new conversation (no References / In-Reply-To) gets its own
    Message-ID as thread_key — not None."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    process_message(uid=501, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    row = db.execute(
        "SELECT thread_key FROM messages WHERE uid=501"
    ).fetchone()
    assert row[0] == "bh-1@boarhog.example"


def test_known_client_does_not_get_lead_label(tmp_path, monkeypatch):
    """A message from a known client domain must not be flagged as a lead."""
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    fake_tg = MagicMock()
    monkeypatch.setattr("hermes.mail.daemon._telegram", lambda cfg: fake_tg)
    process_message(uid=201, msg=msg, db=db, cfg=_cfg(), imap=fake_imap,
                    llm_classifier=None, drafter=None)
    rows = list(db.execute("SELECT COUNT(*) FROM leads"))
    assert rows[0][0] == 0
    label_calls = [c.args[1] for c in fake_imap.apply_label.call_args_list]
    assert "Hermes/Lead" not in label_calls
    fake_tg.send_message.assert_not_called()


def _p1_cfg() -> Config:
    """Config with Phase 1 routing-only clients + Josh as partner."""
    return replace(
        _cfg(),
        clients={
            "tbn": ClientConfig(
                name="tbn", domain="tbn.net", contact_email="todd@tbn.net", active=True,
            ),
            "maxcrofton": ClientConfig(
                name="maxcrofton", contact_email="maxcrofton@gmail.com", active=True,
            ),
        },
        partners={"josh": ("magic@rainbowlasers.com", "joshualogan@gmail.com")},
    )


def _partner_msg(to: str, subject: str, body: str = "see below") -> "Message":
    from email.message import EmailMessage
    m = EmailMessage()
    m["From"] = "Rainbow Lasers <magic@rainbowlasers.com>"
    m["To"] = to
    m["Cc"] = "dzbeers747@gmail.com"
    m["Subject"] = subject
    m["Message-ID"] = "<p1-test@rainbowlasers.com>"
    m.set_content(body)
    return m


def test_partner_thread_resolves_to_client_and_archives(tmp_path):
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = _partner_msg(to="todd@tbn.net", subject="Re: Quote #1052221 / TBN")
    fake_imap = MagicMock()
    process_message(uid=20, msg=msg, db=db, cfg=_p1_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    fake_imap.apply_label.assert_called_once_with(20, "Hermes/Client/tbn")
    fake_imap.archive.assert_called_once_with(20)


def test_partner_thread_no_client_match_goes_review_stays_inbox(tmp_path):
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = _partner_msg(to="dzbeers747@gmail.com", subject="family bbq sunday")
    fake_imap = MagicMock()
    process_message(uid=21, msg=msg, db=db, cfg=_p1_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    fake_imap.apply_label.assert_called_once_with(21, "Hermes/Review")
    fake_imap.archive.assert_not_called()


def test_client_contact_email_sender_routes_and_archives(tmp_path):
    from email.message import EmailMessage
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    m = EmailMessage()
    m["From"] = "Max Crofton <maxcrofton@gmail.com>"
    m["To"] = "dzbeers747@gmail.com"
    m["Subject"] = "Re: website feedback"
    m["Message-ID"] = "<max-test@gmail.com>"
    m.set_content("Looks great")
    fake_imap = MagicMock()
    process_message(uid=22, msg=m, db=db, cfg=_p1_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    fake_imap.apply_label.assert_called_once_with(22, "Hermes/Client/maxcrofton")
    fake_imap.archive.assert_called_once_with(22)
