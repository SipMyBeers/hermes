# Hermes v2 — Revival + Client Ops

**Date:** 2026-06-11
**Owner:** Dylan Beers
**Host:** ghengis (Mac mini, Tailscale `100.116.82.14`, user `macmini`)
**Status:** Design approved 2026-06-11.
**Prior art:** `2026-05-02-hermes-design.md` (v1, deployed, ran until 2026-05-22, died silently).

## Goal

Bring Hermes back from the dead, make silent death impossible, and extend it
from "promo filter + invoicer" into the client-ops layer for Dylan's web
business: route uncle-Josh client chatter out of the inbox, surface every
website lead from all three sources, and track each prospect through a
lightweight pipeline from first contact to invoice.

## What changed since v1

- Hermes died 2026-05-22 ~12:43 (log stops mid-stream, no error; launchd
  agents present in `~/Library/LaunchAgents` on ghengis but not loaded —
  most likely a reboot after which the GUI session never re-bootstrapped
  the agents). Inbox has accumulated ~3 weeks of mess (200+ threads/week).
- Two new client gigs arrived via uncle Josh (`magic@rainbowlasers.com`):
  TBN (`todd@tbn.net`, $1k Squarespace redesign) and Max Crofton
  (`maxcrofton@gmail.com`, $250 brochure site). Josh CCs Dylan on every
  client thread; this is the dominant non-promo inbox noise.
- Dylan is about to start outbound client email himself; outbound needs the
  same organization as inbound.

## Decisions (from 2026-06-11 brainstorm)

| Question | Decision |
|---|---|
| Architecture | Revive Hermes fully on ghengis; extend in place (no Gmail-native filters, no rewrite) |
| Lead sources | Josh forwards/CCs + direct inquiries + contact-form emails. **Kumquat pipeline stays separate** — excluded by rule |
| Josh client chatter | Per-project label, skip inbox (archive), daily digest; real-time ping only when action needed from Dylan |
| Outbound | Auto-file by client, stale-reply nudges, pipeline stages — all three |
| Junk policy | **Archive, don't trash** (v1 trashed; change it) |
| Surface | Telegram (existing bot) **plus** a morning email digest to own inbox |

## Non-goals (unchanged from v1 unless noted)

- No auto-send. All outbound drafts require Telegram `/approve`.
- No paid APIs, no cloud orchestrators, SMTP only.
- No Kumquat/BullpenLM integration — that funnel has its own system.
- Not building a general CRM; pipeline is one table and one digest section.

## Phase 0 — Resurrection

**0.1 Diagnose & restart.** On ghengis: inspect `launchctl print gui/<uid>`,
system reboot history (`last reboot`), and agent bootstrap state. Re-bootstrap
all `com.beers.hermes-*` agents. Root-cause goes in the commit message; if
the cause is "agents don't survive reboot/login state", fix the install
script (`ops/install.sh`) so bootstrap is idempotent and survives reboots
(RunAtLoad + KeepAlive where appropriate).

**0.2 Fix known bugs (visible in `~/.hermes/logs/mail.log`):**
- `idle dropped: a bytes-like object is required, not 'str'` — recurring
  IMAP IDLE reconnect bug in `mail/daemon.py` `run_loop`. Reproduce in a
  test, fix the str/bytes handling.
- `llm response unparseable: ''` — classifier LLM returns empty/non-JSON.
  Use Ollama `format=json`, add one retry, and on second failure fall back
  to `ToReview` (current behavior) without the warning spam.

**0.3 Watchdog — the real fix.** v1 died silently for 3 weeks.
- `hermes-mail` writes a heartbeat (`heartbeats` table: component, ts) on
  every IDLE cycle and every poll.
- New tiny launchd job `hermes-watchdog` (every 30 min): if newest
  `hermes-mail` heartbeat is older than 60 min, send a Telegram alert
  ("hermes-mail silent since <ts>") and attempt one `launchctl kickstart`.
  Watchdog has no dependencies on the daemon's code paths (separate
  entrypoint, direct sqlite read), so a daemon bug can't take it down too.
- Watchdog alert failure mode: if Telegram itself fails, write to
  `~/.hermes/logs/watchdog.log` and exit nonzero so launchd surfaces it.

**0.4 Junk policy change.** `mail/daemon.py`: promo path changes
`move_to_trash` → archive (remove `\\Inbox`, keep `Hermes/Promo` label).
The `learned_promos` table and List-Unsubscribe rule stay as-is.

## Phase 1 — Josh + client routing

**1.1 Config.** `clients.toml` additions (billing fields optional for
non-billing clients):

- `[tbn]` — domain `tbn.net`, contact `todd@tbn.net`, project label `TBN`.
- `[maxcrofton]` — contact `maxcrofton@gmail.com`, project label `MaxCrofton`.
- New top-level `[partners]` table: `josh = ["magic@rainbowlasers.com", "joshualogan@gmail.com"]`.
  A partner is a relationship-holder who CCs/forwards client mail; partner
  mail is never a lead by itself but is a *carrier* of client threads and
  leads.

**1.2 Routing rule (new tier-1 rules, before LLM fallback):**
- Sender or any recipient matches a client domain/contact → label
  `Hermes/Client/<name>`, archive (skip inbox), log. No real-time ping by
  default.
- Sender is a partner → infer project: match other participants + subject
  against known clients; if matched, treat as that client's thread (same as
  above). If no client matches, run lead detection (Phase 2); if that also
  misses, label `Hermes/Review` and keep in inbox.
- **Action detection** (the only real-time ping for client threads): LLM
  check on client-thread messages — does this message ask Dylan a question,
  grant access/credentials, or request a deliverable? If yes → Telegram ping
  with the existing gmail deep-link card. Prompt returns
  `{needs_action: bool, reason: str}`; threshold is boolean, no confidence
  juggling.

**1.3 Outbound watcher.** `hermes-mail` polls `[Gmail]/Sent Mail` (IMAP,
every cycle; store last-seen UID in `messages` like inbound). Sent mail to a
known client/lead contact → apply the same `Hermes/Client/<name>` label
(thread now reads two-way under one label) and update pipeline
`last_outbound` (Phase 2). Sent mail is never pinged or digested as "new
mail"; it only feeds filing + pipeline.

## Phase 2 — Lead engine + pipeline

**2.1 Lead sources** (first two extend existing `extract/lead.py` + `leads`
table):
- **Direct inquiry** (exists): unknown sender + outreach-pattern regex.
  Keep, but add LLM confirmation to cut false positives: regex hit → LLM
  yes/no "is this a prospective client asking about web work?".
- **Josh forward/CC** (new): partner message whose external participant is
  unknown (not in clients.toml, not in leads) → lead candidate. Forwarded
  bodies count: parse `Forwarded message` blocks for the original sender
  (e.g. the Max Crofton fwd pattern).
- **Contact form** (new): client sites POST form fills to a dedicated
  address `leads@ranger-beers.com` (Cloudflare Email Routing → Gmail,
  already catch-all). Deterministic rule: `Delivered-To`/`To` matches the
  leads address → lead, no LLM needed. (Wiring the actual site forms is
  per-site work outside this repo; Hermes just owns the address rule.)
- **Exclusion:** sender/participant domains in a new `lead_ignore_domains`
  config list (seed: kumquat/Zeepo/GHL domains, FFL/insurance domains)
  never create leads.

On lead detection: label `Hermes/Lead`, real-time Telegram ping ("New lead:
<name> — <one-line reason>" + gmail link), insert pipeline row.

**2.2 Pipeline table.** New `pipeline` table:

    contact_email TEXT PRIMARY KEY, display_name TEXT, project TEXT,
    stage TEXT CHECK(stage IN ('lead','replied','quoted','active','invoiced','closed')),
    source TEXT,            -- 'direct' | 'partner' | 'form'
    last_inbound TEXT, last_outbound TEXT,
    created TEXT, updated TEXT, notes TEXT

Stage transitions:
- auto `lead → replied` when outbound watcher sees Dylan reply to the contact;
- auto `→ invoiced` when `hermes-invoice` sends to that contact;
- everything else manual via Telegram `/stage <email-or-name> <stage>`;
- `/pipeline` command prints the board grouped by stage.

**2.3 Stale nudges.** Point the existing `stale` module at `pipeline`
instead of raw threads: any row in `lead/replied/quoted` where
`last_outbound > last_inbound` and no inbound for N days (default 4) →
digest entry "X hasn't replied in N days" + a pre-drafted follow-up queued
for `/approve`. Drafting reuses the existing drafter + availability.toml
slot-weaving.

## Phase 3 — Surfaces

**3.1 Telegram** (existing bot): keep all v1 commands; add `/pipeline`,
`/stage`. Pings as scoped above: leads + needs-action client messages +
watchdog alerts only.

**3.2 Morning email digest** (new, 08:00 alongside the Telegram digest):
HTML email to `dzbeers747@gmail.com` via existing SMTP `send()`, labeled
`Hermes/Digest` + starred so it tops the inbox. Sections: pipeline board,
new leads (24h), needs-action list, stale nudges, traffic counts by
category. Reuses `digest/build.py` data with an HTML template (Jinja2
already in deps). The digest email itself is rule-exempt (never classified,
never archived by the daemon — guard by sender+subject marker).

## Deploy-day backfill

One-shot `hermes-backfill` CLI: run the full v2 classifier over
`in:inbox newer_than:30d` (IMAP search), applying labels/archive exactly as
live mail would, with `--dry-run` first showing planned moves as a table.
Dylan eyeballs the dry run, then runs live. Backfill also seeds the
pipeline: TBN and MaxCrofton enter at `active`, any detected unconverted
leads at `lead`.

## Error handling

- Every new module follows v1 conventions: failures log + degrade to
  `ToReview` (never drop mail on the floor, never guess on send).
- Outbound watcher and watchdog are read-only except labels; worst case is
  a mislabeled thread, recoverable via `/reclassify`.
- Backfill is the only bulk mutation; gated behind dry-run.

## Testing

TDD per repo convention (`uv run pytest`). New test files:
`test_watchdog.py`, `test_routing_partner.py`, `test_action_detect.py`,
`test_outbound_watcher.py`, `test_lead_partner_fwd.py`,
`test_lead_form.py`, `test_pipeline.py`, `test_digest_email.py`,
`test_backfill.py`. Plus regression tests for the two Phase-0 bugs.
Live-mail behaviors verified on ghengis with `--dry-run --since 24h`
before each launchd reload.

## Sequencing

Phases ship in order; each phase is independently deployable and leaves
Hermes better than before it. Phase 0 alone already fixes the daily pain
(promos archived again). Phase 1 kills the Josh noise. Phase 2/3 add the
lead/pipeline value.
