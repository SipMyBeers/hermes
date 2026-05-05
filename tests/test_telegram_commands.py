from pathlib import Path
from unittest.mock import MagicMock
from hermes.config import Config, GmailConfig, TelegramConfig
from hermes.db import connect, migrate
from hermes.telegram.commands import handle_update


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


def test_status_command(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/status"}}
    handle_update(update, _cfg(), db, tg)
    tg.send_message.assert_called_once()
    text = tg.send_message.call_args.args[0]
    assert "Hermes" in text
    assert "messages" in text.lower()


def test_unknown_command_replies_help(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/wat"}}
    handle_update(update, _cfg(), db, tg)
    tg.send_message.assert_called_once()
    assert "/status" in tg.send_message.call_args.args[0]


def test_approve_marks_outbox_sent(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO outbox (kind, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 'a@b.com', 's', 'b', 'queued', '2026-05-02')"
    )
    tg = MagicMock()
    # Patch the actual sender to avoid SMTP
    import hermes.telegram.commands as cmd
    cmd._do_send = MagicMock(return_value=True)
    update = {"update_id": 3, "message": {"chat": {"id": 42}, "text": "/approve 1"}}
    handle_update(update, _cfg(), db, tg)
    row = db.execute("SELECT status FROM outbox WHERE id=1").fetchone()
    assert row[0] == "sent"
    # Slash-command path => silent=False default => user gets text confirm.
    confirms = [c for c in tg.send_message.call_args_list
                if "Sent #1" in c.args[0]]
    assert len(confirms) == 1


def test_reject_slash_command_posts_text_confirmation(tmp_path):
    """The slash-command /reject path keeps the chat-level 'Rejected #N'
    confirmation — only the callback path goes silent."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO outbox (kind, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 'a@b.com', 's', 'b', 'queued', '2026-05-02')"
    )
    tg = MagicMock()
    update = {"update_id": 6, "message": {"chat": {"id": 42}, "text": "/reject 1"}}
    handle_update(update, _cfg(), db, tg)
    confirms = [c for c in tg.send_message.call_args_list
                if "Rejected #1" in c.args[0]]
    assert len(confirms) == 1


def test_other_chat_id_ignored(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 4, "message": {"chat": {"id": 99}, "text": "/status"}}
    handle_update(update, _cfg(), db, tg)
    tg.send_message.assert_not_called()


def test_approve_passes_attachments_through(tmp_path):
    """Regression: invoice /approve must include the PDF attachment."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    db.execute(
        "INSERT INTO outbox (kind, client, to_addr, subject, body, attachments, status, created_at) "
        "VALUES ('invoice', 'boarhog', 'a@b.com', 's', 'b', ?, 'queued', '2026-05-02')",
        (str(pdf),),
    )
    tg = MagicMock()
    import hermes.telegram.commands as cmd
    captured: dict = {}
    def fake_send(cfg, to_addr, subject, body, attachments=None):
        captured["attachments"] = list(attachments or [])
        return True
    cmd._do_send = fake_send
    update = {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/approve 1"}}
    handle_update(update, _cfg(), db, tg)
    assert len(captured["attachments"]) == 1
    assert captured["attachments"][0].name == "invoice.pdf"


def test_parse_attachments_handles_shapes():
    from hermes.telegram.commands import _parse_attachments
    from pathlib import Path
    assert _parse_attachments(None) == []
    assert _parse_attachments("") == []
    assert _parse_attachments("/tmp/x.pdf") == [Path("/tmp/x.pdf")]
    assert _parse_attachments('["/tmp/a.pdf","/tmp/b.pdf"]') == [Path("/tmp/a.pdf"), Path("/tmp/b.pdf")]
    assert _parse_attachments("[malformed") == [Path("[malformed")]  # falls back to single-path


def test_save_command_inserts_bookmark(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 10, "message": {"chat": {"id": 42},
              "text": "/save https://example.com/article reading later"}}
    handle_update(update, _cfg(), db, tg)
    rows = list(db.execute("SELECT url, notes FROM bookmarks"))
    assert rows == [("https://example.com/article", "reading later")]
    tg.send_message.assert_called_once()
    assert "Saved bookmark" in tg.send_message.call_args.args[0]


def test_save_rejects_non_url(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/save justtext"}}
    handle_update(update, _cfg(), db, tg)
    rows = list(db.execute("SELECT COUNT(*) FROM bookmarks"))
    assert rows[0][0] == 0
    assert "doesn't look like a URL" in tg.send_message.call_args.args[0]


def test_save_with_no_args_shows_usage(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 12, "message": {"chat": {"id": 42}, "text": "/save"}}
    handle_update(update, _cfg(), db, tg)
    assert "Usage:" in tg.send_message.call_args.args[0]


def test_bare_url_triggers_read(tmp_path):
    """A plain URL (no /read prefix) should be ingested."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    captured = {}
    def fake_ingest(url, db, source_chat_id=None, cookies_path="", cookies_from_browser=()):
        captured["url"] = url
        from hermes.ingest.url import Digestion
        return Digestion(id=1, url=url, kind="web", title="T", summary="S",
                         actions=(), tools=(), error="")
    import hermes.telegram.commands as cmd
    cmd.ingest = fake_ingest
    update = {"update_id": 50, "message": {"chat": {"id": 42},
              "text": "https://example.com/article"}}
    handle_update(update, _cfg(), db, tg)
    assert captured.get("url") == "https://example.com/article"


def test_read_command_with_no_args_shows_usage(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 51, "message": {"chat": {"id": 42}, "text": "/read"}}
    handle_update(update, _cfg(), db, tg)
    assert "Usage:" in tg.send_message.call_args.args[0]


def test_actions_command_lists_open(tmp_path):
    """/actions sends a header + group label + one card per action.

    Each card is its own send_message call carrying an inline keyboard with
    Done/Snooze/Dismiss buttons that callback to action-id-bearing payloads.
    """
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    ids = add_actions(db, source_kind="email", source_id=10,
                     texts=["Send invoice", "Reply to Casey"],
                     source_subject="Re: invoice", source_sender="casey@x.com")
    tg = MagicMock()
    update = {"update_id": 100, "message": {"chat": {"id": 42}, "text": "/actions"}}
    handle_update(update, _cfg(), db, tg)
    # Header + one group label + 2 cards = 4 calls.
    assert tg.send_message.call_count == 4
    texts = [c.args[0] for c in tg.send_message.call_args_list]
    assert "Open actions (2)" in texts[0]
    # Concatenate everything we sent and check both action texts surface.
    flat = "\n".join(texts)
    assert "Send invoice" in flat
    assert "Reply to Casey" in flat
    # The two cards must each carry a Done/Snooze/Dismiss keyboard with the
    # matching action id.
    card_calls = [c for c in tg.send_message.call_args_list
                  if c.kwargs.get("reply_markup")]
    assert len(card_calls) == 2
    seen_ids = set()
    for c in card_calls:
        kb = c.kwargs["reply_markup"]
        cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r]
        # exactly one done:<id> per card
        for cb in cbs:
            if cb.startswith("done:"):
                seen_ids.add(int(cb.split(":")[1]))
    assert seen_ids == set(ids)


def test_actions_command_each_card_has_buttons(tmp_path):
    """Every per-action card carries the three-button row."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    add_actions(db, source_kind="email", source_id=11, texts=["x"])
    tg = MagicMock()
    update = {"update_id": 110, "message": {"chat": {"id": 42}, "text": "/actions"}}
    handle_update(update, _cfg(), db, tg)
    card_calls = [c for c in tg.send_message.call_args_list
                  if c.kwargs.get("reply_markup")]
    assert len(card_calls) == 1
    kb = card_calls[0].kwargs["reply_markup"]
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r]
    assert any(cb.startswith("done:") for cb in cbs)
    assert any(cb.startswith("snooze:") for cb in cbs)
    assert any(cb.startswith("dismiss:") for cb in cbs)


def test_actions_command_empty_state(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 101, "message": {"chat": {"id": 42}, "text": "/actions"}}
    handle_update(update, _cfg(), db, tg)
    assert tg.send_message.call_args.args[0] == "No open actions. ✓"


def test_done_command_marks_action(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    ids = add_actions(db, source_kind="email", source_id=1, texts=["x"])
    tg = MagicMock()
    update = {"update_id": 102, "message": {"chat": {"id": 42},
              "text": f"/done {ids[0]}"}}
    handle_update(update, _cfg(), db, tg)
    row = db.execute("SELECT status FROM actions WHERE id=?", (ids[0],)).fetchone()
    assert row[0] == "done"
    assert "Done" in tg.send_message.call_args.args[0]


def test_dismiss_command_marks_action(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    ids = add_actions(db, source_kind="email", source_id=1, texts=["x"])
    tg = MagicMock()
    update = {"update_id": 103, "message": {"chat": {"id": 42},
              "text": f"/dismiss {ids[0]}"}}
    handle_update(update, _cfg(), db, tg)
    row = db.execute("SELECT status FROM actions WHERE id=?", (ids[0],)).fetchone()
    assert row[0] == "dismissed"
    assert "Dismissed" in tg.send_message.call_args.args[0]


def test_snooze_with_relative(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    ids = add_actions(db, source_kind="email", source_id=1, texts=["x"])
    tg = MagicMock()
    update = {"update_id": 104, "message": {"chat": {"id": 42},
              "text": f"/snooze {ids[0]} +3d"}}
    handle_update(update, _cfg(), db, tg)
    row = db.execute(
        "SELECT status, snoozed_until FROM actions WHERE id=?", (ids[0],)
    ).fetchone()
    assert row[0] == "snoozed"
    assert row[1] is not None
    assert "Snoozed" in tg.send_message.call_args.args[0]


def test_start_command_posts_dashboard_with_button(tmp_path):
    """/start replies with a dashboard keyboard offering 'What do I need to
    do?' and 'Today's digest' buttons — no slash commands required."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    add_actions(db, source_kind="email", source_id=1, texts=["x", "y"])
    tg = MagicMock()
    update = {"update_id": 200, "message": {"chat": {"id": 42}, "text": "/start"}}
    handle_update(update, _cfg(), db, tg)
    tg.send_message.assert_called_once()
    text = tg.send_message.call_args.args[0]
    assert "Hermes ready" in text
    assert "2 open actions" in text
    kb = tg.send_message.call_args.kwargs.get("reply_markup")
    assert kb is not None
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r]
    assert "actions" in cbs
    assert "digest" in cbs


def test_start_command_singular_when_one_action(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    add_actions(db, source_kind="email", source_id=1, texts=["x"])
    tg = MagicMock()
    update = {"update_id": 201, "message": {"chat": {"id": 42}, "text": "/start"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "1 open action" in text
    assert "1 open actions" not in text


def test_start_command_empty_state(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 202, "message": {"chat": {"id": 42}, "text": "/start"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "no open actions" in text


def test_help_text_focuses_on_tap_not_type(tmp_path):
    """The friendly intro should foreground 'tap' over a wall of slash commands."""
    from hermes.telegram.commands import HELP_TEXT
    assert "Tap the buttons" in HELP_TEXT
    # Power-user index still exists, but the intro must come first.
    tap_idx = HELP_TEXT.find("Tap")
    cmds_idx = HELP_TEXT.find("Slash-command index")
    assert 0 <= tap_idx < cmds_idx


def test_snooze_with_unparseable_when(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    ids = add_actions(db, source_kind="email", source_id=1, texts=["x"])
    tg = MagicMock()
    update = {"update_id": 105, "message": {"chat": {"id": 42},
              "text": f"/snooze {ids[0]} fubar"}}
    handle_update(update, _cfg(), db, tg)
    row = db.execute("SELECT status FROM actions WHERE id=?", (ids[0],)).fetchone()
    assert row[0] == "open"  # untouched
    assert "Couldn't parse" in tg.send_message.call_args.args[0]


# ---------- /draft -----------------------------------------------------------


def _seed_message(db, *, uid: int, sender: str = "Casey <casey@x.com>",
                  subject: str = "Project inquiry",
                  draft_text: str | None = None,
                  raw_email: bytes | None = None) -> None:
    """Helper: insert a messages row for /draft to operate on."""
    from datetime import datetime, timezone
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, subject, "
        "classification, classifier, draft_text, raw_email) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, f"m-{uid}",
         datetime.now(timezone.utc).isoformat(),
         sender, subject, "client", "rule",
         draft_text, raw_email),
    )


def test_draft_uses_cached_draft_text(tmp_path):
    """When messages.draft_text is already populated, /draft skips the
    LLM and queues the cached body straight to outbox."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    _seed_message(db, uid=42, draft_text="Already drafted reply.")
    tg = MagicMock()
    update = {"update_id": 300, "message": {"chat": {"id": 42},
              "text": "/draft 42"}}
    handle_update(update, _cfg(), db, tg)
    rows = list(db.execute(
        "SELECT kind, related_uid, to_addr, subject, body, status FROM outbox"
    ))
    assert len(rows) == 1
    kind, related_uid, to_addr, subject, body, status = rows[0]
    assert kind == "reply"
    assert related_uid == 42
    assert to_addr == "casey@x.com"
    assert subject == "Re: Project inquiry"
    assert body == "Already drafted reply."
    assert status == "queued"
    # The user-facing card carries an approval_card keyboard.
    last = tg.send_message.call_args
    kb = last.kwargs.get("reply_markup")
    assert kb is not None
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r]
    assert any(cb.startswith("approve:") for cb in cbs)
    assert any(cb.startswith("reject:") for cb in cbs)


def test_draft_generates_when_no_cache(tmp_path, monkeypatch):
    """No cached draft_text but raw_email present → run drafter, persist
    the result, queue outbox."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    raw = (
        b"From: Casey <casey@x.com>\r\n"
        b"To: u@g.com\r\n"
        b"Subject: Project inquiry\r\n"
        b"\r\n"
        b"Original body.\r\n"
    )
    _seed_message(db, uid=43, draft_text=None, raw_email=raw)

    captured = {}

    def fake_drafter(msg, sender_name="Hermes", **kwargs):
        captured["sender_name"] = sender_name
        captured["from_"] = str(msg.get("From", ""))
        return "fresh draft body"

    # Patched at the import site _cmd_draft uses (local import).
    monkeypatch.setattr(
        "hermes.classifier.drafter.draft_reply", fake_drafter
    )
    tg = MagicMock()
    update = {"update_id": 301, "message": {"chat": {"id": 42},
              "text": "/draft 43"}}
    handle_update(update, _cfg(), db, tg)
    # Outbox queued with the freshly drafted body.
    out = db.execute(
        "SELECT body, related_uid FROM outbox WHERE related_uid=43"
    ).fetchone()
    assert out is not None
    assert out[0] == "fresh draft body"
    # Cache populated for the next /draft on the same uid.
    cached = db.execute(
        "SELECT draft_text, drafted_at FROM messages WHERE uid=43"
    ).fetchone()
    assert cached[0] == "fresh draft body"
    assert cached[1] is not None
    # Drafter was actually called with the right inputs.
    assert "Casey" in captured["from_"]


def test_draft_no_message_says_so(tmp_path):
    """/draft on a uid that doesn't exist gets a friendly 'not found'
    instead of a stack trace."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 302, "message": {"chat": {"id": 42},
              "text": "/draft 999"}}
    handle_update(update, _cfg(), db, tg)
    rows = list(db.execute("SELECT COUNT(*) FROM outbox"))
    assert rows[0][0] == 0
    text = tg.send_message.call_args.args[0]
    assert "No message found" in text


def test_draft_unparseable_sender(tmp_path):
    """Empty/malformed From header → friendly error, no outbox row."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    _seed_message(db, uid=44, sender="", draft_text="ready to send")
    tg = MagicMock()
    update = {"update_id": 303, "message": {"chat": {"id": 42},
              "text": "/draft 44"}}
    handle_update(update, _cfg(), db, tg)
    rows = list(db.execute("SELECT COUNT(*) FROM outbox"))
    assert rows[0][0] == 0
    text = tg.send_message.call_args.args[0]
    assert "no parseable sender" in text


def test_draft_drafter_returns_empty(tmp_path, monkeypatch):
    """LLM returned an empty string (Ollama down or refused) → friendly
    error, NO outbox row, NO cached draft."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    raw = (
        b"From: Casey <casey@x.com>\r\n"
        b"Subject: x\r\n"
        b"\r\n"
        b"body\r\n"
    )
    _seed_message(db, uid=45, draft_text=None, raw_email=raw)
    monkeypatch.setattr(
        "hermes.classifier.drafter.draft_reply",
        lambda msg, sender_name="Hermes", **kwargs: "",
    )
    tg = MagicMock()
    update = {"update_id": 304, "message": {"chat": {"id": 42},
              "text": "/draft 45"}}
    handle_update(update, _cfg(), db, tg)
    rows = list(db.execute("SELECT COUNT(*) FROM outbox"))
    assert rows[0][0] == 0
    cached = db.execute(
        "SELECT draft_text FROM messages WHERE uid=45"
    ).fetchone()
    assert cached[0] is None
    text = tg.send_message.call_args.args[0]
    assert "couldn't draft" in text


def test_draft_does_not_double_re_prefix(tmp_path):
    """Subject already prefixed with 'Re:' must not stack into 'Re: Re: ...'."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    _seed_message(db, uid=46, subject="Re: original thread",
                  draft_text="ack")
    tg = MagicMock()
    update = {"update_id": 305, "message": {"chat": {"id": 42},
              "text": "/draft 46"}}
    handle_update(update, _cfg(), db, tg)
    out = db.execute(
        "SELECT subject FROM outbox WHERE related_uid=46"
    ).fetchone()
    assert out[0] == "Re: original thread"


def test_draft_no_raw_email_no_cache_says_so(tmp_path):
    """No cached draft AND no raw_email → can't generate, friendly error."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    _seed_message(db, uid=47, draft_text=None, raw_email=None)
    tg = MagicMock()
    update = {"update_id": 306, "message": {"chat": {"id": 42},
              "text": "/draft 47"}}
    handle_update(update, _cfg(), db, tg)
    rows = list(db.execute("SELECT COUNT(*) FROM outbox"))
    assert rows[0][0] == 0
    text = tg.send_message.call_args.args[0]
    assert "no raw email persisted" in text


def test_draft_with_no_args_shows_usage(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 307, "message": {"chat": {"id": 42}, "text": "/draft"}}
    handle_update(update, _cfg(), db, tg)
    assert "Usage:" in tg.send_message.call_args.args[0]
