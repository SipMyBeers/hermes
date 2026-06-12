# Hermes v2 Phase 1 — Josh + Client Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route uncle-Josh (partner) and client threads out of the inbox into per-project labels, match clients by contact email (not just domain), and auto-file Dylan's outbound mail by client.

**Architecture:** Extend the tier-1 rule classifier with contact-email + partner matching; new `classifier/partner.py` infers which client a partner thread belongs to; daemon archives client-labeled mail (urgent enrichment ping already exists and is unchanged); a Sent-folder poller files outbound by client. Config grows optional-billing clients + `[partners]`.

**Tech Stack:** existing hermes package; no new deps. Tests: `uv run pytest`.

**Scope notes:**
- Action-detection ping = existing `summarize_for_brief` urgent path (unchanged). No new LLM call in Phase 1.
- Partner-forward lead extraction is Phase 2; here a partner thread that matches no client falls through to the existing lead/review flow.
- Pipeline `last_outbound` is Phase 2; the Sent watcher here only files labels (and is built so Phase 2 can hook it).

### Task 1: Config — optional billing, contact matching, [partners]

**Files:** Modify `src/hermes/config.py`, `clients.toml`; Test `tests/test_config.py`

- [ ] Tests: client with only `contact_email` + `active` parses (billing defaults 0/""); `[partners]` table parses to `Config.partners: Mapping[str, tuple[str, ...]]` (addresses lowercased) and is NOT a client; existing boarhog-style full config still parses.
- [ ] `ClientConfig`: `domain: str = ""`, `billing_amount_cents: int = 0`, `billing_terms: str = ""`, `service_description: str = ""` become defaulted; `contact_email` stays required.
- [ ] Parser: `RESERVED = {"partners"}`; skip reserved keys in the client loop; parse `[partners]` values as list-of-addresses.
- [ ] Add to local `clients.toml`:

```toml
[partners]
josh = ["magic@rainbowlasers.com", "joshualogan@gmail.com"]

[tbn]
domain = "tbn.net"
contact_email = "todd@tbn.net"
active = true

[maxcrofton]
contact_email = "maxcrofton@gmail.com"
active = true
```

- [ ] Guard: confirm `hermes-invoice` job skips clients with `billing_amount_cents == 0` (read `src/hermes/invoice/job.py`; add skip + test if absent).
- [ ] Commit.

### Task 2: Rules — contact-email client match + partner label

**Files:** Modify `src/hermes/classifier/rules.py`; Test `tests/test_classifier_rules.py`

- [ ] Tests: sender `maxcrofton@gmail.com` with contacts map → client/maxcrofton (and gmail.com NOT treated as client domain); sender `magic@rainbowlasers.com` with partner set → label `partner`, confidence 1.0; partner check beats List-Unsubscribe? No — partner check goes after personal, before client-domain (a partner newsletter is still partner mail? No: keep List-Unsubscribe first; Josh's threads don't carry it).
- [ ] `classify(...)` gains `client_contacts: dict[str, ClientHint] | None = None` and `partner_addrs: set[str] | None = None`. After the personal-allowlist rule: exact sender-addr match in `client_contacts` → client; sender in `partner_addrs` → `RuleResult(label="partner", confidence=1.0, reason="sender is partner <addr>")`.
- [ ] Commit.

### Task 3: Partner project inference

**Files:** Create `src/hermes/classifier/partner.py`; Test `tests/test_partner_infer.py`

- [ ] `infer_client(msg, clients) -> str | None`. Signals, first hit wins:
  1. any To/Cc/From address matches a client `contact_email` or `domain`;
  2. a `From: ...@<client-domain>` or contact email inside the body's forwarded block (regex over first 3000 chars);
  3. client key or `bill_to_company` token appears in the subject (case-insensitive word match).
- [ ] Tests: Todd-CC case (todd@tbn.net in To) → "tbn"; Max forward (body contains `From: Max Crofton <maxcrofton@gmail.com>`) → "maxcrofton"; subject "Quote #1052221 / TBN" → "tbn"; unrelated partner mail → None.
- [ ] Commit.

### Task 4: Daemon routing — partner resolution + client archive

**Files:** Modify `src/hermes/mail/daemon.py`; Test `tests/test_mail_daemon.py`

- [ ] `classify_message`: pass `client_contacts=_client_contacts(cfg)` and `partner_addrs=_partner_addrs(cfg)` into `rule_classify`. On rule label `partner`: run `infer_client`; hit → final label `client` with that client key, reason "partner thread for <client>"; miss → label `review` (stays in inbox; lead detection still runs downstream).
- [ ] `process_message`: after `apply_label`, archive when `classification.label == "client"` (same try/except as promo archive). Client mail keeps enrichment (urgent ping path unchanged).
- [ ] Tests: partner mail w/ Todd CC → labeled `Hermes/Client/tbn` + archived; client contact-email sender → archived; review mail NOT archived.
- [ ] Commit.

### Task 5: Outbound (Sent) watcher

**Files:** Create `src/hermes/mail/outbound.py`; Modify `src/hermes/mail/imap_client.py` (folder switch), `src/hermes/mail/daemon.py` (loop call), `src/hermes/db.py` (v10 `kv` table); Test `tests/test_outbound_watcher.py`

- [ ] db v10: `CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);` + `kv_get/kv_set` helpers (tests in `test_db.py`).
- [ ] `ImapClient.set_folder(name)` thin wrapper over `self._box.folder.set(name)`.
- [ ] `outbound.process_sent(db, cfg, imap) -> int`: set folder `"[Gmail]/Sent Mail"`, fetch uids > `kv_get("sent_anchor", 0)` (reuse `fetch_recent`), for each: match any recipient against client contacts/domains → `imap.apply_label(uid, "Hermes/Client/<name>")`; always advance anchor; finally restore folder `"INBOX"` (try/finally). First run with no anchor: set anchor to current max uid, process nothing (no backlog spam).
- [ ] Daemon loop: call `process_sent` once per cycle after inbound batch, wrapped in try/except (Sent filing must never kill inbound triage).
- [ ] Tests: recipient todd@tbn.net → tbn label; unknown recipient → no label, anchor still advances; first-run anchoring; folder restored on exception.
- [ ] Commit.

### Task 6: Deploy + live verification

- [ ] Full suite green. Bundle → ghengis, `uv sync`, kickstart `hermes-mail`.
- [ ] Add tbn/maxcrofton/partners to ghengis `clients.toml` (repo file ships it; confirm pull took it).
- [ ] Verify: log shows Sent anchor set; send-window check — recent Josh thread message gets `Hermes/Client/tbn` and leaves inbox (Gmail MCP); promos still archiving; heartbeat fresh.
- [ ] Commit fixups; update memory.

## Self-review

Spec 1.1 → T1; 1.2 routing+archive → T2-T4 (action pings = existing urgent enrichment, noted in scope); 1.3 → T5. Type check: `client_contacts` dict keyed by lowercased addr → ClientHint, consistent T2/T4; `partner_addrs` lowercased set, consistent T1/T2/T4.
