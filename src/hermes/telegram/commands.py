from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from loguru import logger
from hermes.actions.store import (
    list_open, count_open, mark_done, dismiss, snooze,
    expire_snoozed, parse_when,
)
from hermes.bookmarks.store import save_bookmark
from hermes.config import Config
from hermes.ingest.url import ingest, find_url, URL_RE  # noqa: F401  (URL_RE re-exported)
from hermes.mail.smtp_client import SmtpSender, build_message
from hermes.telegram.client import TelegramClient
from hermes.telegram.keyboards import actions_card, snooze_choices, approval_card

HELP_TEXT = """*Hermes — your inbox + URL bot*

Tap the buttons on cards I send. You don't have to type commands.

If you'd rather type:
• Send any URL → I'll fetch and summarize it. PDFs work too.
• `/read <url> [--depth N]` — explicit fetch; --depth crawls a docs site.
• `/digest` — today's report.
• `/actions` — open to-dos.
• `/save <url>` — for the weekly bookmark digest.
• `/feeds` — list/add/remove RSS feeds; new items show up periodically.
• `/draft <uid>` — draft a reply for a saved email.

Slash-command index for power users:
`/status` `/approve` `/reject` `/done` `/snooze` `/dismiss` `/retry`
"""


def _do_send(
    cfg: Config,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list[Path] | None = None,
) -> bool:
    """Hook so tests can patch SMTP without monkeypatching smtplib."""
    paths = list(attachments or [])
    msg = build_message(cfg.gmail, to_addr, subject, body, paths)
    try:
        SmtpSender(cfg.gmail).send(msg)
        return True
    except Exception as e:
        logger.error("send failed: {}", e)
        return False


def _parse_attachments(raw: str | None) -> list[Path]:
    """Parse outbox.attachments column into a list[Path].

    Handles None, empty string, single-path string, and JSON-encoded list.
    Falls back to single-path interpretation if JSON parsing fails.
    """
    if not raw:
        return []
    raw = raw.strip()
    # Try JSON first (future-proof for multi-attachment)
    if raw.startswith("["):
        try:
            paths = json.loads(raw)
            return [Path(p) for p in paths if p]
        except (json.JSONDecodeError, TypeError):
            pass
    # Fall back: single path string (Task 17's current shape)
    return [Path(raw)]


def handle_update(
    update: dict,
    cfg: Config,
    db: sqlite3.Connection,
    tg: TelegramClient,
) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    if msg.get("chat", {}).get("id") != cfg.telegram.chat_id:
        logger.warning("ignoring update from chat_id={}", msg.get("chat", {}).get("id"))
        return
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        # Bare URL? Treat as /read.
        url = find_url(text)
        if url:
            _ingest_and_reply(url, cfg, db, tg)
            return
        tg.send_message(HELP_TEXT)
        return
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    handlers = {
        "/status": _cmd_status,
        "/digest": _cmd_digest,
        "/approve": _cmd_approve,
        "/reject": _cmd_reject,
        "/save": _cmd_save,
        "/read": _cmd_read,
        "/actions": _cmd_actions,
        "/done": _cmd_done,
        "/dismiss": _cmd_dismiss,
        "/snooze": _cmd_snooze,
        "/draft": _cmd_draft,
        "/feeds": _cmd_feeds,
        "/start": _cmd_start,
        "/help": lambda *a: tg.send_message(HELP_TEXT),
    }
    fn = handlers.get(cmd)
    if not fn:
        tg.send_message(HELP_TEXT)
        return
    fn(args, cfg, db, tg)


def _cmd_start(args, cfg, db, tg):
    """Friendly dashboard — shows open-action count and one-tap shortcuts."""
    from hermes.telegram.keyboards import dashboard
    n_open = count_open(db)
    text = "*Hermes ready*\n"
    if n_open:
        text += f"_{n_open} open action{'s' if n_open != 1 else ''}_"
    else:
        text += "_no open actions_"
    tg.send_message(text, reply_markup=dashboard())


def _cmd_status(args, cfg, db, tg):
    last_24h = db.execute(
        "SELECT classification, COUNT(*) FROM messages "
        "WHERE received_at > datetime('now','-1 day') "
        "GROUP BY classification"
    ).fetchall()
    pending = db.execute("SELECT COUNT(*) FROM outbox WHERE status='queued'").fetchone()[0]
    failed = db.execute("SELECT COUNT(*) FROM outbox WHERE status='failed'").fetchone()[0]
    counts = ", ".join(f"{c}: {n}" for c, n in last_24h) or "no messages yet"
    tg.send_message(
        f"*Hermes status*\nLast 24h messages → {counts}\n"
        f"Pending approval: {pending}\nFailed sends: {failed}"
    )


def _cmd_digest(args, cfg, db, tg):
    from hermes.digest.build import build_morning_digest  # local import to avoid cycle
    text = build_morning_digest(db, cfg)
    tg.send_message(text)


def _cmd_approve(args, cfg, db, tg, *, silent: bool = False):
    """Send a queued outbox row over SMTP and update its status.

    silent=True suppresses the chat-level confirmation message — the
    callback router uses this because the original card already gets
    decorated to '✉️ ... _sent_' via _decorate(), and posting a
    second confirmation duplicates the signal."""
    if not args:
        tg.send_message("Usage: /approve <outbox_id>")
        return
    try:
        oid = int(args[0])
    except ValueError:
        tg.send_message("Outbox id must be an integer")
        return
    row = db.execute(
        "SELECT to_addr, subject, body, status, attachments FROM outbox WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        tg.send_message(f"No outbox entry with id={oid}")
        return
    to_addr, subject, body, status, attachments_raw = row
    if status != "queued":
        tg.send_message(f"Outbox {oid} status={status}, cannot send")
        return
    ok = _do_send(cfg, to_addr, subject, body, _parse_attachments(attachments_raw))
    if ok:
        db.execute(
            "UPDATE outbox SET status='sent', sent_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), oid),
        )
        if not silent:
            tg.send_message(f"Sent #{oid} to {to_addr}")
    else:
        db.execute("UPDATE outbox SET status='failed', last_error='send error' WHERE id=?", (oid,))
        if not silent:
            tg.send_message(f"Failed to send #{oid} — see logs")


def _cmd_reject(args, cfg, db, tg, *, silent: bool = False):
    """Discard a queued outbox row.

    silent=True (callback path) skips the text confirmation; the card
    decoration to '🗑️ ... _discarded_' carries the signal already."""
    if not args:
        tg.send_message("Usage: /reject <outbox_id>")
        return
    try:
        oid = int(args[0])
    except ValueError:
        tg.send_message("Outbox id must be an integer")
        return
    res = db.execute("DELETE FROM outbox WHERE id=? AND status='queued'", (oid,))
    if not silent:
        tg.send_message(
            f"Rejected #{oid}" if res.rowcount else f"Nothing to reject for id={oid}"
        )


def _cmd_save(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /save <url> [notes...]")
        return
    url = args[0]
    if not url.startswith(("http://", "https://")):
        tg.send_message(f"That doesn't look like a URL: {url}")
        return
    notes = " ".join(args[1:]) if len(args) > 1 else None
    bid = save_bookmark(db, url=url, notes=notes)
    msg = f"Saved bookmark #{bid} — {url}"
    if notes:
        msg += f"\n{notes}"
    tg.send_message(msg)


def _cmd_read(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /read <url> [--depth N]")
        return
    url = args[0]
    if not url.startswith(("http://", "https://")):
        tg.send_message(f"That doesn't look like a URL: {url}")
        return
    depth = 0
    if "--depth" in args:
        try:
            i = args.index("--depth")
            depth = int(args[i + 1])
            # Cap at 5 — beyond this webclaw crawls a long time and the
            # combined markdown blows past gemma4's context window.
            depth = max(0, min(5, depth))
        except (IndexError, ValueError):
            tg.send_message("Usage: /read <url> [--depth N]")
            return
    _ingest_and_reply(url, cfg, db, tg, depth=depth)


def _cmd_actions(args, cfg, db, tg):
    # Reopen anything whose snooze expired since the last call so the
    # listing reflects current reality without waiting for the cron.
    expire_snoozed(db)
    rows = list_open(db, include_low=True, limit=15)
    if not rows:
        tg.send_message("No open actions. ✓")
        return
    tg.send_message(f"*Open actions ({len(rows)})*")
    last_kind = None
    for a in rows:
        if a.source_kind != last_kind:
            label = {"email": "✉️ *Email*", "digestion": "🔖 *URL*",
                     "manual": "📝 *Manual*",
                     "feed_item": "📰 *Feed*"}.get(a.source_kind, a.source_kind)
            tg.send_message(label)
            last_kind = a.source_kind
        badge = "🔴 " if a.importance == "urgent" else ""
        # Phase N.1: surface the consolidated thread depth so a multi-reply
        # conversation reads as 'Reply to Joseph (3 messages)' instead of
        # three duplicate cards. Suppress the suffix at count==1 so we
        # don't print '(1 messages)' on every fresh action.
        count_suffix = (
            f" _({a.thread_count} messages)_" if a.thread_count > 1 else ""
        )
        ctx = f"\n_{a.source_subject[:80]}_" if a.source_subject else ""
        # Phase N.2: when this action originated from an inbound email,
        # look up its persisted Message-ID and build a Gmail jump-link.
        # We only do this for source_kind=='email' — URL/feed/manual
        # actions don't have a corresponding Gmail thread to deep-link to.
        gmail_url = ""
        if a.source_kind == "email" and a.source_id is not None:
            row = db.execute(
                "SELECT message_id FROM messages WHERE uid=?", (a.source_id,)
            ).fetchone()
            if row and row[0]:
                from hermes.mail.gmail_link import gmail_url_for
                gmail_url = gmail_url_for(row[0])
        tg.send_message(
            f"{badge}{a.text}{count_suffix}{ctx}",
            reply_markup=actions_card(a.id, gmail_url=gmail_url),
        )


def _cmd_done(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /done <action_id>")
        return
    try:
        aid = int(args[0])
    except ValueError:
        tg.send_message("Action id must be an integer")
        return
    if mark_done(db, aid):
        tg.send_message(f"Done #{aid} ✓")
    else:
        tg.send_message(
            f"Nothing to close for action {aid} (already done or unknown)"
        )


def _cmd_dismiss(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /dismiss <action_id>")
        return
    try:
        aid = int(args[0])
    except ValueError:
        tg.send_message("Action id must be an integer")
        return
    if dismiss(db, aid):
        tg.send_message(f"Dismissed #{aid}")
    else:
        tg.send_message(f"Nothing to dismiss for action {aid}")


def _cmd_snooze(args, cfg, db, tg):
    if len(args) < 2:
        tg.send_message(
            "Usage: /snooze <action_id> <when>\n"
            "When: 'tomorrow', '+3d', 'monday', '2h', 'next week'"
        )
        return
    try:
        aid = int(args[0])
    except ValueError:
        tg.send_message("Action id must be an integer")
        return
    when_str = " ".join(args[1:])
    until = parse_when(when_str)
    if until is None:
        tg.send_message(
            f"Couldn't parse '{when_str}'. Try '+3d', 'tomorrow', 'monday'."
        )
        return
    if snooze(db, aid, until):
        tg.send_message(
            f"Snoozed #{aid} until {until.strftime('%a %b %d %H:%M UTC')}"
        )
    else:
        tg.send_message(f"Nothing to snooze for action {aid}")


def _cmd_draft(args, cfg, db, tg):
    """Turn a stored email into a Send/Discard outbox card.

    Lookup order:
      1. messages.draft_text — populated by the daemon for client mail.
      2. If empty, run drafter inline against persisted raw_email so the
         user can /draft any reviewable message even if the daemon
         skipped drafting (review/personal/other classes).

    Resulting outbox row is rendered with approval_card(oid) so the user
    sees the full body and the Send/Discard buttons. The DB write happens
    BEFORE the Telegram send, so a Telegram outage doesn't lose the draft.
    """
    if not args:
        tg.send_message("Usage: /draft <message_uid>")
        return
    try:
        uid = int(args[0])
    except ValueError:
        tg.send_message("Message uid must be an integer")
        return
    row = db.execute(
        "SELECT message_id, sender, subject, draft_text, raw_email "
        "FROM messages WHERE uid=?",
        (uid,),
    ).fetchone()
    if not row:
        tg.send_message(f"No message found for uid={uid}")
        return
    message_id, sender, subject, draft_text, raw_email = row

    # If no cached draft, generate one now via the existing drafter.
    if not (draft_text or "").strip():
        if not raw_email:
            tg.send_message(
                f"uid={uid} has no raw email persisted; can't draft."
            )
            return
        from email import message_from_bytes
        from hermes.classifier.drafter import draft_reply as _drafter
        msg = message_from_bytes(raw_email)
        # First name only — the prompt template signs off with this and
        # 'Dylan Beers' as a closer reads stilted. Fall back to 'Hermes'
        # if the user never set a name in secrets.toml.
        sender_name = (
            cfg.user.name.split()[0] if cfg.user.name else "Hermes"
        )
        draft_text = _drafter(msg, sender_name=sender_name)
        if not draft_text.strip():
            tg.send_message(
                f"⚠ couldn't draft a reply for uid={uid} "
                f"(Ollama returned empty)"
            )
            return
        # Persist for next time so a re-run of /draft skips the LLM call.
        db.execute(
            "UPDATE messages SET draft_text=?, drafted_at=? WHERE uid=?",
            (draft_text, datetime.now(timezone.utc).isoformat(), uid),
        )

    # Extract recipient address from the original sender header.
    from email.utils import getaddresses
    to_addr = (
        getaddresses([sender or ""])[0][1] if sender else ""
    ).strip()
    if not to_addr:
        tg.send_message(f"⚠ uid={uid} has no parseable sender address")
        return

    subj = (subject or "(no subject)").strip()
    # Don't double-stack 'Re: Re: Re:' prefixes when replying to a
    # message that was itself a reply.
    if not subj.lower().startswith(("re:", "re :")):
        subj = f"Re: {subj}"

    cur = db.execute(
        "INSERT INTO outbox(kind, related_uid, to_addr, subject, body, "
        "status, created_at) "
        "VALUES ('reply', ?, ?, ?, ?, 'queued', ?)",
        (uid, to_addr, subj, draft_text,
         datetime.now(timezone.utc).isoformat()),
    )
    oid = cur.lastrowid
    # Body capped at 1500 chars in the preview so a runaway draft doesn't
    # exceed Telegram's 4096-char message limit; the full body is in DB
    # for the actual send.
    tg.send_message(
        f"✏️ *Draft reply* (#{oid})\n"
        f"To: {to_addr}\n"
        f"Subject: {subj}\n\n"
        f"{draft_text[:1500]}",
        reply_markup=approval_card(oid),
    )


def _cmd_feeds(args, cfg, db, tg):
    """List, add, or remove RSS feeds polled by hermes-rounds.

    `/feeds`                  → list every configured feed with last-polled
    `/feeds add <url> [name]` → add or rename a feed
    `/feeds remove <url>`     → delete a feed (feed_items rows kept; the
                                FK is just informational)
    """
    if not args:
        rows = db.execute(
            "SELECT url, name, last_polled_at FROM feeds ORDER BY url"
        ).fetchall()
        if not rows:
            tg.send_message(
                "No feeds configured.\n"
                "Add one: `/feeds add https://news.ycombinator.com/rss [HN]`"
            )
            return
        lines = [f"*Feeds ({len(rows)})*"]
        for url, name, last in rows:
            label = name or url
            polled = (
                f"  _last polled: {last}_" if last else "  _never polled_"
            )
            lines.append(f"• *{label}*\n  {url}\n{polled}")
        lines.append("\nAdd: `/feeds add <url> [name]`")
        lines.append("Remove: `/feeds remove <url>`")
        tg.send_message("\n".join(lines))
        return

    sub = args[0].lower()
    if sub == "add":
        if len(args) < 2:
            tg.send_message("Usage: /feeds add <url> [name]")
            return
        url = args[1]
        if not url.startswith(("http://", "https://")):
            tg.send_message(f"That doesn't look like a URL: {url}")
            return
        name = " ".join(args[2:]).strip()
        from hermes.rss.poller import upsert_feed
        upsert_feed(db, url, name)
        tg.send_message(f"Added feed: {name or url}")
    elif sub == "remove":
        if len(args) < 2:
            tg.send_message("Usage: /feeds remove <url>")
            return
        url = args[1]
        from hermes.rss.poller import remove_feed
        if remove_feed(db, url):
            tg.send_message(f"Removed feed: {url}")
        else:
            tg.send_message(f"No feed at {url}")
    else:
        tg.send_message("Usage: /feeds [add|remove] [url] [name]")


def handle_callback(
    update: dict,
    cfg: Config,
    db: sqlite3.Connection,
    tg: TelegramClient,
) -> None:
    """Route Telegram inline-button taps. Verbs:
    done | dismiss | snooze | unsnooze | approve | reject | actions | digest.
    """
    cb = update.get("callback_query") or {}
    if not cb:
        return
    msg = cb.get("message") or {}
    if msg.get("chat", {}).get("id") != cfg.telegram.chat_id:
        logger.warning(
            "ignoring callback from chat_id={}",
            msg.get("chat", {}).get("id"),
        )
        try:
            tg.answer_callback_query(cb.get("id", ""), text="not allowed")
        except Exception:
            pass
        return
    callback_id = cb.get("id", "")
    data = (cb.get("data") or "").strip()
    msg_id = msg.get("message_id")
    parts = data.split(":", 2)
    verb = parts[0] if parts else ""

    try:
        if verb == "done" and len(parts) >= 2:
            aid = int(parts[1])
            ok = mark_done(db, aid)
            tg.answer_callback_query(callback_id, "✓ done" if ok else "already closed")
            if msg_id and ok:
                _decorate(tg, msg, prefix="✓ ", suffix="\n\n_done_",
                          remove_keyboard=True)
        elif verb == "dismiss" and len(parts) >= 2:
            aid = int(parts[1])
            # Snapshot the action's source BEFORE we close the row so we
            # can teach the rule classifier when the source is an email.
            src = db.execute(
                "SELECT source_kind, source_id, source_sender FROM actions "
                "WHERE id=?",
                (aid,),
            ).fetchone()
            ok = dismiss(db, aid)
            tg.answer_callback_query(callback_id, "dismissed" if ok else "already closed")
            # Phase L stage 2: feed the dismiss into learned_promos so the
            # next mail from this sender auto-classifies as promo. Wrapped
            # broadly — a learn-loop failure must never block the dismiss
            # decoration the user is waiting on.
            if ok and src and src[0] == "email" and src[2]:
                try:
                    from hermes.classifier.learned import record_dismiss
                    count = record_dismiss(db, src[2])
                    logger.info(
                        "learned promo: {} now at {} dismisses", src[2], count
                    )
                except Exception as e:
                    logger.warning("learned-promo record failed: {}", e)
            if msg_id and ok:
                _decorate(tg, msg, prefix="🚫 ", suffix="\n\n_dismissed_",
                          remove_keyboard=True)
        elif verb == "savefeed" and len(parts) >= 2:
            # Phase M.2: 🔖 Save on a feed_card stashes the article URL as
            # a bookmark (existing weekly-bookmarks digest picks it up).
            # The corresponding action stays open — the user can still
            # tap Dismiss to clear it from /actions, or it'll decay to
            # 'stale' on the actions-decay cron.
            fid = int(parts[1])
            row = db.execute(
                "SELECT title, url FROM feed_items WHERE id=?", (fid,)
            ).fetchone()
            if not row:
                tg.answer_callback_query(callback_id, "feed item not found")
                return
            title, url = row
            from hermes.bookmarks.store import save_bookmark
            save_bookmark(db, url=url, title=title, notes="from feeds")
            tg.answer_callback_query(callback_id, "saved")
            if msg_id:
                _decorate(tg, msg, prefix="🔖 ", suffix="\n\n_saved_",
                          remove_keyboard=True)
        elif verb == "snooze" and len(parts) == 2:
            # Root tap — open the duration sub-keyboard. No DB write yet.
            aid = int(parts[1])
            tg.answer_callback_query(callback_id, "")
            if msg_id:
                tg.edit_message_text(
                    msg_id,
                    text=(msg.get("text") or "") + "\n\n_pick a snooze:_",
                    reply_markup=snooze_choices(aid),
                )
        elif verb == "snooze" and len(parts) == 3:
            aid = int(parts[1])
            spec = parts[2]
            until = parse_when(spec)
            ok = until is not None and snooze(db, aid, until)
            tg.answer_callback_query(
                callback_id, f"snoozed {spec}" if ok else "couldn't snooze"
            )
            if msg_id and ok:
                _decorate(
                    tg, msg, prefix="🌙 ",
                    suffix=f"\n\n_snoozed until {until.strftime('%a %b %d %H:%M UTC')}_",
                    remove_keyboard=True,
                    strip_suffix="\n\n_pick a snooze:_",
                )
        elif verb == "unsnooze" and len(parts) >= 2:
            # Back from the snooze sub-keyboard — restore the original card.
            aid = int(parts[1])
            tg.answer_callback_query(callback_id, "")
            if msg_id:
                base = (msg.get("text") or "").split("\n\n_pick a snooze:_")[0]
                tg.edit_message_text(
                    msg_id, text=base, reply_markup=actions_card(aid)
                )
        elif verb == "approve" and len(parts) >= 2:
            oid = int(parts[1])
            # silent=True: the card decoration below is the only confirmation
            # the user needs — a second chat message duplicates the signal.
            _cmd_approve([str(oid)], cfg, db, tg, silent=True)
            tg.answer_callback_query(callback_id, "sending")
            if msg_id:
                _decorate(tg, msg, prefix="✉️ ", suffix="\n\n_sent_",
                          remove_keyboard=True)
        elif verb == "reject" and len(parts) >= 2:
            oid = int(parts[1])
            _cmd_reject([str(oid)], cfg, db, tg, silent=True)
            tg.answer_callback_query(callback_id, "discarded")
            if msg_id:
                _decorate(tg, msg, prefix="🗑️ ", suffix="\n\n_discarded_",
                          remove_keyboard=True)
        elif verb == "actions":
            tg.answer_callback_query(callback_id, "")
            _cmd_actions([], cfg, db, tg)
        elif verb == "digest":
            tg.answer_callback_query(callback_id, "")
            _cmd_digest([], cfg, db, tg)
        elif verb == "draft" and len(parts) >= 2:
            uid = int(parts[1])
            # Flash before the LLM call so the user sees instant feedback;
            # generation can take a few seconds when the cache is cold.
            tg.answer_callback_query(callback_id, "drafting…")
            try:
                _cmd_draft([str(uid)], cfg, db, tg)
            except Exception as e:
                tg.send_message(f"⚠ draft failed: {e}")
        else:
            tg.answer_callback_query(callback_id, "?")
    except Exception as e:
        logger.exception("callback handler failed: {}", e)
        try:
            tg.answer_callback_query(callback_id, "error")
        except Exception:
            pass


def _decorate(
    tg: TelegramClient,
    msg: dict,
    *,
    prefix: str,
    suffix: str,
    remove_keyboard: bool,
    strip_suffix: str = "",
) -> None:
    """Edit the original card to show its terminal state.

    `prefix` is added once (idempotent — won't double-stack if the card was
    already decorated). `strip_suffix` lets snooze-confirmation strip the
    intermediate '_pick a snooze:_' line that the sub-keyboard added.
    """
    text = msg.get("text") or ""
    if strip_suffix and strip_suffix in text:
        text = text.split(strip_suffix)[0]
    bare_prefix = prefix.rstrip()
    if bare_prefix and not text.lstrip().startswith(bare_prefix):
        text = prefix + text
    text += suffix
    markup = None if remove_keyboard else msg.get("reply_markup")
    try:
        tg.edit_message_text(msg.get("message_id"), text=text, reply_markup=markup)
    except Exception as e:
        logger.warning("edit_message_text failed: {}", e)


def _ingest_and_reply(url: str, cfg, db, tg, depth: int = 0) -> None:
    """Shared ingest path used by /read and the bare-URL detector.

    depth>0 forwards to the webclaw crawl path so docs sites land as one
    combined summary."""
    ack = f"📥 Reading {url}"
    if depth > 0:
        ack += f" (crawl depth={depth})"
    tg.send_message(ack + " …")
    d = ingest(
        url,
        db,
        source_chat_id=cfg.telegram.chat_id,
        cookies_path=cfg.ingest.cookies_path,
        cookies_from_browser=cfg.ingest.cookies_from_browser,
        depth=depth,
    )
    if d.error:
        tg.send_message(f"⚠ Couldn't ingest #{d.id}\n{url}\n{d.error}")
        return
    lines = [f"🔖 *#{d.id}* {d.title or url}"]
    if d.summary:
        lines.append(d.summary)
    if d.actions:
        lines.append("")
        lines.append("*Actions:*")
        for a in d.actions:
            lines.append(f"  – {a}")
    if d.tools:
        lines.append("")
        lines.append("*Tools mentioned:*")
        for t in d.tools:
            name = t.get("name", "")
            desc = t.get("description", "")
            if name and desc:
                lines.append(f"  – *{name}* — {desc}")
            elif name:
                lines.append(f"  – *{name}*")
    tg.send_message("\n".join(lines))
