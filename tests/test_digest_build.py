from datetime import datetime, timezone
from pathlib import Path
from hermes.config import Config, GmailConfig, TelegramConfig
from hermes.db import connect, migrate
from hermes.digest.build import build_morning_digest
from hermes.digest.enrich import EmailBrief, Tool


# Default enricher for tests: never call gemma4. Returns None which under
# the post-J.1 filter suppresses the row from the per-email block. Tests
# that need the row to actually render should use _ALWAYS_BRIEF instead.
_NO_ENRICH = lambda raw: None
_ALWAYS_BRIEF = lambda raw: EmailBrief(
    summary="(test summary)", actions=(), tools=(), importance="normal"
)


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
    """Per-email enriched section excludes promo and transactional.

    The classification filter happens before enrichment, so promos and
    receipts never reach the enricher in the first place. With None briefs
    suppressed under the new filter, the surviving 'client' row only
    renders if its enricher returns a real summary."""
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
    fake_brief = EmailBrief(summary="Real client message.", actions=(), tools=(),
                             importance="normal")
    text = build_morning_digest(db, _cfg(), enrich_one=lambda raw: fake_brief)
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
        importance="normal",
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
    """When enrichment returns an empty EmailBrief (Ollama down or LLM
    flagged the row as nothing-to-say), the row is suppressed and the
    Overnight section reports the filtered count instead of leaking
    sender/subject without context."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (7, "m7", now, "sam@boarhog.example", "Quick question", "client", "rule", b"raw"),
    )
    empty = EmailBrief(summary="", actions=(), tools=(), importance="normal")
    text = build_morning_digest(db, _cfg(), enrich_one=lambda raw: empty)
    assert "Quick question" not in text
    assert "all routine" in text


def test_digest_handles_missing_raw_email(tmp_path):
    """Older rows with raw_email=NULL get suppressed under the new filter
    (no brief = no signal to render). The enricher still gets called with
    None so the default-enricher contract stays correct."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (5, "m5", now, "old@boarhog.example", "Pre-migration mail", "client", "rule", None),
    )
    sentinel_called_with = []

    def trap(raw):
        sentinel_called_with.append(raw)
        return None  # default-enricher behaviour for raw=None

    text = build_morning_digest(db, _cfg(), enrich_one=trap)
    assert sentinel_called_with == [None]
    # Suppressed: no subject line, but the count message acknowledges it.
    assert "Pre-migration mail" not in text
    assert "all routine" in text


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


def test_short_sender_extracts_display_name():
    from hermes.digest.build import _short_sender
    assert _short_sender("Casey <casey@unknown.io>") == "Casey"
    assert _short_sender("casey@unknown.io") == "casey@unknown.io"
    assert _short_sender("") == ""
    assert _short_sender("\"Casey, CTO\" <casey@x.com>") == "Casey, CTO"


def test_short_subject_truncates_and_collapses():
    from hermes.digest.build import _short_subject
    assert _short_subject("hello") == "hello"
    long = "a" * 100
    short = _short_subject(long, max_len=80)
    assert len(short) <= 80
    assert short.endswith("…")
    assert _short_subject("a\nb\rc") == "a b c"


def test_digest_uses_short_sender_in_per_email_block(tmp_path):
    """Verbose 'Display <addr>' should render as just 'Display' in the brief."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (501, "m-501", now, "CoinTracker <hello@mail.cointracker.io>",
         "Crypto portfolio update", "review", "llm", 0.6),
    )
    text = build_morning_digest(db, _cfg(), enrich_one=_ALWAYS_BRIEF)
    assert "CoinTracker" in text
    assert "hello@mail.cointracker.io" not in text


def test_digest_skips_tools_block_when_tool_name_in_subject(tmp_path):
    """If the email is *about* CoinTracker and the only tool spotted is
    CoinTracker, don't surface the redundant 'Tools mentioned' block."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (502, "m-502", now, "CoinTracker <hello@cointracker.io>",
         "Crypto portfolio update from CoinTracker", "review", "llm", 0.6),
    )
    fake_brief = EmailBrief(summary="Sync your wallets.", actions=("Sync",),
                             tools=(Tool(name="CoinTracker", description="x"),))
    text = build_morning_digest(db, _cfg(), enrich_one=lambda raw: fake_brief)
    assert "Tools mentioned" not in text


def test_digest_filters_noise_importance(tmp_path):
    """importance='noise' rows must not appear in the per-email block."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (910, "m-910", now, "promos@tubi.tv", "New on Tubi this week",
         "other", "rule", b"raw"),
    )
    noise_brief = EmailBrief(
        summary="Generic streaming promo.", actions=(), tools=(), importance="noise"
    )
    text = build_morning_digest(db, _cfg(), enrich_one=lambda raw: noise_brief)
    assert "New on Tubi" not in text
    assert "all routine" in text


def test_digest_filters_empty_summary(tmp_path):
    """Empty summary => row gets suppressed even if importance='normal'."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (911, "m-911", now, "noreply@x.com", "Confirmation #4581",
         "other", "rule", b"raw"),
    )
    blank = EmailBrief(summary="", actions=(), tools=(), importance="normal")
    text = build_morning_digest(db, _cfg(), enrich_one=lambda raw: blank)
    assert "Confirmation #4581" not in text


def test_digest_sorts_urgent_first(tmp_path):
    """When two rows survive, the urgent one renders first with the 🔴 badge."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        # Insert normal first to prove the sort actually moves urgent above it.
        (920, "m-920", now, "casual@x.com", "Routine ping", "client", "rule", b"raw-n"),
        (921, "m-921", now, "boss@x.com", "Site is down", "client", "rule", b"raw-u"),
    ]
    db.executemany(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    def by_raw(raw):
        if raw == b"raw-u":
            return EmailBrief(summary="Production is offline.", actions=(),
                              tools=(), importance="urgent")
        return EmailBrief(summary="Status update.", actions=(), tools=(),
                          importance="normal")

    text = build_morning_digest(db, _cfg(), enrich_one=by_raw)
    urgent_pos = text.find("Site is down")
    normal_pos = text.find("Routine ping")
    assert urgent_pos != -1 and normal_pos != -1
    assert urgent_pos < normal_pos
    # 🔴 badge appears on the urgent line.
    urgent_line = [ln for ln in text.splitlines() if "Site is down" in ln][0]
    assert "🔴" in urgent_line
    normal_line = [ln for ln in text.splitlines() if "Routine ping" in ln][0]
    assert "🔴" not in normal_line


def test_digest_shows_filtered_count_when_anything_suppressed(tmp_path):
    """When some rows survive and some get filtered, the header reports both."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (930, "m-930", now, "real@x.com", "Real email", "client", "rule", b"keep"),
        (931, "m-931", now, "promo@x.com", "Junk", "other", "rule", b"drop"),
    ]
    db.executemany(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    def by_raw(raw):
        if raw == b"keep":
            return EmailBrief(summary="Real content.", actions=(), tools=(),
                              importance="normal")
        return EmailBrief(summary="x", actions=(), tools=(), importance="noise")

    text = build_morning_digest(db, _cfg(), enrich_one=by_raw)
    assert "1 to review" in text
    assert "noise filtered" in text


def test_digest_truncation_count_separates_noise_from_unprocessed(tmp_path):
    """17 interesting rows (>15 cap) + 3 of the first 15 returned as noise.
    The header must show '12 to review · 3 noise filtered · 2 not yet
    processed' — three distinct buckets, never collapsed."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (700 + i, f"m-{700+i}", now, f"s{i}@x.com", f"Subject {i}",
         "client", "rule", f"raw-{i}".encode())
        for i in range(17)
    ]
    db.executemany(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    # The most recent 15 rows are i=2..16 (DESC by received_at, then UID
    # tiebreak picks higher first). Mark 3 of those 15 as noise.
    noise_raws = {b"raw-16", b"raw-15", b"raw-14"}

    def by_raw(raw: bytes | None):
        if raw in noise_raws:
            return EmailBrief(summary="x", actions=(), tools=(),
                              importance="noise")
        return EmailBrief(summary="content", actions=(), tools=(),
                          importance="normal")

    text = build_morning_digest(db, _cfg(), enrich_one=by_raw)
    # 15 enriched - 3 noise = 12 to review.
    # 17 total - 15 cap = 2 not-yet-processed.
    assert "12 to review" in text
    assert "3 noise filtered" in text
    assert "2 not yet processed" in text
    # Old "...truncated" footer must be gone — the suffix already reports it.
    assert "truncated" not in text


def test_digest_truncation_only_when_overflow(tmp_path):
    """Exactly 15 rows: no truncation note, no 'not yet processed' suffix."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (800 + i, f"m-{800+i}", now, f"s{i}@x.com", f"Subject {i}",
         "client", "rule", f"r-{i}".encode())
        for i in range(15)
    ]
    db.executemany(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    text = build_morning_digest(
        db, _cfg(),
        enrich_one=lambda raw: EmailBrief(
            summary="x", actions=(), tools=(), importance="normal"
        ),
    )
    assert "15 to review" in text
    assert "not yet processed" not in text
    assert "noise filtered" not in text
    assert "truncated" not in text


def test_digest_includes_open_actions_summary(tmp_path):
    """The brief surfaces the open-action queue depth + top items so the
    user sees what's pending without a separate /actions roundtrip."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    add_actions(db, source_kind="email", source_id=1,
                texts=["Send the renewal quote", "Reply to Sam"],
                importance="normal",
                source_subject="Re: renewal", source_sender="sam@x.com")
    text = build_morning_digest(db, _cfg(), enrich_one=_NO_ENRICH)
    assert "Open actions (2)" in text
    assert "Send the renewal quote" in text
    assert "Reply to Sam" in text


def test_digest_omits_open_actions_when_zero(tmp_path):
    """No open actions => no Open-actions header (don't pollute the brief
    with a 0-count ghost section)."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    text = build_morning_digest(db, _cfg(), enrich_one=_NO_ENRICH)
    assert "Open actions" not in text


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
    text = build_morning_digest(db, _cfg(), enrich_one=_ALWAYS_BRIEF)
    assert "Hello World" in text
    assert "=?UTF-8?B?" not in text
