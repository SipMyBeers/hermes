"""Morning email digest — the summary lives where the mail does.

Sends an HTML digest to the user's own inbox at 08:00 (launchd). The daemon
recognizes the X-Hermes-Digest header, labels + stars it, and never
classifies or archives it.
"""
from __future__ import annotations
import argparse
import sqlite3
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Template
from loguru import logger

from hermes.config import GmailConfig, load_config
from hermes.db import connect, migrate
from hermes.mail.smtp_client import SmtpSender, build_message
from hermes.pipeline.store import board
from hermes.stale.check import find_stale_pipeline

_HTML = Template("""\
<html><body style="font-family: -apple-system, Helvetica, sans-serif; color: #1a1a1a; max-width: 640px;">
<h2 style="margin-bottom: 2px;">Hermes — morning brief</h2>
<p style="color:#666; margin-top:0;">{{ counts_line }}</p>

{% if leads %}
<h3>🆕 New leads (24h)</h3>
<ul>
{% for l in leads %}<li><b>{{ l.sender }}</b> — {{ l.reason }}</li>
{% endfor %}</ul>
{% endif %}

{% if stale %}
<h3>💤 Waiting on reply</h3>
<ul>
{% for s in stale %}<li><b>{{ s.display_name or s.contact_email }}</b> — {{ s.stage }}, {{ s.days_waiting }}d quiet</li>
{% endfor %}</ul>
{% endif %}

{% if actions %}
<h3>☑️ Open actions</h3>
<ul>
{% for a in actions %}<li>{{ a }}</li>
{% endfor %}</ul>
{% endif %}

{% if pipeline %}
<h3>📊 Pipeline</h3>
<table cellpadding="4" style="border-collapse: collapse;">
<tr style="text-align:left; border-bottom:1px solid #ccc;"><th>Stage</th><th>Contact</th><th>Project</th></tr>
{% for r in pipeline %}
<tr><td>{{ r.stage }}</td><td>{{ r.display_name or r.contact_email }}</td><td>{{ r.project or "" }}</td></tr>
{% endfor %}
</table>
{% endif %}
<p style="color:#999; font-size: 12px;">hermes · ghengis</p>
</body></html>
""")


def _counts_line(db: sqlite3.Connection) -> str:
    rows = db.execute(
        "SELECT classification, COUNT(*) FROM messages "
        "WHERE received_at > datetime('now', '-1 day') AND sender != '<bootstrap>' "
        "GROUP BY classification ORDER BY 2 DESC"
    ).fetchall()
    if not rows:
        return "No mail processed in the last 24h."
    return "Last 24h: " + ", ".join(f"{n} {c}" for c, n in rows)


def build_digest_email(db: sqlite3.Connection, gmail: GmailConfig) -> EmailMessage:
    leads = [
        {"sender": r[0], "reason": r[1]}
        for r in db.execute(
            "SELECT sender, reason FROM leads "
            "WHERE detected_at > datetime('now', '-1 day') ORDER BY detected_at DESC"
        ).fetchall()
    ]
    actions = [
        r[0] for r in db.execute(
            "SELECT text FROM actions WHERE status='open' "
            "ORDER BY CASE importance WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
            "created_at DESC LIMIT 10"
        ).fetchall()
    ]
    pipeline = board(db)
    stale = find_stale_pipeline(db, days=4)
    counts_line = _counts_line(db)

    html = _HTML.render(
        counts_line=counts_line, leads=leads, stale=stale,
        actions=actions, pipeline=pipeline,
    )
    text_lines = [counts_line, ""]
    if leads:
        text_lines.append("New leads: " + "; ".join(l["sender"] for l in leads))
    if stale:
        text_lines.append("Waiting on reply: " + "; ".join(
            f"{s.display_name or s.contact_email} ({s.days_waiting}d)" for s in stale))
    if actions:
        text_lines.append("Open actions: " + "; ".join(actions))
    if pipeline:
        text_lines.append("Pipeline: " + "; ".join(
            f"{r['display_name'] or r['contact_email']}={r['stage']}" for r in pipeline))

    msg = build_message(
        gmail,
        to_addr=gmail.imap_user,
        subject="Hermes — morning brief",
        body_text="\n".join(text_lines),
        attachments=[],
        body_html=html,
    )
    msg["X-Hermes-Digest"] = "1"
    return msg


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-digest-email")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--print", action="store_true", help="print text body, don't send")
    args = p.parse_args()
    cfg = load_config()
    db = connect(Path(args.db))
    migrate(db)
    msg = build_digest_email(db, cfg.gmail)
    if args.print:
        print(msg.get_body(("plain",)).get_content())
        return 0
    SmtpSender(cfg.gmail).send(msg)
    logger.info("morning brief emailed to {}", cfg.gmail.imap_user)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
