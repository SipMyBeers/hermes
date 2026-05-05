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


def test_process_message_does_not_archive_client(tmp_path):
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    process_message(uid=12, msg=msg, db=db, cfg=_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    fake_imap.archive.assert_not_called()


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


def test_process_message_persists_actions_for_client(tmp_path, monkeypatch):
    """Client emails get enriched and any extracted actions land in the
    actions table tied back to the message uid."""
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
        "SELECT source_kind, source_id, text, importance, status FROM actions"
    ))
    assert len(rows) == 2
    assert rows[0] == ("email", 300, "Send renewal terms", "normal", "open")
    assert rows[1] == ("email", 300, "Generate invoice", "normal", "open")


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
    """When actions were extracted, the urgent ping carries a Done/Snooze/
    Dismiss inline keyboard wired to the first action's id."""
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
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r]
    # First action id was inserted as the only row -> id 1.
    aid = db.execute("SELECT id FROM actions").fetchone()[0]
    assert f"done:{aid}" in cbs
    assert f"snooze:{aid}" in cbs
    assert f"dismiss:{aid}" in cbs


def test_urgent_inbox_ping_without_actions_has_no_button(tmp_path, monkeypatch):
    """If the LLM returned a summary but zero actions, the urgent ping
    still fires but without a keyboard (no id to bind to)."""
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
    assert urgent_calls[0].kwargs.get("reply_markup") is None


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
