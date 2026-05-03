from datetime import datetime, timezone
from pathlib import Path
from hermes.config import Config, GmailConfig, TelegramConfig
from hermes.db import connect, migrate
from hermes.digest.build import build_morning_digest
from hermes.digest.enrich import EmailBrief, Tool


# Default enricher for tests: never call gemma4. Returns None so the digest
# falls back to the bare sender/subject line.
_NO_ENRICH = lambda raw: None


def _cfg():
    return Config(
        gmail=GmailConfig(
            imap_user="u@g.com", imap_app_password="p",
            smtp_user="u@g.com", smtp_app_password="p",
            from_address="U <u@g.com>", reply_to="U <u@x.com>",
        ),
        telegram=TelegramConfig(bot_token="1:A", chat_id=42),
        digest_evening_report=False,
        clients={},
    )


def test_digest_with_no_data(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    text = build_morning_digest(db, _cfg(), enrich_one=_NO_ENRICH)
    assert "Hermes morning report" in text
    assert "no messages" in text.lower() or "0 promo" in text.lower()


def test_digest_counts_messages_by_class(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (1, "m1", now, "a@b.com", "Hi", "promo", "rule"),
        (2, "m2", now, "c@d.com", "Hi", "promo", "rule"),
        (3, "m3", now, "e@f.com", "Question", "client", "rule"),
    ]
    db.executemany(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    text = build_morning_digest(db, _cfg(), enrich_one=_NO_ENRICH)
    assert "promo: 2" in text.lower()
    assert "client: 1" in text.lower()


def test_digest_lists_pending_outbox(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO outbox(kind, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 'a@b.com', 'Re: Q', 'b', 'queued', '2026-05-02')"
    )
    text = build_morning_digest(db, _cfg(), enrich_one=_NO_ENRICH)
    assert "pending approval" in text.lower()
    assert "Re: Q" in text


def test_digest_skips_promos_and_transactional_in_per_email_block(tmp_path):
    """Per-email enriched section excludes promo and transactional."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (1, "m1", now, "spam@b.com", "50% OFF", "promo", "rule"),
        (2, "m2", now, "stripe@b.com", "Receipt", "transactional", "rule"),
        (3, "m3", now, "real@b.com", "Project update", "client", "rule"),
    ]
    db.executemany(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    text = build_morning_digest(db, _cfg(), enrich_one=_NO_ENRICH)
    assert "Overnight" in text
    assert "Project update" in text
    # promo / transactional must NOT show up in the per-email blocks
    assert "50% OFF" not in text
    assert "Receipt" not in text


def test_digest_includes_per_email_brief_when_enrichment_returns_data(tmp_path):
    """Injected enricher's summary, actions, and tools render in the brief."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (42, "m42", now, "sam@boarhog.example", "Renewal quote", "client", "rule", b"raw bytes"),
    )

    fake_brief = EmailBrief(
        summary="Sam wants a hosting renewal quote for next quarter.",
        actions=("Send renewal terms", "Generate invoice"),
        tools=(Tool(name="Stripe", description="payments platform"),),
    )

    def fake_enrich(raw: bytes | None):
        # Sanity: the digest must hand us the persisted bytes.
        assert raw == b"raw bytes"
        return fake_brief

    text = build_morning_digest(db, _cfg(), enrich_one=fake_enrich)
    assert "[42]" in text
    assert "Renewal quote" in text
    assert "Sam wants a hosting renewal quote" in text
    assert "Send renewal terms" in text
    assert "Generate invoice" in text
    assert "Stripe" in text
    assert "payments platform" in text


def test_digest_handles_empty_brief_gracefully(tmp_path):
    """When enrichment returns an empty EmailBrief (Ollama down), still show sender/subject."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (7, "m7", now, "sam@boarhog.example", "Quick question", "client", "rule", b"raw"),
    )
    empty = EmailBrief(summary="", actions=(), tools=())
    text = build_morning_digest(db, _cfg(), enrich_one=lambda raw: empty)
    assert "[7]" in text
    assert "Quick question" in text
    # No "Actions:" / "Tools mentioned:" headers since the brief is empty.
    assert "Actions:" not in text
    assert "Tools mentioned:" not in text


def test_digest_handles_missing_raw_email(tmp_path):
    """Older rows with raw_email=NULL get bare sender/subject lines."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (5, "m5", now, "old@boarhog.example", "Pre-migration mail", "client", "rule", None),
    )
    # Use the real default enricher path — it must short-circuit on raw=None
    # without ever calling gemma4. We assert by passing a sentinel that would
    # fail loudly if invoked with non-None.
    sentinel_called_with = []

    def trap(raw):
        sentinel_called_with.append(raw)
        return None  # default-enricher behaviour for raw=None

    text = build_morning_digest(db, _cfg(), enrich_one=trap)
    assert sentinel_called_with == [None]
    assert "Pre-migration mail" in text


def test_digest_excludes_bootstrap_sentinel_row(tmp_path):
    """The first-run anchor row (sender '<bootstrap>') must not surface
    in the per-email block."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (44385, "hermes-bootstrap-44385", now, "<bootstrap>",
         "<initial anchor>", "other", "user", 1.0),
    )
    text = build_morning_digest(db, _cfg(), enrich_one=_NO_ENRICH)
    assert "<bootstrap>" not in text
    assert "<initial anchor>" not in text


def test_digest_decodes_encoded_word_subjects(tmp_path):
    """RFC 2047 =?UTF-8?B?...?= subjects must be rendered as plain text."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    encoded = "=?UTF-8?B?SGVsbG8gV29ybGQ=?="  # decodes to 'Hello World'
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (1001, "m-1001", now, "Casey <casey@x.com>", encoded,
         "review", "llm", 0.6),
    )
    text = build_morning_digest(db, _cfg(), enrich_one=_NO_ENRICH)
    assert "Hello World" in text
    assert "=?UTF-8?B?" not in text
