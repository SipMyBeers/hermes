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
    def fake_ingest(url, db, source_chat_id=None, cookies_path="",
                    cookies_from_browser=(), depth=0):
        captured["url"] = url
        captured["depth"] = depth
        from hermes.ingest.url import Digestion
        return Digestion(id=1, url=url, kind="web", title="T", summary="S",
                         actions=(), tools=(), error="")
    import hermes.telegram.commands as cmd
    cmd.ingest = fake_ingest
    update = {"update_id": 50, "message": {"chat": {"id": 42},
              "text": "https://example.com/article"}}
    handle_update(update, _cfg(), db, tg)
    assert captured.get("url") == "https://example.com/article"
    # Bare URL → single-page (depth=0).
    assert captured.get("depth") == 0


def test_read_command_with_no_args_shows_usage(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 51, "message": {"chat": {"id": 42}, "text": "/read"}}
    handle_update(update, _cfg(), db, tg)
    assert "Usage:" in tg.send_message.call_args.args[0]


def _fake_ingest_capture(captured: dict):
    """Build a fake ingest() that records depth and returns a stub Digestion."""
    def fake_ingest(url, db, source_chat_id=None, cookies_path="",
                    cookies_from_browser=(), depth=0):
        captured["url"] = url
        captured["depth"] = depth
        from hermes.ingest.url import Digestion
        return Digestion(id=1, url=url, kind="web", title="T", summary="S",
                         actions=(), tools=(), error="")
    return fake_ingest


def test_read_command_accepts_depth_flag(tmp_path):
    """`/read <url> --depth 2` forwards depth=2 into ingest()."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    captured = {}
    import hermes.telegram.commands as cmd
    cmd.ingest = _fake_ingest_capture(captured)
    update = {"update_id": 60, "message": {"chat": {"id": 42},
              "text": "/read https://docs.example.com --depth 2"}}
    handle_update(update, _cfg(), db, tg)
    assert captured.get("depth") == 2


def test_read_command_caps_depth_at_5(tmp_path):
    """A user typing --depth 99 is clamped to 5 server-side."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    captured = {}
    import hermes.telegram.commands as cmd
    cmd.ingest = _fake_ingest_capture(captured)
    update = {"update_id": 61, "message": {"chat": {"id": 42},
              "text": "/read https://docs.example.com --depth 99"}}
    handle_update(update, _cfg(), db, tg)
    assert captured.get("depth") == 5


def test_read_command_invalid_depth_shows_usage(tmp_path):
    """`--depth notanumber` → usage hint, no ingest call."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    captured = {}
    import hermes.telegram.commands as cmd
    cmd.ingest = _fake_ingest_capture(captured)
    update = {"update_id": 62, "message": {"chat": {"id": 42},
              "text": "/read https://docs.example.com --depth notanumber"}}
    handle_update(update, _cfg(), db, tg)
    # Ingest was not called (no captured url), and we got a usage message.
    assert "url" not in captured
    msgs = [c.args[0] for c in tg.send_message.call_args_list]
    assert any("Usage:" in m for m in msgs)


def test_read_command_depth_acks_show_crawl(tmp_path):
    """The 'Reading…' ack should mention the crawl depth so the user
    knows it isn't a single-page fetch."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    captured = {}
    import hermes.telegram.commands as cmd
    cmd.ingest = _fake_ingest_capture(captured)
    update = {"update_id": 63, "message": {"chat": {"id": 42},
              "text": "/read https://docs.example.com --depth 3"}}
    handle_update(update, _cfg(), db, tg)
    msgs = [c.args[0] for c in tg.send_message.call_args_list]
    assert any("crawl depth=3" in m for m in msgs)


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


def test_actions_card_shows_thread_count_when_gt_one(tmp_path):
    """A thread-consolidated action with count >1 surfaces '(N messages)'
    in its card body so the user sees how many replies have piled up."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_or_consolidate_email_action
    add_or_consolidate_email_action(
        db, thread_key="root@x.com", source_id=1,
        text="Reply to Joseph", importance="normal",
        source_sender="joseph@x.com", source_subject="Project sync",
    )
    add_or_consolidate_email_action(
        db, thread_key="root@x.com", source_id=2,
        text="Reply to Joseph", importance="normal",
        source_sender="joseph@x.com", source_subject="Re: Project sync",
    )
    add_or_consolidate_email_action(
        db, thread_key="root@x.com", source_id=3,
        text="Reply to Joseph", importance="normal",
        source_sender="joseph@x.com", source_subject="Re: Re: Project sync",
    )
    tg = MagicMock()
    update = {"update_id": 200, "message": {"chat": {"id": 42}, "text": "/actions"}}
    handle_update(update, _cfg(), db, tg)
    flat = "\n".join(c.args[0] for c in tg.send_message.call_args_list)
    assert "(3 messages)" in flat


def test_actions_card_omits_count_for_single_thread(tmp_path):
    """A fresh single-message action (thread_count=1) must NOT print
    '(1 messages)' — that's chatter, not signal."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_or_consolidate_email_action
    add_or_consolidate_email_action(
        db, thread_key="root@x.com", source_id=1,
        text="Reply to Joseph", importance="normal",
        source_sender="joseph@x.com",
    )
    tg = MagicMock()
    update = {"update_id": 201, "message": {"chat": {"id": 42}, "text": "/actions"}}
    handle_update(update, _cfg(), db, tg)
    flat = "\n".join(c.args[0] for c in tg.send_message.call_args_list)
    assert "(1 messages)" not in flat
    assert "messages)" not in flat


def test_actions_email_action_includes_gmail_link(tmp_path):
    """Phase N.2: an open action whose source_kind=='email' and whose
    source_id points to a messages row carrying a real Message-ID
    surfaces a 📬 Open in Gmail button on its card. The button is a
    url-button (not callback) carrying the rfc822msgid search URL."""
    from datetime import datetime, timezone
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    # Persist a messages row with a real Message-ID and an action that
    # points to it via source_id.
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, "
        "subject, classification, classifier) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (42, "abc@example.com",
         datetime.now(timezone.utc).isoformat(),
         "Casey <casey@x.com>", "Project ping", "client", "rule"),
    )
    from hermes.actions.store import add_actions
    add_actions(db, source_kind="email", source_id=42,
                texts=["Reply to Casey"])
    tg = MagicMock()
    update = {"update_id": 700, "message": {"chat": {"id": 42},
              "text": "/actions"}}
    handle_update(update, _cfg(), db, tg)
    # The action card is the call carrying a reply_markup keyboard.
    card_calls = [c for c in tg.send_message.call_args_list
                  if c.kwargs.get("reply_markup")]
    assert len(card_calls) == 1
    kb = card_calls[0].kwargs["reply_markup"]
    rows = kb["inline_keyboard"]
    # Row 0 is the action triad; Row 1 is the new Gmail jump-link.
    assert len(rows) == 2
    gmail_btn = rows[1][0]
    assert "Gmail" in gmail_btn["text"]
    assert gmail_btn["url"].startswith(
        "https://mail.google.com/mail/u/0/#search/rfc822msgid:"
    )
    assert "abc@example.com" in gmail_btn["url"]


def test_actions_digestion_action_omits_gmail_link(tmp_path):
    """A URL-source action has no Gmail thread to link to, so the
    Gmail row must NOT appear on its card. The triad row stays."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.actions.store import add_actions
    add_actions(db, source_kind="digestion", source_id=99,
                texts=["Try webclaw on the docs"])
    tg = MagicMock()
    update = {"update_id": 701, "message": {"chat": {"id": 42},
              "text": "/actions"}}
    handle_update(update, _cfg(), db, tg)
    card_calls = [c for c in tg.send_message.call_args_list
                  if c.kwargs.get("reply_markup")]
    assert len(card_calls) == 1
    kb = card_calls[0].kwargs["reply_markup"]
    rows = kb["inline_keyboard"]
    # No Gmail row.
    assert len(rows) == 1
    flat = [b for r in rows for b in r]
    assert not any("Gmail" in b.get("text", "") for b in flat)


def test_actions_email_action_omits_gmail_link_for_bootstrap_sentinel(tmp_path):
    """Email actions whose source row has the synthetic 'hermes-...'
    Message-ID (bootstrap anchor) must NOT surface a Gmail row — that
    URL would be broken."""
    from datetime import datetime, timezone
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, "
        "subject, classification, classifier) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (60, "hermes-bootstrap-9999",
         datetime.now(timezone.utc).isoformat(),
         "<bootstrap>", "<initial anchor>", "other", "user"),
    )
    from hermes.actions.store import add_actions
    add_actions(db, source_kind="email", source_id=60,
                texts=["whatever"])
    tg = MagicMock()
    update = {"update_id": 702, "message": {"chat": {"id": 42},
              "text": "/actions"}}
    handle_update(update, _cfg(), db, tg)
    card_calls = [c for c in tg.send_message.call_args_list
                  if c.kwargs.get("reply_markup")]
    assert len(card_calls) == 1
    kb = card_calls[0].kwargs["reply_markup"]
    rows = kb["inline_keyboard"]
    assert len(rows) == 1  # no Gmail row


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


def test_help_text_lists_draft_command():
    """HELP_TEXT must surface /draft so power users discover it."""
    from hermes.telegram.commands import HELP_TEXT
    assert "/draft" in HELP_TEXT


def test_help_text_lists_feeds_command():
    """HELP_TEXT must surface /feeds so users can discover it."""
    from hermes.telegram.commands import HELP_TEXT
    assert "/feeds" in HELP_TEXT


def test_feeds_list_empty(tmp_path):
    """/feeds with no feeds configured → friendly bootstrap message."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 400, "message": {"chat": {"id": 42},
              "text": "/feeds"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "No feeds configured" in text
    assert "/feeds add" in text


def test_feeds_list_renders_each_feed(tmp_path):
    """/feeds (no args) lists every saved feed with its name and URL."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.rss.poller import upsert_feed
    upsert_feed(db, "https://hn.example/rss", "HN")
    upsert_feed(db, "https://lobsters.example/rss", "Lobsters")
    tg = MagicMock()
    update = {"update_id": 401, "message": {"chat": {"id": 42},
              "text": "/feeds"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "HN" in text
    assert "Lobsters" in text
    assert "https://hn.example/rss" in text
    assert "https://lobsters.example/rss" in text
    assert "(2)" in text  # the *Feeds (2)* header


def test_feeds_add_inserts_row(tmp_path):
    """/feeds add <url> [name...] persists a feeds row."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 402, "message": {"chat": {"id": 42},
              "text": "/feeds add https://hn.example/rss HN"}}
    handle_update(update, _cfg(), db, tg)
    row = db.execute(
        "SELECT url, name FROM feeds WHERE url=?",
        ("https://hn.example/rss",),
    ).fetchone()
    assert row == ("https://hn.example/rss", "HN")
    text = tg.send_message.call_args.args[0]
    assert "Added feed" in text
    assert "HN" in text


def test_feeds_add_supports_multi_word_name(tmp_path):
    """Trailing words after the URL get joined into the feed name."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 403, "message": {"chat": {"id": 42},
              "text": "/feeds add https://hn.example/rss Hacker News"}}
    handle_update(update, _cfg(), db, tg)
    row = db.execute(
        "SELECT name FROM feeds WHERE url=?",
        ("https://hn.example/rss",),
    ).fetchone()
    assert row[0] == "Hacker News"


def test_feeds_add_rejects_non_url(tmp_path):
    """`/feeds add not-a-url` should not write a row and should warn."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 404, "message": {"chat": {"id": 42},
              "text": "/feeds add notaurl"}}
    handle_update(update, _cfg(), db, tg)
    n = db.execute("SELECT COUNT(*) FROM feeds").fetchone()[0]
    assert n == 0
    text = tg.send_message.call_args.args[0]
    assert "doesn't look like a URL" in text


def test_feeds_add_with_no_url_shows_usage(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 405, "message": {"chat": {"id": 42},
              "text": "/feeds add"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "Usage:" in text


def test_feeds_remove_existing(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    from hermes.rss.poller import upsert_feed
    upsert_feed(db, "https://hn.example/rss", "HN")
    tg = MagicMock()
    update = {"update_id": 406, "message": {"chat": {"id": 42},
              "text": "/feeds remove https://hn.example/rss"}}
    handle_update(update, _cfg(), db, tg)
    n = db.execute("SELECT COUNT(*) FROM feeds").fetchone()[0]
    assert n == 0
    text = tg.send_message.call_args.args[0]
    assert "Removed feed" in text


def test_feeds_remove_missing(tmp_path):
    """Removing a feed that doesn't exist tells the user politely."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 407, "message": {"chat": {"id": 42},
              "text": "/feeds remove https://nope.example/rss"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "No feed at" in text


def test_feeds_remove_with_no_url_shows_usage(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 408, "message": {"chat": {"id": 42},
              "text": "/feeds remove"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "Usage:" in text


def test_feeds_unknown_subcommand_shows_usage(tmp_path):
    """`/feeds wat` falls through to the catch-all usage line."""
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 409, "message": {"chat": {"id": 42},
              "text": "/feeds bogus"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "Usage:" in text


def test_approved_invoice_sets_pipeline_invoiced(tmp_path):
    """Phase 2: a sent invoice advances (or creates) the contact's pipeline
    row at stage 'invoiced'."""
    from hermes.pipeline.store import board
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO outbox (kind, to_addr, subject, body, status, created_at) "
        "VALUES ('invoice', 'todd@tbn.net', 'Invoice TBN-001', 'b', 'queued', '2026-06-12')"
    )
    tg = MagicMock()
    import hermes.telegram.commands as cmd
    cmd._do_send = MagicMock(return_value=True)
    update = {"update_id": 9, "message": {"chat": {"id": 42}, "text": "/approve 1"}}
    handle_update(update, _cfg(), db, tg)
    rows = board(db)
    assert rows and rows[0]["contact_email"] == "todd@tbn.net"
    assert rows[0]["stage"] == "invoiced"


def test_approved_reply_does_not_touch_pipeline_stage(tmp_path):
    from hermes.pipeline.store import upsert_lead, board
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    upsert_lead(db, "a@b.com", display_name="A", source="direct")
    db.execute(
        "INSERT INTO outbox (kind, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 'a@b.com', 's', 'b', 'queued', '2026-06-12')"
    )
    tg = MagicMock()
    import hermes.telegram.commands as cmd
    cmd._do_send = MagicMock(return_value=True)
    update = {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/approve 1"}}
    handle_update(update, _cfg(), db, tg)
    assert board(db)[0]["stage"] == "lead"


def test_cmd_pipeline_empty(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/pipeline"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "empty" in text.lower()


def test_cmd_pipeline_board(tmp_path):
    from hermes.pipeline.store import upsert_lead, set_stage
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    upsert_lead(db, "todd@tbn.net", display_name="Todd Rodeghiero", source="partner", project="tbn")
    set_stage(db, "todd@tbn.net", "active")
    upsert_lead(db, "p@dentist.com", display_name="New Prospect", source="partner")
    tg = MagicMock()
    update = {"update_id": 12, "message": {"chat": {"id": 42}, "text": "/pipeline"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "Todd Rodeghiero" in text and "New Prospect" in text
    assert "ACTIVE" in text.upper() and "LEAD" in text.upper()


def test_cmd_stage_moves_contact(tmp_path):
    from hermes.pipeline.store import upsert_lead, board
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    upsert_lead(db, "todd@tbn.net", display_name="Todd", source="partner")
    tg = MagicMock()
    update = {"update_id": 13, "message": {"chat": {"id": 42}, "text": "/stage todd quoted"}}
    handle_update(update, _cfg(), db, tg)
    assert board(db)[0]["stage"] == "quoted"
    assert "quoted" in tg.send_message.call_args.args[0]


def test_cmd_stage_reports_errors(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 14, "message": {"chat": {"id": 42}, "text": "/stage nobody quoted"}}
    handle_update(update, _cfg(), db, tg)
    assert "no pipeline contact" in tg.send_message.call_args.args[0]
    update = {"update_id": 15, "message": {"chat": {"id": 42}, "text": "/stage"}}
    handle_update(update, _cfg(), db, tg)
    assert "Usage" in tg.send_message.call_args.args[0]


def test_cmd_followup_renders_options_for_contact(tmp_path):
    from hermes.pipeline.store import upsert_lead
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    upsert_lead(db, "pat@dentist.com", display_name="Pat Smith", source="form")
    tg = MagicMock()
    update = {"update_id": 20, "message": {"chat": {"id": 42}, "text": "/followup pat"}}
    handle_update(update, _cfg(), db, tg)
    text = tg.send_message.call_args.args[0]
    assert "Pat" in text
    assert "voicemail" in text.lower()
    assert "/followup pat@dentist.com send" in text


def test_cmd_followup_send_queues_outbox(tmp_path):
    from hermes.pipeline.store import upsert_lead
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    upsert_lead(db, "pat@dentist.com", display_name="Pat Smith", source="form")
    tg = MagicMock()
    update = {"update_id": 21, "message": {"chat": {"id": 42},
              "text": "/followup pat@dentist.com send 2"}}
    handle_update(update, _cfg(), db, tg)
    row = db.execute(
        "SELECT kind, to_addr, status, subject FROM outbox ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "reply" and row[1] == "pat@dentist.com" and row[2] == "queued"
    assert "Pat" not in row[3] or row[3]  # subject exists
    confirm = tg.send_message.call_args.args[0]
    assert "/approve" in confirm


def test_cmd_followup_unknown_contact(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 22, "message": {"chat": {"id": 42}, "text": "/followup nobody"}}
    handle_update(update, _cfg(), db, tg)
    assert "no pipeline contact" in tg.send_message.call_args.args[0]
