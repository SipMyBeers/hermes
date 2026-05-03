# Hermes — Email Triage & Invoicing Agent

**Date:** 2026-05-02
**Owner:** Dylan Beers
**Host:** ghengis (Mac mini, Tailscale `100.116.82.14`, user `macmini`)
**Status:** Design approved, pending spec review.

## Goal

A lean, self-hosted agent that watches `dzbeers747@gmail.com`, separates promos from clients, drafts replies for review, and generates and sends monthly invoices from `dylan@ranger-beers.com`. All compute stays on ghengis, no paid APIs, no external orchestrators.

## Non-goals

- No n8n, no Mission Control wiring, no cloud workflow tools.
- No Resend / SendGrid / SES / Mailgun. SMTP only.
- No bring-your-own-key UI. Single user, single inbox.
- No Workspace/multi-tenant support. Personal Gmail only.
- No auto-reply. All outbound mail requires Telegram `/approve`.

## Constraints

- Free LLMs only — local Ollama on ghengis (`gemma4`, `qwen2.5-coder:7b`, `qwen3:4b`, `nomic-embed-text` already present). LM Studio also available with `GLM-4.7-Flash`.
- Free outbound — Gmail SMTP with app password and "Send mail as" alias for `dylan@ranger-beers.com`.
- Telegram is the only user surface. No web UI.
- All cron via `launchd` on the host. No external scheduler.
- macOS 26.3, system Python 3.9.6 — venv with newer Python via `uv` to avoid system pollution.

## Architecture

```
                         ┌──────────────────────┐
   Gmail IMAP IDLE  ───▶ │  hermes-mail (daemon)│ ──▶ ~/.hermes/state.sqlite
                         │  • rule classifier   │
                         │  • LLM fallback ─────┼──▶ Ollama :11434
                         │  • Gmail label apply │
                         │  • SMTP outbound     │
                         └──────┬───────────────┘
                                │ events
                                ▼
                       ┌────────────────────┐         ┌─────────────────┐
                       │ hermes-tg (bot)    │ ◀──────▶│ Telegram (you)  │
                       │ commands: /digest  │         │                 │
                       │ /approve /invoice  │         │                 │
                       │ /client /status    │         │                 │
                       └────────┬───────────┘         └─────────────────┘
                                ▼
                       ┌────────────────────┐
                       │ hermes-invoice     │  ← launchd, 1st of month 9am
                       │ • Jinja2 → HTML    │
                       │ • WeasyPrint → PDF │
                       │ • approval card    │
                       │ • SMTP send on     │
                       │   /approve         │
                       └────────────────────┘
```

Three small units, one shared SQLite, one shared `~/.config/hermes/secrets.toml`. Each unit testable in isolation.

## Components

### 1. `hermes-mail` — IMAP daemon

**Path:** `~/Projects/hermes/mail/`
**Responsibility:** Watch Gmail, classify, label, route to Telegram, send approved outbound.

**Inbound flow:**

1. `imap.gmail.com:993` IMAP IDLE; reconnect with exponential backoff on drop.
2. New message arrives → fetch headers + body.
3. **Tier-1 rule classifier** (deterministic, microseconds):
   - Has `List-Unsubscribe` header → `Promo`. Apply Gmail label `Hermes/Promo`, archive, log.
   - Sender domain in `clients.toml` → `Client/<name>`. Real-time Telegram ping.
   - Gmail `\Important` flag + no other rule match → `ToReview`. Real-time Telegram ping.
   - Sender in `personal_allowlist` → `Personal`. No notify.
   - Subject matches transactional patterns (`receipt|invoice|payment|order #`) → `Transactional`. No notify, log to ledger.
4. **Tier-2 LLM fallback** (only on no-rule-match):
   - `qwen3:4b` returns one of `promo|client|personal|transactional|other` plus a confidence in `[0,1]`.
   - **Threshold:** `confidence >= 0.75` is treated as decisive. Below that, the message is labeled `ToReview` and surfaces in the daily digest with the model's best guess.
   - On `client` (decisive or not), escalate to `gemma4` for a draft reply, save draft, post Telegram approval card.
   - On `promo` decisive, label `Hermes/Promo` and archive.
   - On `personal` or `other` decisive, label and notify only via daily digest.
   - **Notification dedup:** at most one real-time Telegram ping per sender per 30-minute window — burst senders get coalesced into one "(N new from <sender>)" message.

**Outbound flow:**

- All sends go through one `send(message)` function.
- SMTP `smtp.gmail.com:587` STARTTLS, app password.
- `From: Dylan Beers <dzbeers747@gmail.com>`, `Reply-To: Dylan Beers <dylan@ranger-beers.com>`. See "Email delivery path" below for why we don't put `dylan@ranger-beers.com` in `From` directly in v1.
- Failures land in `outbox` table with `last_error`; daemon retries with backoff; user can `/retry <id>` from Telegram.

**Why two tiers, not LLM-everywhere:** rule-based is faster, more accurate (fewer false-positives) and free of CPU cost on the 90% of mail that's obviously a promo. LLM is reserved for the cases it adds value (drafting, ambiguous senders).

### 2. `hermes-tg` — Telegram bot

**Path:** `~/Projects/hermes/telegram/`
**Responsibility:** Sole human surface — notifications, approvals, status, manual triggers.

**Setup:** Reuses existing `BOT_TOKEN` from `~/Documents/telegram-bot.py`. As part of install, the token is moved into `~/.config/hermes/secrets.toml` (mode 600) and the original file references the new path. Existing bot keeps working.

**Commands:**

| Command | Effect |
|---------|--------|
| `/digest` | On-demand category summary; auto-posted at 08:00 ghengis-local daily (the morning email report — see "Daily morning report" below). |
| `/approve <id>` | Send queued draft or invoice. |
| `/reject <id>` | Discard queued draft. |
| `/edit <id> <text>` | Replace draft body, leave queued. |
| `/invoice <client>` | Manually trigger invoice for a client. |
| `/client add <email> <name> <amount>` | Append to allowlist + billing config. |
| `/client list` | Show all clients and billing config. |
| `/reclassify <uid> <label>` | Move a message; if label is `Client/<name>`, learn sender domain. |
| `/status` | Daemon health: last IMAP poll, queue depth, Ollama latency, last 24h counts. |
| `/retry <id>` | Re-attempt a failed outbound. |

**Why reuse the existing bot:** one token, one chat, no new bot to babysit. Existing `telegram-bot.py` already polls correctly and routes to Ollama — Hermes adds command handlers in the same loop.

### 3. `hermes-invoice` — Monthly invoice job

**Path:** `~/Projects/hermes/invoices/`
**Responsibility:** Render and send monthly invoices for each client with a `billing_amount` set.

**Trigger:** `launchd` plist fires 1st of each month at 09:00 ghengis-local. Manual via `/invoice <client>`.

**Render pipeline:**

1. Load client record from `clients.toml` and SQLite `clients` table.
2. Fill Jinja2 template `invoices/templates/standard.html` with client + line items + month + invoice number.
3. Render to PDF via **WeasyPrint** (free, pure Python, no headless browser).
4. Save to `~/.hermes/invoices/<year>/<month>/<client>-<invoice_number>.pdf`.
5. Compose covering email (gemma4-drafted, conservative tone), attach PDF.
6. Post Telegram approval card with summary + first page preview.
7. On `/approve <id>` → SMTP send → write to `ledger`.

**Boarhog onboarding (first-run, today):**

- On first execution, no client config exists.
- Hermes-tg posts: *"No client config yet. Reply with: `boarhog | <amount> | <recipient_email> | <service desc> | <due_date>`"*.
- Reply is parsed → saved to `clients.toml` + SQLite.
- Hermes-invoice immediately runs Boarhog's May invoice → approval card → send.

## Data model

**`~/.hermes/state.sqlite`** — single file, one daemon owns the writes.

```sql
-- inbound mail tracking
CREATE TABLE messages (
  uid INTEGER PRIMARY KEY,
  message_id TEXT UNIQUE,
  received_at TEXT NOT NULL,
  sender TEXT NOT NULL,
  subject TEXT,
  classification TEXT NOT NULL,    -- promo|client|personal|transactional|other|review
  classifier TEXT NOT NULL,        -- rule|qwen3|gemma4|user
  confidence REAL,
  label TEXT,
  draft_text TEXT,
  drafted_at TEXT,
  notified_at TEXT
);

-- client billing config (mirrors clients.toml, queryable from bot)
CREATE TABLE clients (
  name TEXT PRIMARY KEY,
  domain TEXT,                     -- email domain for sender allowlist
  contact_email TEXT NOT NULL,
  billing_amount_cents INTEGER,
  billing_terms TEXT,              -- e.g. "Net 30", "Due on receipt"
  service_description TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

-- monthly invoice ledger (in + out)
CREATE TABLE ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  direction TEXT NOT NULL,         -- 'out' (we billed) | 'in' (we paid)
  client TEXT,
  vendor TEXT,
  invoice_number TEXT,
  month TEXT NOT NULL,             -- YYYY-MM
  amount_cents INTEGER NOT NULL,
  status TEXT NOT NULL,            -- draft|sent|paid|overdue|void
  pdf_path TEXT,
  message_id TEXT,                 -- email Message-ID once sent
  sent_at TEXT,
  paid_at TEXT
);

-- outbound queue (drafts pending approval + retry on failure)
CREATE TABLE outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,              -- reply|invoice|adhoc
  related_uid INTEGER,             -- for reply
  client TEXT,                     -- for invoice
  to_addr TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  attachments TEXT,                -- JSON array of paths
  status TEXT NOT NULL,            -- queued|sent|failed
  last_error TEXT,
  created_at TEXT NOT NULL,
  sent_at TEXT
);
```

**`~/.config/hermes/secrets.toml`** (mode 600):

```toml
[gmail]
imap_user = "dzbeers747@gmail.com"
imap_app_password = "..."         # 16-char app password
smtp_user = "dzbeers747@gmail.com"
smtp_app_password = "..."         # same value, separate field for clarity
from_address = "Dylan Beers <dzbeers747@gmail.com>"
reply_to = "Dylan Beers <dylan@ranger-beers.com>"

[telegram]
bot_token = "8210698895:..."
chat_id = -1                       # filled in on first /start
```

**`~/Projects/hermes/clients.toml`** (committed, no secrets):

```toml
[boarhog]
domain = "..."                     # filled in by user reply on first run
contact_email = "..."
billing_amount_cents = 0
billing_terms = "Net 30"
service_description = "Hosting fees"
active = true
```

## Errors & failure modes

| Failure | Detection | Response |
|---------|-----------|----------|
| IMAP connection drops | IDLE socket EOF | Exponential backoff reconnect (1s → 60s cap), no message loss (UIDs idempotent). |
| Gmail auth fails | IMAP `NO` response | Telegram alert, daemon halts, manual fix required. |
| Ollama unreachable | HTTP timeout | Tier-2 mail queued with `pending_classification`, retried every 60s; Telegram alert after 5min downtime. |
| SMTP send fails | `smtplib` exception | `outbox.status='failed'`, `last_error` populated, Telegram alert; user `/retry`. |
| Telegram offline | `getUpdates` timeout | Daemon keeps classifying, queues notifications in `outbox` with `kind='telegram'`, drains on reconnect. |
| Wrong classification | User `/reclassify` | Updates `messages.classification`, learns rule (sender domain → `clients.toml` if `Client/<name>`). |
| WeasyPrint render fails | Exception in invoice job | Draft email still posted to Telegram with attached HTML; user can fix template and retry. |
| Disk full (SQLite) | SQLite write error | Daemon halts, Telegram alert; user clears space. |

## Testing strategy

- **Unit:** rule classifier with fixture mbox files; SMTP and Telegram clients mocked; SQLite migrations applied to in-memory DB; PDF render diff against golden file.
- **Integration:** `pytest --live-imap` flag runs against a fenced label `Hermes/Test` in real Gmail (cannot touch other labels). Tagged `@pytest.mark.live` and excluded from default runs.
- **Dry-run mode:** `hermes-mail --dry-run --once --since=24h` processes recent mail, prints all actions (label, archive, draft, notify) without applying them. Used before each rule change.
- **Smoke:** `hermes-invoice --dry-run --client=boarhog --month=2026-05` renders the PDF, prints the email, does not send.

## Install footprint

| Path | Purpose |
|------|---------|
| `~/Projects/hermes/` | Code, templates, tests. Git-tracked. |
| `~/Projects/hermes/clients.toml` | Client billing config, no secrets. |
| `~/.config/hermes/secrets.toml` | Gmail + Telegram secrets, mode 600. |
| `~/.hermes/state.sqlite` | Runtime state. |
| `~/.hermes/invoices/<year>/<month>/` | Generated PDFs. |
| `~/.hermes/logs/` | Rotating logs (`mail.log`, `tg.log`, `invoice.log`). |
| `~/Library/LaunchAgents/com.beers.hermes-mail.plist` | Always-on daemon. |
| `~/Library/LaunchAgents/com.beers.hermes-tg.plist` | Always-on daemon. |
| `~/Library/LaunchAgents/com.beers.hermes-invoice.plist` | Monthly cron. |
| `uv` venv at `~/Projects/hermes/.venv` | Python 3.12+ isolated. |

## Email delivery path

DNS audit on 2026-05-02 found:

- MX → `route1/2/3.mx.cloudflare.net` (Cloudflare Email Routing already active for inbound).
- SPF → `v=spf1 include:_spf.mx.cloudflare.net ~all` (authorizes Cloudflare only — **does not authorize Google SMTP**).
- DMARC → `p=quarantine; aspf=r; adkim=r` (relaxed alignment, but still enforces).
- DKIM → no key published for `google._domainkey`, `selector1._domainkey`, or `cf2024-1._domainkey`.

**Implication for free Gmail:** sending `From: dylan@ranger-beers.com` via `smtp.gmail.com` would fail SPF (Google not authorized) and fail DKIM alignment (free Gmail signs `d=gmail.com`, not `d=ranger-beers.com` — proper domain DKIM requires Workspace). DMARC then quarantines. Client invoices land in spam.

**v1 strategy — Reply-To header:** outbound mail uses `From: dzbeers747@gmail.com`, `Reply-To: dylan@ranger-beers.com`. Recipients see your personal Gmail address but replies route to the brand address. SPF passes (Google sends from a Google address), DKIM aligns (`d=gmail.com` matches `From: ...@gmail.com`), DMARC passes. Ships today, deliverable, no DNS work.

**v1.1 migration — Zoho Mail Free:** Zoho Free gives a real `dylan@ranger-beers.com` mailbox with proper SPF/DKIM/DMARC under the domain. Migration is a config swap in `secrets.toml` (host, port, creds) plus DNS edits to point MX at Zoho (or split-route via Cloudflare). One day's work, planned for the week after launch. Hermes' `send()` function is provider-agnostic — only the SMTP creds change.

## One-time external setup

These are user actions outside the codebase, listed in install order:

1. **Gmail 2FA + app password.** Generate at <https://myaccount.google.com/apppasswords>.
2. **Cloudflare Email Routing rule.** Forward `dylan@ranger-beers.com` → `dzbeers747@gmail.com`. MX is already pointed at Cloudflare; just add the routing rule. Free, ~2 min. Required so client replies reach the inbox Hermes watches.
3. **SSH access.** Pubkey already added; verified `macmini@ghengis` works.

(No SPF / DKIM / "Send mail as" / Zoho work needed for v1 — that's all v1.1.)

## Daily morning report

Auto-posted to Telegram at **08:00 ghengis-local** by a fourth `launchd` plist (`com.beers.hermes-digest.plist`). Same payload as `/digest` on demand.

**Contents (in order):**

1. **Headline counts since 8am yesterday** — `Promo: 47 (archived)`, `Client: 3`, `Personal: 2`, `Transactional: 8`, `ToReview: 1`.
2. **Client mail headlines** — sender, subject, one-line gemma4 summary. Tap-through to `/draft <id>` to surface Hermes' suggested reply.
3. **ToReview headlines** — anything tier-2 flagged below threshold. Tap-through to `/reclassify <id> <label>`.
4. **Outbound status** — `Drafts pending approval: N`, `Failed sends: N` with `/retry` shortcut, `Invoices sent yesterday: N`.
5. **Ledger snapshot** — current month: `out: $X billed / $Y paid`, `in: $Z spent`. Overdue invoices flagged red.
6. **Health line** — IMAP last-poll, Ollama latency, daemon uptime.

The 08:00 cron is independent of the always-on daemons — even if `hermes-mail` is asleep, the digest job reads SQLite directly and posts. If the daemon is down, the digest header includes a `⚠ DAEMON DOWN <duration>` warning.

A second optional cron at **18:00** posts an end-of-day variant focused on `Drafts pending approval` only — toggle via `~/.config/hermes/secrets.toml` `[digest] evening_report = true`. Off by default.

## Cron jobs (launchd plists)

| Plist | Schedule | Job |
|-------|----------|-----|
| `com.beers.hermes-mail.plist` | always-on (`KeepAlive=true`) | IMAP IDLE daemon |
| `com.beers.hermes-tg.plist` | always-on (`KeepAlive=true`) | Telegram bot polling |
| `com.beers.hermes-digest.plist` | `StartCalendarInterval Hour=8 Minute=0` | Morning report |
| `com.beers.hermes-digest-evening.plist` | `Hour=18 Minute=0` (disabled by default) | Evening drafts-pending report |
| `com.beers.hermes-invoice.plist` | `Day=1 Hour=9 Minute=0` | Monthly invoice generation |

All plists set `RunAtLoad=false` for the cron-style ones, `RunAtLoad=true` for the daemons. Logs to `~/.hermes/logs/<job>.log`, rotated weekly via `newsyslog.conf` entry written at install time.

## Open questions / deferred

- **Boarhog domain + amount:** captured via first-run Telegram prompt today. Won't block install.
- **Inbound invoice tracking (scope item 8):** deferred to v2. Receipts/invoices land in `Transactional` label and `ledger` direction `'in'` automatically, but no spreadsheet export yet.
- **Multi-recipient invoices (CC/BCC accounting):** deferred. Single recipient per client for now.
- **Per-client invoice templates:** v1 ships one shared `standard.html`. Adding overrides is a one-file change in v2.
- **Zoho Mail Free migration (v1.1):** swap SMTP creds in `secrets.toml`, add MX/SPF/DKIM/DMARC for Zoho on `ranger-beers.com`, remove the `Reply-To` header workaround so `From:` is the brand address. Planned the week after v1 ships.
- **Inbound DKIM verification on incoming client mail:** v1 trusts Gmail's spam decisions. v2 could surface DKIM/SPF/DMARC pass/fail in the digest as a phishing signal.

## Acceptance criteria for v1

- [ ] `hermes-mail` runs under `launchd`, survives reboot, reconnects on IMAP drop.
- [ ] Promos auto-archived under `Hermes/Promo`; verified zero false-positives in 7-day soak.
- [ ] Real-time Telegram ping fires within 30s of a client email landing.
- [ ] `/digest` returns counts and headlines for each label.
- [ ] `/invoice boarhog` end-to-end: Telegram preview → `/approve` → email lands in client inbox with attached PDF, `From: dylan@ranger-beers.com`.
- [ ] May 2026 Boarhog invoice sent and logged in `ledger`.
- [ ] All secrets out of `~/Documents/`; existing `telegram-bot.py` updated to read from `~/.config/hermes/secrets.toml`.
