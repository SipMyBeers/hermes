# Hermes v2 Phase 2+3 — Lead Engine, Pipeline, Surfaces

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface every website lead (direct, Josh-forward, contact-form), track each prospect through a pipeline (`lead → replied → quoted → active → invoiced → closed`), nudge on stale replies, and add the `/pipeline`+`/stage` commands plus the 8am email digest.

**Architecture:** New `pipeline/store.py` over a v11 `pipeline` table; lead detection extends `extract/lead.py` with form + partner-forward sources and an LLM confirm; daemon + Sent watcher feed pipeline timestamps; stale job grows a pipeline section reusing `/draft`; email digest is a new job using existing SMTP + digest data, self-exempt via an `X-Hermes-Digest` header rule.

**Tech Stack:** existing package; Jinja2 (already a dep) for digest HTML.

### Task 1: db v11 pipeline table + store

Files: `src/hermes/db.py`, create `src/hermes/pipeline/__init__.py` + `src/hermes/pipeline/store.py`, test `tests/test_pipeline_store.py`.

- [ ] v11: `pipeline(contact_email TEXT PRIMARY KEY, display_name TEXT, project TEXT, stage TEXT NOT NULL DEFAULT 'lead' CHECK(stage IN ('lead','replied','quoted','active','invoiced','closed')), source TEXT, last_inbound TEXT, last_outbound TEXT, created TEXT NOT NULL, updated TEXT NOT NULL, notes TEXT)`.
- [ ] store API (all lowercase the email key, all bump `updated`):
  - `upsert_lead(db, contact_email, display_name, source, project=None)` — insert stage='lead'; if row exists, no stage change (just touch updated).
  - `touch_inbound(db, contact_email)` / `touch_outbound(db, contact_email)` — set timestamp now; touch_outbound auto-advances stage lead→replied; both no-op (return False) when the contact isn't in the pipeline.
  - `set_stage(db, contact_or_name_fragment, stage)` — exact email OR unique substring match on email/display_name/project; returns matched email or raises ValueError on 0/2+ matches.
  - `board(db)` — rows grouped/ordered by stage for display.
- [ ] Tests: upsert idempotent; touch_outbound advances lead→replied only from lead; set_stage fragment match + ambiguity error; board ordering.

### Task 2: leads config

Files: `src/hermes/config.py`, `clients.toml`, test `tests/test_config.py`.

- [ ] `[leads]` reserved table in clients.toml → `LeadsConfig(form_address: str = "", ignore_domains: tuple[str, ...] = ())` on `Config.leads`. Reserved-key skip extends to `{"partners", "leads"}`.
- [ ] clients.toml entry: `form_address = "leads@ranger-beers.com"`, `ignore_domains = ["kumquat.life", "zeepo.com", "gohighlevel.com", "uppatop.com", "surancebay.com", "xcelsolutions.com"]`.

### Task 3: lead detection extensions

Files: `src/hermes/extract/lead.py`, test `tests/test_extract_lead.py` (+ new `tests/test_lead_sources.py`).

- [ ] `detect_lead(..., ignore_domains: set[str] = frozenset())` — sender domain in ignore list → never a lead.
- [ ] `detect_form_lead(msg, form_address) -> bool` — form_address appears in To/Cc/Delivered-To/X-Forwarded-To (case-insensitive). Deterministic.
- [ ] `extract_forward_original(msg) -> tuple[name, addr] | None` — first `From: Name <addr>` line inside the body head (forwarded block), regex on first 3000 chars.
- [ ] `detect_partner_lead(msg, clients, partner_addrs, ignore_domains) -> LeadSignal` — original sender from forward block that is NOT a client contact/domain, NOT a partner, NOT ignored → lead (reason mentions partner forward).
- [ ] `confirm_lead_llm(msg, ...) -> bool` — Ollama qwen3 think=false yes/no "is this a prospective client asking about web/site work?"; returns True on any LLM failure (never drop a lead on infra failure).

### Task 4: daemon lead wiring

Files: `src/hermes/mail/daemon.py`, test `tests/test_mail_daemon.py`.

- [ ] Form leads first: if `detect_form_lead` → treat as lead regardless of classification rules (label Hermes/Lead, ping, pipeline upsert source='form', keep in inbox).
- [ ] Partner-review path (partner mail, no client match) → `detect_partner_lead`; hit → lead flow, source='partner', contact = forwarded original sender.
- [ ] Direct leads: existing detect_lead hit → `confirm_lead_llm` gate (skipped/true when no LLM) → lead flow, source='direct'.
- [ ] All lead flows: `upsert_lead` + existing `_persist_lead` + label + ping (one shared `_handle_lead` helper).
- [ ] Inbound from a known pipeline contact (any classification except promo): `touch_inbound`.
- [ ] `detect_lead` call passes `ignore_domains` from cfg.leads.

### Task 5: outbound → pipeline

Files: `src/hermes/mail/outbound.py`, test `tests/test_outbound_watcher.py`.

- [ ] In `process_sent`, for every recipient (client-matched or not): `touch_outbound(db, addr)` — store no-ops for unknown contacts. lead→replied advance comes free from Task 1.

### Task 6: invoice → pipeline

Files: `src/hermes/telegram/commands.py` (where approved invoice outbox rows are sent) or `src/hermes/invoice/job.py`, test alongside existing invoice tests.

- [ ] When an outbox row with kind='invoice' is sent successfully: `set_stage`-style direct update to 'invoiced' for the client's contact_email (resolve client → contact via cfg.clients; upsert row if missing with stage='invoiced', source='direct').

### Task 7: stale pipeline nudges

Files: `src/hermes/stale/check.py`, `src/hermes/stale/job.py`, tests `tests/test_stale_check.py`.

- [ ] `find_stale_pipeline(db, days=4)` — rows in stage lead/replied/quoted where last_outbound > last_inbound (or last_inbound NULL) and last_outbound older than N days.
- [ ] Job output adds a "💤 Waiting on reply" section: contact, stage, days waiting, and `/draft <uid>` for the contact's most recent inbound uid (lookup in messages by sender LIKE) when available.

### Task 8 (Phase 3): /pipeline + /stage commands

Files: `src/hermes/telegram/commands.py`, test `tests/test_telegram_commands.py` if present (else new file mirroring existing command tests).

- [ ] `/pipeline` — board grouped by stage, one line per contact (`name — project · 3d since contact`). Empty → friendly message.
- [ ] `/stage <email-or-fragment> <stage>` — calls store.set_stage; replies with the transition or the ambiguity/0-match error text. HELP_TEXT updated.

### Task 9 (Phase 3): morning email digest

Files: create `src/hermes/digest/email_job.py`, template inline (Jinja2 string), `pyproject.toml` entrypoint `hermes-digest-email`, `ops/launchd/com.beers.hermes-digest-email.plist` (8:00 daily), daemon self-exemption, tests `tests/test_digest_email.py`.

- [ ] Build context: pipeline board, leads detected last 24h (leads table), open actions (actions table, status='open', top 10), stale pipeline rows, 24h classification counts.
- [ ] HTML via Jinja2 string template (simple table layout, no external assets); plaintext fallback body.
- [ ] Send via existing `build_message` + `SmtpClient.send` to cfg.gmail.imap_user; add header `X-Hermes-Digest: 1`.
- [ ] Daemon exemption: in `_process_fetched`/`process_message` front: `msg["X-Hermes-Digest"]` present → apply label `Hermes/Digest`, star (`+FLAGS \\Flagged` — add `ImapClient.star(uid)`), persist row classification 'other', skip everything else.
- [ ] launchd plist: StartCalendarInterval 08:00, UserName macmini, same env as digest.

### Task 10: deploy + verify

- [ ] Full suite; bundle → ghengis; uv sync; bootstrap new digest-email plist; kickstart mail + watchdog sanity.
- [ ] Live checks: pipeline table exists (v11); seed TBN + Max as stage='active' rows (one-off SQL, source='partner'); send `/pipeline` via Telegram API to verify; run `hermes-digest-email --print` then live send; confirm digest email lands labeled+starred and is NOT reprocessed.
- [ ] Update memory.

## Self-review

Spec 2.1 sources → T3/T4 (kumquat exclusion via ignore_domains T2); 2.2 table/transitions → T1/T5/T6/T8; 2.3 stale → T7; 3.1 commands → T8; 3.2 digest → T9. Backfill CLI from spec deploy-day section: superseded — the live daemon already drained the backlog (Phase 1); leads backfill limited to seeding TBN/Max rows in T10.
