# Hermes v2 Phase 0 — Resurrection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Hermes mail daemon back online on ghengis, fix the two bugs in its logs, make silent death impossible (watchdog + boot-survival), and switch promos from trash to archive.

**Architecture:** Three small code fixes in the existing Python package (IDLE bytes bug, LLM retry, heartbeat) + one new watchdog entrypoint, then an ops change: hermes launchd jobs move from per-user LaunchAgents (require GUI login — root cause of the June outage) to system LaunchDaemons with `UserName macmini`.

**Tech Stack:** Python 3.12/uv, imap_tools, httpx/Ollama, sqlite3, launchd. Tests: `uv run pytest`.

**Root-cause notes (from 2026-06-11 diagnosis):**
- Log stops 2026-05-22 12:43; ghengis rebooted Jun 4/5; `launchctl list` shows no hermes jobs; plists live in `~/Library/LaunchAgents` which only load on GUI login that never happened. Death = sleep/shutdown + reboot without login.
- `idle dropped: a bytes-like object is required, not 'str'` = `imap_client.idle()` does `"EXISTS" in r` over `bytes` responses from `idle.poll()`.
- `llm response unparseable: ''` = Ollama returns empty `response`; classifier already uses `format=json`, lacks a retry.
- Trash-vs-archive is already config: `[mail] delete_promos` in `~/.config/hermes/config.toml` on ghengis is `true`; flip to `false`.

---

### Task 1: Fix IDLE bytes/str crash

**Files:**
- Modify: `src/hermes/mail/imap_client.py:71-78` (`idle`)
- Test: `tests/test_imap_client.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_imap_client.py`)

```python
def test_idle_handles_bytes_responses(monkeypatch):
    """idle.poll() returns bytes lines from imaplib; idle() must not TypeError."""
    from hermes.mail.imap_client import ImapClient
    from hermes.config import GmailConfig

    client = ImapClient(GmailConfig(imap_user="x@y.z", imap_app_password="pw"))

    class FakeIdle:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def poll(self, timeout):
            return [b"* 42 EXISTS", b"* 1 RECENT"]

    class FakeBox:
        idle = FakeIdle()

    client._box = FakeBox()
    assert client.idle(timeout=1) is True


def test_idle_handles_str_responses():
    """Defensive: also accept str lines (imap_tools version drift)."""
    from hermes.mail.imap_client import ImapClient
    from hermes.config import GmailConfig

    client = ImapClient(GmailConfig(imap_user="x@y.z", imap_app_password="pw"))

    class FakeIdle:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def poll(self, timeout):
            return ["* 42 EXISTS"]

    class FakeBox:
        idle = FakeIdle()

    client._box = FakeBox()
    assert client.idle(timeout=1) is True
```

Note: if `GmailConfig` has different required fields, construct with whatever
`tests/test_imap_client.py` already uses — copy its existing fixture pattern.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_imap_client.py -k idle -v`
Expected: `test_idle_handles_bytes_responses` FAILS with `TypeError: a bytes-like object is required, not 'str'`

- [ ] **Step 3: Fix `idle()`**

```python
    def idle(self, timeout: int = 29 * 60) -> bool:
        """Block until new mail or timeout. Returns True if new mail arrived."""
        assert self._box, "call connect() first"
        with self._box.idle as idle:
            responses = idle.poll(timeout=timeout)
        decoded = [
            r.decode("ascii", errors="replace") if isinstance(r, (bytes, bytearray)) else str(r)
            for r in responses
        ]
        return any("EXISTS" in r for r in decoded)
```

- [ ] **Step 4: Run tests** — `uv run pytest tests/test_imap_client.py -v` → all PASS
- [ ] **Step 5: Commit** — `git add -A && git commit -m "fix(mail): decode IDLE poll responses — bytes TypeError caused reconnect churn"`

### Task 2: LLM classifier retry on empty/unparseable

**Files:**
- Modify: `src/hermes/classifier/llm.py` (`classify_llm`)
- Test: `tests/test_classifier_llm.py`

- [ ] **Step 1: Write the failing test** (append; mirror the file's existing httpx mocking pattern)

```python
def test_classify_llm_retries_on_empty_response(monkeypatch):
    """First Ollama call returns empty response; second returns valid JSON.
    classify_llm must retry once instead of giving up."""
    import hermes.classifier.llm as llm_mod
    calls = {"n": 0}

    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"response": ""}
            return {"response": '{"label": "promo", "confidence": 0.9, "why": "newsletter"}'}

    monkeypatch.setattr(llm_mod.httpx, "post", lambda *a, **k: FakeResp())

    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = "deals@shop.com"
    msg["Subject"] = "50% off"
    msg.set_content("Big sale")

    result = llm_mod.classify_llm(msg)
    assert result.label == "promo"
    assert calls["n"] == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_classifier_llm.py -k retries -v`
Expected: FAIL — `result.label == "unknown"` (no retry today)

- [ ] **Step 3: Implement retry** — restructure the parse block into one attempt loop:

```python
def classify_llm(
    msg: Message,
    model: str = "qwen3:4b",
    host: str = "http://localhost:11434",
    timeout: float = 30.0,
    retries: int = 1,
) -> LLMResult:
    prompt = _PROMPT.format(
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        body=_body_text(msg)[:1500],
    )
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
    last_err: str = ""
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(f"{host}/api/generate", json=payload, timeout=timeout)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("ollama unreachable: {}", e)
            return LLMResult(label="unknown", confidence=0.0, why=f"ollama error: {e}")
        raw = resp.json().get("response", "")
        try:
            data = json.loads(raw)
            label = str(data.get("label", "")).lower()
            if label not in ALLOWED_LABELS:
                label = "other"
            confidence = float(data.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            return LLMResult(label=label, confidence=confidence, why=str(data.get("why", "")))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            last_err = str(e)
            if attempt < retries:
                logger.debug("llm response unparseable (attempt {}): {!r}; retrying", attempt + 1, raw[:120])
                continue
            logger.warning("llm response unparseable after retries: {!r} -> {}", raw[:200], e)
    return LLMResult(label="unknown", confidence=0.0, why=f"unparseable response: {last_err}")
```

- [ ] **Step 4: Run full classifier tests** — `uv run pytest tests/test_classifier_llm.py -v` → all PASS
- [ ] **Step 5: Commit** — `git commit -am "fix(classifier): retry once on empty/unparseable Ollama response"`

### Task 3: Heartbeats table + daemon heartbeat writes

**Files:**
- Modify: `src/hermes/db.py` (SCHEMA_VERSION 8→9, `_SCHEMA_V9`, migrate branch)
- Modify: `src/hermes/mail/daemon.py` (`run_loop` writes heartbeat each cycle)
- Test: `tests/test_db.py`, `tests/test_watchdog.py` (created here, used by Task 4)

- [ ] **Step 1: Failing test for migration** (append to `tests/test_db.py`, copying its in-memory connect pattern)

```python
def test_migrate_v9_creates_heartbeats(tmp_path):
    from hermes.db import connect, migrate
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    conn.execute(
        "INSERT INTO heartbeats (component, ts) VALUES ('hermes-mail', '2026-06-11T00:00:00+00:00') "
        "ON CONFLICT(component) DO UPDATE SET ts=excluded.ts"
    )
    row = conn.execute("SELECT ts FROM heartbeats WHERE component='hermes-mail'").fetchone()
    assert row[0].startswith("2026-06-11")
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_db.py -k heartbeats -v` → FAIL `no such table: heartbeats`

- [ ] **Step 3: Implement migration** in `src/hermes/db.py`:

```python
SCHEMA_VERSION = 9

_SCHEMA_V9 = """
CREATE TABLE IF NOT EXISTS heartbeats (
    component TEXT PRIMARY KEY,
    ts        TEXT NOT NULL
);
"""
```

and in `migrate()` before the final `PRAGMA user_version`:

```python
    if current < 9:
        # v8 -> v9: heartbeats table for the Phase-0 watchdog. Additive.
        conn.executescript(_SCHEMA_V9)
```

- [ ] **Step 4: Heartbeat helper + daemon wiring.** Add to `src/hermes/db.py`:

```python
def beat(conn: sqlite3.Connection, component: str) -> None:
    """Upsert a liveness timestamp; read by hermes-watchdog."""
    from datetime import datetime, timezone
    conn.execute(
        "INSERT INTO heartbeats (component, ts) VALUES (?, ?) "
        "ON CONFLICT(component) DO UPDATE SET ts=excluded.ts",
        (component, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
```

In `src/hermes/mail/daemon.py` `run_loop`, first line inside `while True:` add `beat(db, "hermes-mail")`, and also call it right after the `for fetched ...` loop completes (so long IDLE blocks still bound staleness to one cycle). Import: `from hermes.db import beat` (match the module's existing import style — it may import `connect, migrate` already; extend that line).

- [ ] **Step 5: Run** — `uv run pytest tests/test_db.py tests/test_mail_daemon.py -v` → PASS
- [ ] **Step 6: Commit** — `git commit -am "feat(db,mail): v9 heartbeats table; daemon beats every loop cycle"`

### Task 4: Watchdog entrypoint

**Files:**
- Create: `src/hermes/watchdog.py`
- Modify: `pyproject.toml` (add `hermes-watchdog = "hermes.watchdog:main"` to scripts)
- Test: `tests/test_watchdog.py`

- [ ] **Step 1: Failing tests**

```python
"""Watchdog: alert + kickstart when hermes-mail's heartbeat goes stale."""
from datetime import datetime, timedelta, timezone


def _db(tmp_path):
    from hermes.db import connect, migrate
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    return conn


def _set_beat(conn, age_minutes: int):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    conn.execute(
        "INSERT INTO heartbeats (component, ts) VALUES ('hermes-mail', ?) "
        "ON CONFLICT(component) DO UPDATE SET ts=excluded.ts", (ts,))
    conn.commit()


def test_fresh_heartbeat_no_alert(tmp_path):
    from hermes.watchdog import check
    conn = _db(tmp_path)
    _set_beat(conn, age_minutes=5)
    sent = []
    assert check(conn, notify=sent.append, kickstart=lambda: None, max_age_minutes=60) is True
    assert sent == []


def test_stale_heartbeat_alerts_and_kickstarts(tmp_path):
    from hermes.watchdog import check
    conn = _db(tmp_path)
    _set_beat(conn, age_minutes=90)
    sent, kicks = [], []
    assert check(conn, notify=sent.append, kickstart=lambda: kicks.append(1), max_age_minutes=60) is False
    assert len(sent) == 1 and "hermes-mail" in sent[0] and len(kicks) == 1


def test_missing_heartbeat_row_alerts(tmp_path):
    from hermes.watchdog import check
    conn = _db(tmp_path)
    sent = []
    assert check(conn, notify=sent.append, kickstart=lambda: None, max_age_minutes=60) is False
    assert len(sent) == 1
```

- [ ] **Step 2: Run** — `uv run pytest tests/test_watchdog.py -v` → FAIL (no module)

- [ ] **Step 3: Implement `src/hermes/watchdog.py`**

```python
"""Liveness watchdog for hermes-mail.

Runs as its own launchd job every 30 minutes. Reads the heartbeats table
directly (no daemon code paths — a daemon bug must not break the watchdog),
alerts Telegram when the heartbeat is stale, and attempts one
`launchctl kickstart` of the mail daemon.
"""
from __future__ import annotations
import argparse
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from loguru import logger

MAIL_JOB = "system/com.beers.hermes-mail"


def check(
    conn: sqlite3.Connection,
    notify: Callable[[str], object],
    kickstart: Callable[[], object],
    max_age_minutes: int = 60,
) -> bool:
    """Return True when healthy. On stale/missing heartbeat: notify + kickstart."""
    row = conn.execute(
        "SELECT ts FROM heartbeats WHERE component='hermes-mail'"
    ).fetchone()
    now = datetime.now(timezone.utc)
    if row:
        last = datetime.fromisoformat(row[0])
        if now - last <= timedelta(minutes=max_age_minutes):
            return True
        msg = f"hermes-mail heartbeat stale: last beat {row[0]} (>{max_age_minutes}m ago). Kickstarting."
    else:
        msg = "hermes-mail has no heartbeat row — daemon likely never started. Kickstarting."
    logger.warning(msg)
    try:
        notify(msg)
    except Exception as e:  # Telegram down must not stop the kickstart
        logger.error("watchdog notify failed: {}", e)
    try:
        kickstart()
    except Exception as e:
        logger.error("watchdog kickstart failed: {}", e)
    return False


def _kickstart() -> None:
    subprocess.run(["launchctl", "kickstart", "-k", MAIL_JOB], check=False, timeout=30)


def _notify(text: str) -> None:
    from hermes.config import load_config
    from hermes.telegram.client import TelegramClient
    cfg = load_config()
    TelegramClient(cfg.telegram).send_message(text)


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-watchdog")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--max-age-minutes", type=int, default=60)
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    healthy = check(conn, notify=_notify, kickstart=_kickstart, max_age_minutes=args.max_age_minutes)
    return 0 if healthy else 1
```

Note: verify `TelegramClient` construction/signature against
`src/hermes/telegram/client.py:13` while implementing — match the real API
(it may be `send_message(chat_id, text)` or take config differently). Same
for `load_config().telegram`. Adjust `_notify` accordingly; `check()` is the
tested unit and stays as written.

- [ ] **Step 4: Add entrypoint** in `pyproject.toml` `[project.scripts]`: `hermes-watchdog = "hermes.watchdog:main"`, then `uv sync`.
- [ ] **Step 5: Run** — `uv run pytest tests/test_watchdog.py -v` → PASS; `uv run pytest` (full suite, with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` if PDF tests matter) → no regressions.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(watchdog): hermes-watchdog — stale-heartbeat alert + kickstart"`

### Task 5: LaunchDaemon plists (boot-survival) + watchdog plist

**Files:**
- Create: `ops/launchd/com.beers.hermes-watchdog.plist`
- Modify: `ops/install.sh` (install into `/Library/LaunchDaemons` with `UserName`)

- [ ] **Step 1: Watchdog plist** (`ops/launchd/com.beers.hermes-watchdog.plist`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.beers.hermes-watchdog</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/macmini/Projects/hermes/.venv/bin/hermes-watchdog</string>
  </array>
  <key>UserName</key>          <string>macmini</string>
  <key>WorkingDirectory</key>  <string>/Users/macmini/Projects/hermes</string>
  <key>RunAtLoad</key>         <true/>
  <key>StartInterval</key>     <integer>1800</integer>
  <key>StandardOutPath</key>   <string>/Users/macmini/.hermes/logs/watchdog.log</string>
  <key>StandardErrorPath</key> <string>/Users/macmini/.hermes/logs/watchdog.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key><string>/Users/macmini</string>
  </dict>
</dict>
</plist>
```

- [ ] **Step 2: Add `UserName`/`HOME` to every existing hermes plist** in `ops/launchd/` (same two keys as above; LaunchDaemons run as root without `UserName`). One-line sed or manual — verify each file afterward with `plutil -lint ops/launchd/*.plist`.

- [ ] **Step 3: Update `ops/install.sh`** so the launchd section installs to the system domain (replacing the LaunchAgents copy):

```bash
# System LaunchDaemons: survive reboot without GUI login (the May/June outage).
sudo cp ops/launchd/com.beers.hermes-*.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/com.beers.hermes-*.plist
sudo chmod 644 /Library/LaunchDaemons/com.beers.hermes-*.plist
for f in /Library/LaunchDaemons/com.beers.hermes-*.plist; do
  sudo launchctl bootout system "$f" 2>/dev/null || true
  sudo launchctl bootstrap system "$f"
done
# Remove the old per-user agents so jobs don't double-run after a GUI login.
rm -f ~/Library/LaunchAgents/com.beers.hermes-*.plist
```

- [ ] **Step 4: Commit** — `git add -A && git commit -m "ops: run hermes jobs as system LaunchDaemons; add watchdog job"`

### Task 6: Deploy to ghengis + live verification

**Files:** none local — ops on `macmini@100.116.82.14` (repo at `/Users/macmini/Projects/hermes`).

- [ ] **Step 1: Push/pull.** Local: `git push` (or `git bundle` over scp if no shared remote — check `git remote -v` first; ghengis repo origin may be the laptop). On ghengis: `cd ~/Projects/hermes && git pull && uv sync`.
- [ ] **Step 2: Flip junk policy.** On ghengis edit `~/.config/hermes/config.toml`: `delete_promos = false`.
- [ ] **Step 3: Run migration + smoke test.** `uv run hermes-mail --once --dry-run` → exits 0, log shows imap connect, no bytes errors. DB now at user_version 9.
- [ ] **Step 4: Install daemons.** Run the new install.sh launchd section (or the commands by hand). Verify: `sudo launchctl print system/com.beers.hermes-mail | head` shows `state = running`; `tail -f ~/.hermes/logs/mail.log` shows IDLE cycles with **no** `bytes-like` warnings.
- [ ] **Step 5: Verify heartbeat + watchdog.** `sqlite3 ~/.hermes/state.sqlite "SELECT * FROM heartbeats"` → fresh ts. Force-run watchdog once: `sudo launchctl kickstart system/com.beers.hermes-watchdog`; watchdog.log shows healthy exit. Then negative test: `sudo launchctl bootout system/com.beers.hermes-mail`, run watchdog by hand with `--max-age-minutes 0` → Telegram alert arrives; re-bootstrap mail daemon.
- [ ] **Step 6: End-to-end Gmail check.** From the laptop, via Gmail MCP: confirm a fresh promo (e.g. next TLDR) gets `Hermes/Promo` label and leaves inbox (archived, NOT in trash). Confirm digest/tg jobs are loaded.
- [ ] **Step 7: Commit any deploy fixups; update memory** (`MEMORY.md` hermes entry: revived, daemons in system domain, watchdog live).

## Self-review notes

- Spec 0.1 (diagnose/restart) → root-cause noted in header + Task 5/6 fix install; 0.2 bugs → Tasks 1–2; 0.3 watchdog → Tasks 3–5; 0.4 junk policy → Task 6 Step 2 (config, not code — spec assumed code change; config flag already existed).
- Watchdog Telegram wiring flagged as verify-against-real-API at implementation time (signature unknown until read) — intentional, the tested unit (`check`) is fully specified.
- Backfill of the 3-week mess is Phase 0-adjacent but listed in spec under deploy-day; the daemon's `_bootstrap_anchor` may only process new UIDs — if so, backfill ships with Phase 1/2 work as `hermes-backfill`. Out of scope here.
