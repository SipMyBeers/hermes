# Hermes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Hermes — a self-hosted email triage and invoicing agent on ghengis — and ship the May Boarhog invoice today. Phase A produces a working CLI invoice sender; Phase B–E adds the IMAP daemon, Telegram bot, morning digest, and launchd cron.

**Architecture:** Three small Python units (mail daemon, Telegram bot, invoice/digest jobs) sharing one SQLite state file and one secrets file. Tier-1 rule classifier handles ~90% of mail, tier-2 Ollama LLM only for ambiguous senders and drafting. SMTP via Gmail with `Reply-To: dylan@ranger-beers.com` to dodge DMARC alignment issues. All cron via launchd on ghengis.

**Tech Stack:** Python 3.12 (uv-managed venv), `imap_tools` (IMAP IDLE), stdlib `smtplib` (SMTP), `httpx` (Ollama + Telegram HTTP), `jinja2` + `weasyprint` (PDF), `pytest` (tests), stdlib `sqlite3` + `tomllib`, `loguru` (logs).

---

## File Structure

```
~/Projects/hermes/
├── pyproject.toml               # uv project, deps pinned
├── clients.toml                 # client billing config (committed, no secrets)
├── README.md
├── .gitignore                   # .venv, *.sqlite, secrets.toml, .pdf
├── docs/
│   ├── specs/2026-05-02-hermes-design.md
│   └── plans/2026-05-02-hermes-implementation.md
├── src/hermes/
│   ├── __init__.py
│   ├── config.py                # load secrets.toml + clients.toml
│   ├── db.py                    # SQLite connect + migrations
│   ├── models.py                # dataclasses for Message, Client, Outbox, Ledger
│   ├── classifier/
│   │   ├── __init__.py
│   │   ├── rules.py             # tier-1 deterministic classifier
│   │   ├── llm.py               # tier-2 Ollama classifier
│   │   └── drafter.py           # gemma4 reply-draft generator
│   ├── mail/
│   │   ├── __init__.py
│   │   ├── imap_client.py       # IMAP connect, fetch, label, idle
│   │   ├── smtp_client.py       # SMTP send wrapper (the only send() in the system)
│   │   └── daemon.py            # IMAP IDLE → classify → label → notify loop
│   ├── telegram/
│   │   ├── __init__.py
│   │   ├── client.py            # Telegram HTTP wrapper
│   │   ├── commands.py          # /digest /approve /reject /invoice etc.
│   │   └── daemon.py            # poll loop
│   ├── invoice/
│   │   ├── __init__.py
│   │   ├── render.py            # Jinja2 → HTML → WeasyPrint → PDF
│   │   ├── job.py               # monthly cron entry point
│   │   ├── cli.py               # one-shot CLI for Phase A
│   │   └── templates/standard.html
│   └── digest/
│       ├── __init__.py
│       ├── build.py             # read SQLite, compose markdown report
│       └── job.py               # cron entry point
├── tests/
│   ├── conftest.py              # fixtures: in-memory db, mock SMTP, mock Telegram, mock Ollama
│   ├── fixtures/mbox/
│   │   ├── promo_amazon.eml
│   │   ├── promo_newsletter.eml
│   │   ├── client_boarhog.eml
│   │   ├── personal.eml
│   │   ├── transactional_receipt.eml
│   │   └── ambiguous.eml
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_classifier_rules.py
│   ├── test_classifier_llm.py
│   ├── test_drafter.py
│   ├── test_smtp_send.py
│   ├── test_imap_client.py
│   ├── test_telegram_client.py
│   ├── test_telegram_commands.py
│   ├── test_invoice_render.py
│   └── test_digest_build.py
└── ops/
    ├── launchd/
    │   ├── com.beers.hermes-mail.plist
    │   ├── com.beers.hermes-tg.plist
    │   ├── com.beers.hermes-digest.plist
    │   ├── com.beers.hermes-digest-evening.plist
    │   └── com.beers.hermes-invoice.plist
    └── install.sh
```

---

## Phase A — Ship the May Boarhog invoice today (Tasks 1–7)

### Task 1: Project skeleton

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `clients.toml`, `README.md`, `src/hermes/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1: Verify uv is installed locally; if not, install**

```bash
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```
Expected: prints `uv 0.x.x` (any 0.x).

- [ ] **Step 2: Initialize project**

```bash
cd /Users/beers/Projects/active/hermes
uv init --no-readme --python 3.12
rm -f hello.py
mkdir -p src/hermes tests/fixtures/mbox ops/launchd
touch src/hermes/__init__.py tests/__init__.py
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "hermes"
version = "0.1.0"
description = "Email triage and invoicing agent for ghengis"
requires-python = ">=3.12"
dependencies = [
    "imap-tools>=1.6",
    "httpx>=0.27",
    "jinja2>=3.1",
    "weasyprint>=62",
    "loguru>=0.7",
]

[project.scripts]
hermes-mail = "hermes.mail.daemon:main"
hermes-tg = "hermes.telegram.daemon:main"
hermes-invoice = "hermes.invoice.job:main"
hermes-invoice-cli = "hermes.invoice.cli:main"
hermes-digest = "hermes.digest.job:main"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-mock>=3",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --tb=short"

[tool.ruff]
line-length = 100

[tool.uv]
package = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 4: Write `.gitignore`**

```gitignore
.venv/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
*.sqlite
*.sqlite-journal
**/secrets.toml
.hermes/
*.pdf
.DS_Store
```

- [ ] **Step 5: Write empty `clients.toml` skeleton**

```toml
# Hermes client billing config. Secrets live in ~/.config/hermes/secrets.toml.
# Add a section per client:
#
# [client_key]
# domain = "example.com"
# contact_email = "billing@example.com"
# billing_amount_cents = 50000
# billing_terms = "Net 30"
# service_description = "Hosting fees"
# active = true
```

- [ ] **Step 6: Write `README.md`**

```markdown
# Hermes

Email triage + invoicing agent for ghengis. See `docs/specs/` for design,
`docs/plans/` for implementation.

## Quick start (after install on ghengis)

    hermes-invoice-cli --client boarhog --month 2026-05 --send
    hermes-mail --dry-run --since 24h
    hermes-tg            # foreground bot
    hermes-digest        # one-shot morning report
```

- [ ] **Step 7: Install deps and verify**

```bash
uv sync
uv run python -c "import hermes; print('ok')"
uv run pytest
```
Expected: `uv sync` resolves, `import` prints `ok`, pytest reports `no tests ran`.

- [ ] **Step 8: Commit**

```bash
git add .
git commit -m "feat: project skeleton (uv, pyproject.toml, layout)"
```

---

### Task 2: Config loader

**Files:**
- Create: `src/hermes/config.py`, `tests/test_config.py`, `tests/fixtures/secrets.example.toml`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
from pathlib import Path
import tomllib
import pytest
from hermes.config import load_config, Config, GmailConfig, TelegramConfig, ClientConfig


def test_load_config_full(tmp_path: Path):
    secrets = tmp_path / "secrets.toml"
    secrets.write_text("""
[gmail]
imap_user = "dzbeers747@gmail.com"
imap_app_password = "abcd efgh ijkl mnop"
smtp_user = "dzbeers747@gmail.com"
smtp_app_password = "abcd efgh ijkl mnop"
from_address = "Dylan Beers <dzbeers747@gmail.com>"
reply_to = "Dylan Beers <dylan@ranger-beers.com>"

[telegram]
bot_token = "123:ABC"
chat_id = 42

[digest]
evening_report = false
""")
    clients = tmp_path / "clients.toml"
    clients.write_text("""
[boarhog]
domain = "boarhog.example"
contact_email = "billing@boarhog.example"
billing_amount_cents = 50000
billing_terms = "Net 30"
service_description = "Hosting fees"
active = true
""")
    cfg = load_config(secrets_path=secrets, clients_path=clients)
    assert isinstance(cfg, Config)
    assert cfg.gmail.imap_user == "dzbeers747@gmail.com"
    assert cfg.gmail.from_address.endswith("@gmail.com>")
    assert cfg.gmail.reply_to.endswith("@ranger-beers.com>")
    assert cfg.telegram.bot_token == "123:ABC"
    assert cfg.telegram.chat_id == 42
    assert cfg.digest_evening_report is False
    assert "boarhog" in cfg.clients
    assert cfg.clients["boarhog"].billing_amount_cents == 50000


def test_load_config_missing_secrets(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(secrets_path=tmp_path / "missing.toml", clients_path=tmp_path / "missing2.toml")


def test_load_config_no_clients(tmp_path: Path):
    secrets = tmp_path / "secrets.toml"
    secrets.write_text("""
[gmail]
imap_user = "u@g.com"
imap_app_password = "x"
smtp_user = "u@g.com"
smtp_app_password = "x"
from_address = "U <u@g.com>"
reply_to = "U <u@g.com>"

[telegram]
bot_token = "1:A"
chat_id = 0
""")
    clients = tmp_path / "clients.toml"
    clients.write_text("# empty")
    cfg = load_config(secrets_path=secrets, clients_path=clients)
    assert cfg.clients == {}
```

- [ ] **Step 2: Run, expect FAIL (no module)**

```bash
uv run pytest tests/test_config.py -v
```
Expected: ImportError on `hermes.config`.

- [ ] **Step 3: Implement `src/hermes/config.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class GmailConfig:
    imap_user: str
    imap_app_password: str
    smtp_user: str
    smtp_app_password: str
    from_address: str
    reply_to: str


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: int


@dataclass(frozen=True)
class ClientConfig:
    name: str
    domain: str
    contact_email: str
    billing_amount_cents: int
    billing_terms: str
    service_description: str
    active: bool


@dataclass(frozen=True)
class Config:
    gmail: GmailConfig
    telegram: TelegramConfig
    digest_evening_report: bool
    clients: dict[str, ClientConfig] = field(default_factory=dict)


def _default_secrets_path() -> Path:
    return Path.home() / ".config" / "hermes" / "secrets.toml"


def _default_clients_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "clients.toml"


def load_config(
    secrets_path: Path | None = None,
    clients_path: Path | None = None,
) -> Config:
    secrets_path = secrets_path or _default_secrets_path()
    clients_path = clients_path or _default_clients_path()
    if not secrets_path.exists():
        raise FileNotFoundError(f"secrets file not found: {secrets_path}")
    if not clients_path.exists():
        raise FileNotFoundError(f"clients file not found: {clients_path}")

    with secrets_path.open("rb") as f:
        s = tomllib.load(f)
    with clients_path.open("rb") as f:
        c = tomllib.load(f)

    gmail = GmailConfig(**s["gmail"])
    telegram = TelegramConfig(
        bot_token=s["telegram"]["bot_token"],
        chat_id=int(s["telegram"]["chat_id"]),
    )
    digest_evening = bool(s.get("digest", {}).get("evening_report", False))

    clients: dict[str, ClientConfig] = {}
    for key, body in c.items():
        clients[key] = ClientConfig(
            name=key,
            domain=body["domain"],
            contact_email=body["contact_email"],
            billing_amount_cents=int(body["billing_amount_cents"]),
            billing_terms=body["billing_terms"],
            service_description=body["service_description"],
            active=bool(body["active"]),
        )
    return Config(gmail=gmail, telegram=telegram, digest_evening_report=digest_evening, clients=clients)
```

- [ ] **Step 4: Run, expect PASS**

```bash
uv run pytest tests/test_config.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hermes/config.py tests/test_config.py
git commit -m "feat(config): typed loader for secrets.toml + clients.toml"
```

---

### Task 3: Database module

**Files:**
- Create: `src/hermes/db.py`, `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

`tests/test_db.py`:
```python
from pathlib import Path
import sqlite3
from hermes.db import connect, migrate, SCHEMA_VERSION


def test_connect_creates_file(tmp_path: Path):
    db_path = tmp_path / "test.sqlite"
    conn = connect(db_path)
    assert db_path.exists()
    conn.close()


def test_migrate_idempotent(tmp_path: Path):
    db_path = tmp_path / "test.sqlite"
    conn = connect(db_path)
    migrate(conn)
    migrate(conn)  # second call must not raise
    cur = conn.execute("PRAGMA user_version")
    assert cur.fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_migrate_creates_tables(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"messages", "clients", "ledger", "outbox"}.issubset(tables)


def test_messages_uniqueness(tmp_path: Path):
    conn = connect(tmp_path / "t.sqlite")
    migrate(conn)
    conn.execute(
        "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
        "VALUES (1, 'mid-1', '2026-05-02T00:00:00', 'a@b.com', 'promo', 'rule')"
    )
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO messages(uid, message_id, received_at, sender, classification, classifier) "
            "VALUES (2, 'mid-1', '2026-05-02T00:00:00', 'a@b.com', 'promo', 'rule')"
        )
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_db.py -v
```

- [ ] **Step 3: Implement `src/hermes/db.py`**

```python
from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS messages (
  uid INTEGER PRIMARY KEY,
  message_id TEXT UNIQUE NOT NULL,
  received_at TEXT NOT NULL,
  sender TEXT NOT NULL,
  subject TEXT,
  classification TEXT NOT NULL,
  classifier TEXT NOT NULL,
  confidence REAL,
  label TEXT,
  draft_text TEXT,
  drafted_at TEXT,
  notified_at TEXT
);

CREATE TABLE IF NOT EXISTS clients (
  name TEXT PRIMARY KEY,
  domain TEXT,
  contact_email TEXT NOT NULL,
  billing_amount_cents INTEGER,
  billing_terms TEXT,
  service_description TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  direction TEXT NOT NULL CHECK (direction IN ('out','in')),
  client TEXT,
  vendor TEXT,
  invoice_number TEXT,
  month TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  status TEXT NOT NULL,
  pdf_path TEXT,
  message_id TEXT,
  sent_at TEXT,
  paid_at TEXT
);

CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('reply','invoice','adhoc')),
  related_uid INTEGER,
  client TEXT,
  to_addr TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  attachments TEXT,
  status TEXT NOT NULL CHECK (status IN ('queued','sent','failed')),
  last_error TEXT,
  created_at TEXT NOT NULL,
  sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_received ON messages(received_at);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status);
CREATE INDEX IF NOT EXISTS idx_ledger_month ON ledger(month, direction);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    conn.executescript(_SCHEMA_V1)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
```

- [ ] **Step 4: Run, expect PASS**

```bash
uv run pytest tests/test_db.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hermes/db.py tests/test_db.py
git commit -m "feat(db): SQLite schema + idempotent migrate"
```

---

### Task 4: SMTP send wrapper

**Files:**
- Create: `src/hermes/mail/__init__.py`, `src/hermes/mail/smtp_client.py`, `tests/test_smtp_send.py`

- [ ] **Step 1: Write the failing test**

`tests/test_smtp_send.py`:
```python
from email.message import EmailMessage
from unittest.mock import MagicMock, patch
from hermes.config import GmailConfig
from hermes.mail.smtp_client import SmtpSender, build_message


def _gmail() -> GmailConfig:
    return GmailConfig(
        imap_user="u@gmail.com",
        imap_app_password="p",
        smtp_user="u@gmail.com",
        smtp_app_password="p",
        from_address="U <u@gmail.com>",
        reply_to="U <u@ranger-beers.com>",
    )


def test_build_message_sets_headers():
    msg = build_message(
        gmail=_gmail(),
        to_addr="client@example.com",
        subject="Invoice",
        body_text="Hello",
        attachments=[],
    )
    assert msg["From"] == "U <u@gmail.com>"
    assert msg["Reply-To"] == "U <u@ranger-beers.com>"
    assert msg["To"] == "client@example.com"
    assert msg["Subject"] == "Invoice"
    assert msg.get_content().strip() == "Hello"


def test_build_message_attaches_pdf(tmp_path):
    pdf = tmp_path / "invoice.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    msg = build_message(
        gmail=_gmail(),
        to_addr="c@e.com",
        subject="s",
        body_text="b",
        attachments=[pdf],
    )
    parts = list(msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_filename() == "invoice.pdf"
    assert parts[0].get_content_type() == "application/pdf"


def test_smtp_sender_send_calls_smtp(tmp_path):
    sender = SmtpSender(_gmail())
    fake_smtp = MagicMock()
    msg = build_message(_gmail(), "c@e.com", "s", "b", [])
    with patch("hermes.mail.smtp_client.smtplib.SMTP") as smtp_cls:
        smtp_cls.return_value.__enter__.return_value = fake_smtp
        sender.send(msg)
    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once_with("u@gmail.com", "p")
    fake_smtp.send_message.assert_called_once_with(msg)
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_smtp_send.py -v
```

- [ ] **Step 3: Implement `src/hermes/mail/__init__.py`** (empty file)

```bash
touch src/hermes/mail/__init__.py
```

- [ ] **Step 4: Implement `src/hermes/mail/smtp_client.py`**

```python
from __future__ import annotations
from email.message import EmailMessage
from pathlib import Path
import mimetypes
import smtplib
from loguru import logger
from hermes.config import GmailConfig

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def build_message(
    gmail: GmailConfig,
    to_addr: str,
    subject: str,
    body_text: str,
    attachments: list[Path],
    body_html: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = gmail.from_address
    msg["Reply-To"] = gmail.reply_to
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    for path in attachments:
        ctype, _ = mimetypes.guess_type(str(path))
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(
            path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )
    return msg


class SmtpSender:
    def __init__(self, gmail: GmailConfig):
        self.gmail = gmail

    def send(self, message: EmailMessage) -> str:
        """Send and return Message-ID. Raises smtplib exceptions on failure."""
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(self.gmail.smtp_user, self.gmail.smtp_app_password)
            smtp.send_message(message)
        logger.info("smtp.send to={} subject={!r}", message["To"], message["Subject"])
        return str(message.get("Message-ID") or "")
```

- [ ] **Step 5: Run, expect PASS**

```bash
uv run pytest tests/test_smtp_send.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/hermes/mail/__init__.py src/hermes/mail/smtp_client.py tests/test_smtp_send.py
git commit -m "feat(mail): SMTP send wrapper with attachment support"
```

---

### Task 5: Invoice template + Jinja2 render

**Files:**
- Create: `src/hermes/invoice/__init__.py`, `src/hermes/invoice/render.py`, `src/hermes/invoice/templates/standard.html`, `tests/test_invoice_render.py`

- [ ] **Step 1: Write the failing test**

`tests/test_invoice_render.py`:
```python
from datetime import date
from hermes.invoice.render import render_html, render_pdf, InvoiceContext


def _ctx() -> InvoiceContext:
    return InvoiceContext(
        invoice_number="INV-2026-05-001",
        issue_date=date(2026, 5, 2),
        due_date=date(2026, 6, 1),
        client_name="Boarhog LLC",
        client_email="billing@boarhog.example",
        from_name="Dylan Beers",
        from_email="dylan@ranger-beers.com",
        line_items=[("Hosting fees — May 2026", 50000)],
        currency="USD",
        terms="Net 30",
        notes="Thank you for your business.",
    )


def test_render_html_contains_amount():
    html = render_html(_ctx())
    assert "Boarhog LLC" in html
    assert "INV-2026-05-001" in html
    assert "Hosting fees" in html
    assert "$500.00" in html  # 50000 cents formatted
    assert "Net 30" in html


def test_render_html_total_sums_lines(tmp_path):
    ctx = _ctx()
    ctx.line_items.append(("Setup", 10000))
    html = render_html(ctx)
    assert "$600.00" in html  # total


def test_render_pdf_writes_file(tmp_path):
    out = tmp_path / "invoice.pdf"
    render_pdf(_ctx(), out)
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_invoice_render.py -v
```

- [ ] **Step 3: Implement `src/hermes/invoice/__init__.py`** (empty)

```bash
mkdir -p src/hermes/invoice/templates
touch src/hermes/invoice/__init__.py
```

- [ ] **Step 4: Implement `src/hermes/invoice/templates/standard.html`**

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Invoice {{ invoice_number }}</title>
  <style>
    body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; color: #222; padding: 48px; }
    h1 { font-size: 28px; margin: 0 0 4px 0; }
    .muted { color: #777; font-size: 12px; }
    .section { margin: 24px 0; }
    .grid { display: flex; justify-content: space-between; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { text-align: left; padding: 8px 0; border-bottom: 1px solid #eee; }
    th { font-size: 11px; text-transform: uppercase; color: #999; letter-spacing: 1px; }
    .total-row td { border-top: 2px solid #222; border-bottom: none; font-weight: bold; padding-top: 12px; }
    .right { text-align: right; }
    .notes { font-size: 12px; color: #555; }
  </style>
</head>
<body>
  <h1>Invoice</h1>
  <div class="muted">{{ invoice_number }}</div>

  <div class="section grid">
    <div>
      <div class="muted">From</div>
      <div>{{ from_name }}</div>
      <div>{{ from_email }}</div>
    </div>
    <div>
      <div class="muted">Bill to</div>
      <div>{{ client_name }}</div>
      <div>{{ client_email }}</div>
    </div>
    <div>
      <div class="muted">Issued</div>
      <div>{{ issue_date.strftime('%Y-%m-%d') }}</div>
      <div class="muted" style="margin-top: 8px;">Due</div>
      <div>{{ due_date.strftime('%Y-%m-%d') }}</div>
    </div>
  </div>

  <div class="section">
    <table>
      <thead>
        <tr><th>Description</th><th class="right">Amount</th></tr>
      </thead>
      <tbody>
        {% for desc, cents in line_items %}
        <tr>
          <td>{{ desc }}</td>
          <td class="right">${{ "%.2f"|format(cents / 100) }}</td>
        </tr>
        {% endfor %}
        <tr class="total-row">
          <td>Total {{ currency }}</td>
          <td class="right">${{ "%.2f"|format(total_cents / 100) }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="section notes">
    <div><strong>Terms:</strong> {{ terms }}</div>
    {% if notes %}<div style="margin-top: 8px;">{{ notes }}</div>{% endif %}
  </div>
</body>
</html>
```

- [ ] **Step 5: Implement `src/hermes/invoice/render.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


@dataclass
class InvoiceContext:
    invoice_number: str
    issue_date: date
    due_date: date
    client_name: str
    client_email: str
    from_name: str
    from_email: str
    line_items: list[tuple[str, int]]  # (description, amount_cents)
    currency: str = "USD"
    terms: str = "Net 30"
    notes: str = ""

    @property
    def total_cents(self) -> int:
        return sum(c for _, c in self.line_items)


def render_html(ctx: InvoiceContext) -> str:
    tmpl = _env.get_template("standard.html")
    return tmpl.render(**ctx.__dict__, total_cents=ctx.total_cents)


def render_pdf(ctx: InvoiceContext, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(ctx)
    HTML(string=html).write_pdf(str(out_path))
    return out_path
```

- [ ] **Step 6: Run, expect PASS**

```bash
uv run pytest tests/test_invoice_render.py -v
```
Expected: 3 passed. (WeasyPrint may emit a font warning — ignore.)

- [ ] **Step 7: Commit**

```bash
git add src/hermes/invoice tests/test_invoice_render.py
git commit -m "feat(invoice): Jinja2 template + WeasyPrint PDF render"
```

---

### Task 6: Invoice CLI (the May-invoice tool)

**Files:**
- Create: `src/hermes/invoice/cli.py`, `tests/test_invoice_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/test_invoice_cli.py`:
```python
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
from hermes.config import Config, GmailConfig, TelegramConfig, ClientConfig
from hermes.invoice.cli import build_invoice_for_client, _next_invoice_number


def _config(tmp_path: Path) -> Config:
    return Config(
        gmail=GmailConfig(
            imap_user="u@g.com", imap_app_password="p",
            smtp_user="u@g.com", smtp_app_password="p",
            from_address="Dylan Beers <u@g.com>",
            reply_to="Dylan Beers <dylan@ranger-beers.com>",
        ),
        telegram=TelegramConfig(bot_token="1:A", chat_id=0),
        digest_evening_report=False,
        clients={
            "boarhog": ClientConfig(
                name="boarhog", domain="boarhog.example",
                contact_email="billing@boarhog.example",
                billing_amount_cents=50000,
                billing_terms="Net 30",
                service_description="Hosting fees",
                active=True,
            )
        },
    )


def test_build_invoice_for_client_uses_client_billing(tmp_path):
    cfg = _config(tmp_path)
    ctx, pdf_path = build_invoice_for_client(
        cfg, client_key="boarhog", month="2026-05", out_dir=tmp_path
    )
    assert ctx.client_name == "boarhog"
    assert ctx.client_email == "billing@boarhog.example"
    assert ctx.line_items == [("Hosting fees — May 2026", 50000)]
    assert ctx.terms == "Net 30"
    assert pdf_path.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF")


def test_next_invoice_number_unique(tmp_path):
    n1 = _next_invoice_number(tmp_path, "2026-05")
    (tmp_path / f"{n1}.pdf").touch()
    n2 = _next_invoice_number(tmp_path, "2026-05")
    assert n1 != n2
    assert n1.startswith("INV-2026-05-")
    assert n2.startswith("INV-2026-05-")
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_invoice_cli.py -v
```

- [ ] **Step 3: Implement `src/hermes/invoice/cli.py`**

```python
from __future__ import annotations
import argparse
from datetime import date, timedelta
from pathlib import Path
import calendar
from loguru import logger
from hermes.config import Config, load_config
from hermes.invoice.render import InvoiceContext, render_pdf
from hermes.mail.smtp_client import SmtpSender, build_message

_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _next_invoice_number(out_dir: Path, month: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = list(out_dir.glob(f"INV-{month}-*.pdf"))
    n = len(existing) + 1
    return f"INV-{month}-{n:03d}"


def build_invoice_for_client(
    cfg: Config, client_key: str, month: str, out_dir: Path
) -> tuple[InvoiceContext, Path]:
    if client_key not in cfg.clients:
        raise KeyError(f"client {client_key!r} not in clients.toml")
    client = cfg.clients[client_key]
    if not client.billing_amount_cents:
        raise ValueError(f"client {client_key} has no billing_amount_cents")
    year, mo = map(int, month.split("-"))
    issue = date(year, mo, 1)
    last_day = calendar.monthrange(year, mo)[1]
    due = (date(year, mo, last_day)
           if client.billing_terms.lower().startswith("due on receipt")
           else date(year, mo, last_day) + timedelta(days=30))
    invoice_number = _next_invoice_number(out_dir, month)
    line_label = f"{client.service_description} — {_MONTH_NAMES[mo]} {year}"
    ctx = InvoiceContext(
        invoice_number=invoice_number,
        issue_date=issue,
        due_date=due,
        client_name=client.name,
        client_email=client.contact_email,
        from_name=cfg.gmail.from_address.split(" <")[0].strip(),
        from_email=cfg.gmail.reply_to.split("<")[-1].rstrip(">").strip(),
        line_items=[(line_label, client.billing_amount_cents)],
        terms=client.billing_terms,
    )
    pdf_path = out_dir / f"{invoice_number}.pdf"
    render_pdf(ctx, pdf_path)
    return ctx, pdf_path


def _compose_email(ctx: InvoiceContext, cfg: Config) -> tuple[str, str]:
    subject = f"Invoice {ctx.invoice_number} — {ctx.client_name}"
    total_dollars = f"${ctx.total_cents / 100:,.2f}"
    body = (
        f"Hi,\n\n"
        f"Attached is invoice {ctx.invoice_number} for {total_dollars}, "
        f"due {ctx.due_date.strftime('%B %d, %Y')} ({ctx.terms}).\n\n"
        f"Reply to this email if anything looks off.\n\n"
        f"Thanks,\n"
        f"{ctx.from_name}"
    )
    return subject, body


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-invoice-cli")
    p.add_argument("--client", required=True, help="client key from clients.toml")
    p.add_argument("--month", required=True, help="YYYY-MM")
    p.add_argument("--send", action="store_true", help="actually send (omit for dry-run)")
    p.add_argument("--out-dir", default=str(Path.home() / ".hermes" / "invoices"))
    args = p.parse_args()

    cfg = load_config()
    year, mo = args.month.split("-")
    out_dir = Path(args.out_dir) / year / mo
    ctx, pdf_path = build_invoice_for_client(cfg, args.client, args.month, out_dir)
    subject, body = _compose_email(ctx, cfg)

    print(f"Invoice: {ctx.invoice_number}")
    print(f"Client:  {ctx.client_name} <{ctx.client_email}>")
    print(f"Amount:  ${ctx.total_cents / 100:,.2f}")
    print(f"Due:     {ctx.due_date}")
    print(f"PDF:     {pdf_path}")
    print(f"Subject: {subject}")
    print(f"--- Body ---\n{body}\n---")

    if not args.send:
        print("\nDry-run (use --send to actually email). PDF written above.")
        return 0

    msg = build_message(cfg.gmail, ctx.client_email, subject, body, [pdf_path])
    SmtpSender(cfg.gmail).send(msg)
    logger.info("invoice sent: {} to {}", ctx.invoice_number, ctx.client_email)
    print("\nSent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run, expect PASS**

```bash
uv run pytest tests/test_invoice_cli.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Verify CLI in dry-run mode (no real sends)**

```bash
mkdir -p ~/.config/hermes
cat > ~/.config/hermes/secrets.toml <<'EOF'
[gmail]
imap_user = "dummy@gmail.com"
imap_app_password = "xxxx xxxx xxxx xxxx"
smtp_user = "dummy@gmail.com"
smtp_app_password = "xxxx xxxx xxxx xxxx"
from_address = "Dylan Beers <dummy@gmail.com>"
reply_to = "Dylan Beers <dylan@ranger-beers.com>"

[telegram]
bot_token = "0:dummy"
chat_id = 0
EOF
chmod 600 ~/.config/hermes/secrets.toml

cat > clients.toml <<'EOF'
[boarhog]
domain = "boarhog.example"
contact_email = "billing@boarhog.example"
billing_amount_cents = 50000
billing_terms = "Net 30"
service_description = "Hosting fees"
active = true
EOF

uv run hermes-invoice-cli --client boarhog --month 2026-05
```
Expected: prints invoice details, writes PDF to `~/.hermes/invoices/2026/05/INV-2026-05-001.pdf`, exits 0. Open the PDF — confirm it renders.

- [ ] **Step 6: Commit**

```bash
git add src/hermes/invoice/cli.py tests/test_invoice_cli.py
git commit -m "feat(invoice): CLI for one-shot invoice render + send (Phase A)"
```

---

### Task 7: Deploy Phase A to ghengis and send Boarhog May invoice

**Files:** none (deployment task)

- [ ] **Step 1: Push project to ghengis**

```bash
ssh macmini@ghengis 'mkdir -p ~/Projects/hermes'
rsync -av --exclude '.venv' --exclude '__pycache__' --exclude '*.sqlite' \
      /Users/beers/Projects/active/hermes/ macmini@ghengis:~/Projects/hermes/
```

- [ ] **Step 2: Install uv on ghengis if missing**

```bash
ssh macmini@ghengis 'bash -lc "command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh; uv --version"'
```
Expected: prints uv version.

- [ ] **Step 3: Install WeasyPrint system deps on ghengis**

WeasyPrint needs `pango` and `cairo`. Homebrew is on ghengis (Ollama lives at `/opt/homebrew/`).

```bash
ssh macmini@ghengis 'bash -lc "brew list pango cairo gdk-pixbuf libffi 2>/dev/null || brew install pango cairo gdk-pixbuf libffi"'
```
Expected: all four installed (or already installed).

- [ ] **Step 4: Sync deps in remote venv**

```bash
ssh macmini@ghengis 'bash -lc "cd ~/Projects/hermes && uv sync"'
```
Expected: resolves and installs.

- [ ] **Step 5: Generate Gmail app password (USER ACTION, manual)**

Open <https://myaccount.google.com/apppasswords> in browser. Sign in with `dzbeers747@gmail.com`. Create app password named `hermes-ghengis`. Copy the 16-char value.

- [ ] **Step 6: Capture real Boarhog details (USER ACTION, manual)**

Confirm with user via Telegram or in this thread:
- `client_email` for Boarhog billing contact
- `billing_amount_cents` (e.g. `50000` for $500)
- `billing_terms` (default "Net 30")
- `service_description` (default "Hosting fees")
- `domain` (e.g. `boarhog.com`)

- [ ] **Step 7: Write real `secrets.toml` and `clients.toml` on ghengis**

```bash
ssh macmini@ghengis 'mkdir -p ~/.config/hermes && chmod 700 ~/.config/hermes'
ssh macmini@ghengis 'cat > ~/.config/hermes/secrets.toml' <<EOF
[gmail]
imap_user = "dzbeers747@gmail.com"
imap_app_password = "<APP_PASSWORD_FROM_STEP_5>"
smtp_user = "dzbeers747@gmail.com"
smtp_app_password = "<APP_PASSWORD_FROM_STEP_5>"
from_address = "Dylan Beers <dzbeers747@gmail.com>"
reply_to = "Dylan Beers <dylan@ranger-beers.com>"

[telegram]
bot_token = "<EXISTING_BOT_TOKEN>"
chat_id = 0
EOF
ssh macmini@ghengis 'chmod 600 ~/.config/hermes/secrets.toml'

ssh macmini@ghengis 'cat > ~/Projects/hermes/clients.toml' <<EOF
[boarhog]
domain = "<BOARHOG_DOMAIN>"
contact_email = "<BOARHOG_BILLING_EMAIL>"
billing_amount_cents = <AMOUNT_CENTS>
billing_terms = "Net 30"
service_description = "Hosting fees"
active = true
EOF
```

- [ ] **Step 8: Dry-run the May invoice**

```bash
ssh macmini@ghengis 'bash -lc "cd ~/Projects/hermes && uv run hermes-invoice-cli --client boarhog --month 2026-05"'
```
Expected: prints invoice metadata, PDF written to `~/.hermes/invoices/2026/05/INV-2026-05-001.pdf`. Copy back to local for visual review:

```bash
scp macmini@ghengis:~/.hermes/invoices/2026/05/INV-2026-05-001.pdf /tmp/boarhog-may-2026.pdf
open /tmp/boarhog-may-2026.pdf
```

- [ ] **Step 9: Send for real after user approves the PDF**

```bash
ssh macmini@ghengis 'bash -lc "cd ~/Projects/hermes && uv run hermes-invoice-cli --client boarhog --month 2026-05 --send"'
```
Expected: prints `Sent.`, mail lands in client inbox.

- [ ] **Step 10: Verify ledger** (will be wired in Task 22; for now, manually confirm the email appears in Gmail Sent and an SMTP success log line in stdout)

- [ ] **Step 11: Commit deployment-touched files (none in repo for this task; tag the milestone)**

```bash
cd /Users/beers/Projects/active/hermes
git tag phase-a-shipped
```

**Phase A is complete: May Boarhog invoice sent.**

---

## Phase B — Email triage daemon (Tasks 8–14)

### Task 8: Rule classifier (tier-1)

**Files:**
- Create: `src/hermes/classifier/__init__.py`, `src/hermes/classifier/rules.py`, `tests/fixtures/mbox/*.eml`, `tests/test_classifier_rules.py`

- [ ] **Step 1: Create fixtures**

`tests/fixtures/mbox/promo_amazon.eml`:
```
From: Amazon Deals <deals@amazon.com>
To: dzbeers747@gmail.com
Subject: 50% off select items today only
Date: Fri, 02 May 2026 08:00:00 +0000
List-Unsubscribe: <https://amazon.com/unsubscribe?token=abc>
List-Unsubscribe-Post: List-Unsubscribe=One-Click
Message-ID: <amzn-1@amazon.com>
Content-Type: text/plain

Shop today's deals.
```

`tests/fixtures/mbox/promo_newsletter.eml`:
```
From: TLDR Newsletter <newsletter@tldrnewsletter.com>
To: dzbeers747@gmail.com
Subject: TLDR for May 2
Date: Fri, 02 May 2026 09:00:00 +0000
List-Unsubscribe: <mailto:unsub@tldrnewsletter.com>
Message-ID: <tldr-1@tldrnewsletter.com>
Content-Type: text/plain

Today's tech news.
```

`tests/fixtures/mbox/client_boarhog.eml`:
```
From: Sam Hogarth <sam@boarhog.example>
To: dzbeers747@gmail.com
Subject: Quick question about hosting
Date: Fri, 02 May 2026 10:00:00 +0000
Message-ID: <bh-1@boarhog.example>
Content-Type: text/plain

Hey Dylan, quick one — what's the bandwidth ceiling on our plan?
```

`tests/fixtures/mbox/personal.eml`:
```
From: Mom <mom@example.com>
To: dzbeers747@gmail.com
Subject: Sunday dinner?
Date: Fri, 02 May 2026 11:00:00 +0000
Message-ID: <mom-1@example.com>
Content-Type: text/plain

You free Sunday?
```

`tests/fixtures/mbox/transactional_receipt.eml`:
```
From: Stripe <receipts@stripe.com>
To: dzbeers747@gmail.com
Subject: Your receipt from acme — order #1234
Date: Fri, 02 May 2026 12:00:00 +0000
Message-ID: <stripe-1@stripe.com>
Content-Type: text/plain

Receipt details.
```

`tests/fixtures/mbox/ambiguous.eml`:
```
From: john@unknownsender.io
To: dzbeers747@gmail.com
Subject: Following up
Date: Fri, 02 May 2026 13:00:00 +0000
Message-ID: <amb-1@unknownsender.io>
Content-Type: text/plain

Wanted to follow up on the proposal.
```

- [ ] **Step 2: Write the failing test**

`tests/test_classifier_rules.py`:
```python
from email import message_from_bytes
from pathlib import Path
import pytest
from hermes.classifier.rules import classify, RuleResult, ClientHint

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def _msg(name: str):
    return message_from_bytes((FIXTURES / name).read_bytes())


def test_promo_via_list_unsubscribe():
    r = classify(_msg("promo_amazon.eml"), client_domains={}, personal_allowlist=set())
    assert r.label == "promo"
    assert r.confidence == 1.0
    assert "List-Unsubscribe" in r.reason


def test_promo_newsletter_also_caught():
    r = classify(_msg("promo_newsletter.eml"), client_domains={}, personal_allowlist=set())
    assert r.label == "promo"


def test_client_via_domain_allowlist():
    hints = {"boarhog.example": ClientHint(client="boarhog")}
    r = classify(_msg("client_boarhog.eml"), client_domains=hints, personal_allowlist=set())
    assert r.label == "client"
    assert r.client == "boarhog"


def test_personal_via_allowlist():
    r = classify(_msg("personal.eml"), client_domains={}, personal_allowlist={"mom@example.com"})
    assert r.label == "personal"


def test_transactional_via_subject():
    r = classify(_msg("transactional_receipt.eml"), client_domains={}, personal_allowlist=set())
    assert r.label == "transactional"


def test_ambiguous_returns_unknown():
    r = classify(_msg("ambiguous.eml"), client_domains={}, personal_allowlist=set())
    assert r.label == "unknown"
    assert r.confidence == 0.0
```

- [ ] **Step 3: Run, expect FAIL**

```bash
uv run pytest tests/test_classifier_rules.py -v
```

- [ ] **Step 4: Implement `src/hermes/classifier/__init__.py`** (empty)

```bash
mkdir -p src/hermes/classifier
touch src/hermes/classifier/__init__.py
```

- [ ] **Step 5: Implement `src/hermes/classifier/rules.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from email.message import Message
from email.utils import getaddresses
import re

_TX_SUBJECT_PATTERNS = [
    r"\breceipt\b",
    r"\border\s*#",
    r"\binvoice\s+(received|paid|#)",
    r"\bpayment\s+confirmation\b",
    r"\byour\s+(order|receipt|payment)\b",
]
_TX_RE = re.compile("|".join(_TX_SUBJECT_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class ClientHint:
    client: str   # client_key from clients.toml


@dataclass(frozen=True)
class RuleResult:
    label: str    # promo|client|personal|transactional|unknown
    confidence: float
    reason: str
    client: str | None = None


def _sender(msg: Message) -> tuple[str, str]:
    """Return (display_name, email_addr) lowercased."""
    addrs = getaddresses([msg.get("From", "")])
    if not addrs:
        return ("", "")
    name, addr = addrs[0]
    return (name, addr.lower())


def _domain_of(addr: str) -> str:
    return addr.split("@", 1)[1] if "@" in addr else ""


def classify(
    msg: Message,
    client_domains: dict[str, ClientHint],
    personal_allowlist: set[str],
) -> RuleResult:
    _, sender_addr = _sender(msg)

    if msg.get("List-Unsubscribe"):
        return RuleResult(label="promo", confidence=1.0, reason="List-Unsubscribe header present")

    if sender_addr in {a.lower() for a in personal_allowlist}:
        return RuleResult(label="personal", confidence=1.0, reason="sender in personal_allowlist")

    sender_domain = _domain_of(sender_addr)
    if sender_domain in client_domains:
        hint = client_domains[sender_domain]
        return RuleResult(
            label="client",
            confidence=1.0,
            reason=f"sender domain matches client {hint.client}",
            client=hint.client,
        )

    subject = msg.get("Subject", "") or ""
    if _TX_RE.search(subject):
        return RuleResult(label="transactional", confidence=0.9, reason=f"subject pattern: {subject!r}")

    return RuleResult(label="unknown", confidence=0.0, reason="no rule matched")
```

- [ ] **Step 6: Run, expect PASS**

```bash
uv run pytest tests/test_classifier_rules.py -v
```
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add src/hermes/classifier tests/test_classifier_rules.py tests/fixtures/mbox
git commit -m "feat(classifier): tier-1 rule classifier (promo/client/personal/transactional)"
```

---

### Task 9: LLM classifier (tier-2)

**Files:**
- Create: `src/hermes/classifier/llm.py`, `tests/test_classifier_llm.py`

- [ ] **Step 1: Write the failing test**

`tests/test_classifier_llm.py`:
```python
from email import message_from_bytes
from pathlib import Path
from unittest.mock import patch, MagicMock
from hermes.classifier.llm import classify_llm, LLMResult

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def _msg(name: str):
    return message_from_bytes((FIXTURES / name).read_bytes())


def test_classify_llm_parses_response():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "model": "qwen3:4b",
        "response": '{"label": "client", "confidence": 0.82, "why": "asks question about service"}',
    }
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.classifier.llm.httpx.post", return_value=fake_resp) as p:
        r = classify_llm(_msg("ambiguous.eml"), model="qwen3:4b", host="http://x")
    assert isinstance(r, LLMResult)
    assert r.label == "client"
    assert 0.81 < r.confidence < 0.83
    p.assert_called_once()


def test_classify_llm_falls_back_on_unparseable():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": "not json at all"}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.classifier.llm.httpx.post", return_value=fake_resp):
        r = classify_llm(_msg("ambiguous.eml"), model="qwen3:4b", host="http://x")
    assert r.label == "unknown"
    assert r.confidence == 0.0


def test_classify_llm_clamps_label():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": '{"label": "spam", "confidence": 0.9}'}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.classifier.llm.httpx.post", return_value=fake_resp):
        r = classify_llm(_msg("ambiguous.eml"), model="qwen3:4b", host="http://x")
    assert r.label == "other"  # unknown -> other
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_classifier_llm.py -v
```

- [ ] **Step 3: Implement `src/hermes/classifier/llm.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from email.message import Message
import json
import httpx
from loguru import logger

ALLOWED_LABELS = {"promo", "client", "personal", "transactional", "other"}

_PROMPT = """You are classifying an email into ONE label. Respond ONLY with JSON of the form:
{{"label": "<label>", "confidence": <0..1>, "why": "<short reason>"}}

Allowed labels (use exactly one):
- promo         : marketing, sales, newsletters, deals
- client        : a paying or prospective business client of mine asking about services
- personal     : friends, family, personal correspondence
- transactional : receipts, order confirmations, payment notifications
- other         : doesn't fit any above

Email:
From: {sender}
Subject: {subject}
Body (first 1500 chars):
{body}
"""


@dataclass(frozen=True)
class LLMResult:
    label: str
    confidence: float
    why: str


def _body_text(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def classify_llm(
    msg: Message,
    model: str = "qwen3:4b",
    host: str = "http://localhost:11434",
    timeout: float = 30.0,
) -> LLMResult:
    prompt = _PROMPT.format(
        sender=msg.get("From", ""),
        subject=msg.get("Subject", ""),
        body=_body_text(msg)[:1500],
    )
    payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
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
        why = str(data.get("why", ""))
        return LLMResult(label=label, confidence=confidence, why=why)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("llm response unparseable: {!r} -> {}", raw[:200], e)
        return LLMResult(label="unknown", confidence=0.0, why="unparseable response")
```

- [ ] **Step 4: Run, expect PASS**

```bash
uv run pytest tests/test_classifier_llm.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hermes/classifier/llm.py tests/test_classifier_llm.py
git commit -m "feat(classifier): tier-2 Ollama LLM classifier with JSON parsing"
```

---

### Task 10: Drafter (gemma4 reply generator)

**Files:**
- Create: `src/hermes/classifier/drafter.py`, `tests/test_drafter.py`

- [ ] **Step 1: Write the failing test**

`tests/test_drafter.py`:
```python
from email import message_from_bytes
from pathlib import Path
from unittest.mock import patch, MagicMock
from hermes.classifier.drafter import draft_reply

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def test_draft_reply_returns_text():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"response": "Hi Sam, thanks for the question..."}
    fake_resp.raise_for_status = MagicMock()
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    with patch("hermes.classifier.drafter.httpx.post", return_value=fake_resp):
        text = draft_reply(msg, sender_name="Dylan", model="gemma4", host="http://x")
    assert "Hi Sam" in text


def test_draft_reply_handles_failure():
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    with patch("hermes.classifier.drafter.httpx.post", side_effect=Exception("boom")):
        text = draft_reply(msg, sender_name="Dylan", model="gemma4", host="http://x")
    assert text == ""
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_drafter.py -v
```

- [ ] **Step 3: Implement `src/hermes/classifier/drafter.py`**

```python
from __future__ import annotations
from email.message import Message
import httpx
from loguru import logger
from hermes.classifier.llm import _body_text

_PROMPT = """You are drafting a brief, professional reply on behalf of {sender_name}.
- Two short paragraphs maximum.
- Tone: friendly, direct, no fluff.
- Do NOT make up facts or commitments. If the answer requires info you don't have, ask one clarifying question.
- End with "{sender_name}".

Original email:
From: {from_}
Subject: {subject}
Body:
{body}

Reply:
"""


def draft_reply(
    msg: Message,
    sender_name: str,
    model: str = "gemma4",
    host: str = "http://localhost:11434",
    timeout: float = 60.0,
) -> str:
    prompt = _PROMPT.format(
        sender_name=sender_name,
        from_=msg.get("From", ""),
        subject=msg.get("Subject", ""),
        body=_body_text(msg)[:2500],
    )
    try:
        resp = httpx.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        return str(resp.json().get("response", "")).strip()
    except Exception as e:
        logger.warning("drafter failed: {}", e)
        return ""
```

- [ ] **Step 4: Run, expect PASS**

```bash
uv run pytest tests/test_drafter.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hermes/classifier/drafter.py tests/test_drafter.py
git commit -m "feat(classifier): gemma4 reply drafter"
```

---

### Task 11: IMAP client wrapper

**Files:**
- Create: `src/hermes/mail/imap_client.py`, `tests/test_imap_client.py`

- [ ] **Step 1: Write the failing test**

`tests/test_imap_client.py`:
```python
from unittest.mock import MagicMock, patch
from hermes.config import GmailConfig
from hermes.mail.imap_client import ImapClient


def _gmail():
    return GmailConfig(
        imap_user="u@gmail.com", imap_app_password="p",
        smtp_user="u@gmail.com", smtp_app_password="p",
        from_address="U <u@gmail.com>", reply_to="U <u@x.com>",
    )


def test_imap_client_connect_logs_in():
    fake_box = MagicMock()
    with patch("hermes.mail.imap_client.MailBox") as mb:
        mb.return_value.login.return_value = fake_box
        ic = ImapClient(_gmail())
        ic.connect()
    mb.assert_called_once_with("imap.gmail.com")
    mb.return_value.login.assert_called_once_with("u@gmail.com", "p", initial_folder="INBOX")


def test_imap_client_apply_label_uses_gmail_extension():
    fake_box = MagicMock()
    ic = ImapClient(_gmail())
    ic._box = fake_box
    ic.apply_label(uid=42, label="Hermes/Promo")
    fake_box.client.uid.assert_called()
    args = fake_box.client.uid.call_args.args
    assert args[0] == "STORE"
    assert "X-GM-LABELS" in args[2]
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_imap_client.py -v
```

- [ ] **Step 3: Implement `src/hermes/mail/imap_client.py`**

```python
from __future__ import annotations
from contextlib import contextmanager
from typing import Iterator
from imap_tools import MailBox, AND
from loguru import logger
from hermes.config import GmailConfig

IMAP_HOST = "imap.gmail.com"


class ImapClient:
    def __init__(self, gmail: GmailConfig):
        self.gmail = gmail
        self._box: MailBox | None = None

    def connect(self) -> None:
        box = MailBox(IMAP_HOST).login(
            self.gmail.imap_user, self.gmail.imap_app_password, initial_folder="INBOX"
        )
        self._box = box
        logger.info("imap connected as {}", self.gmail.imap_user)

    def disconnect(self) -> None:
        if self._box:
            try:
                self._box.logout()
            except Exception as e:
                logger.warning("imap logout error: {}", e)
        self._box = None

    def fetch_recent(self, since_uid: int = 0, limit: int = 100) -> Iterator:
        assert self._box, "call connect() first"
        criteria = AND(uid=f"{since_uid + 1}:*") if since_uid else AND(all=True)
        for msg in self._box.fetch(criteria, mark_seen=False, limit=limit):
            yield msg

    def apply_label(self, uid: int, label: str) -> None:
        """Apply a Gmail label via the X-GM-LABELS IMAP extension."""
        assert self._box, "call connect() first"
        # Gmail-specific: STORE +X-GM-LABELS
        self._box.client.uid("STORE", str(uid), "+X-GM-LABELS", f'"{label}"')
        logger.debug("imap label uid={} label={}", uid, label)

    def archive(self, uid: int) -> None:
        assert self._box, "call connect() first"
        # Remove \\Inbox label = archive
        self._box.client.uid("STORE", str(uid), "-X-GM-LABELS", '"\\\\Inbox"')

    def idle(self, timeout: int = 29 * 60) -> bool:
        """Block until new mail or timeout. Returns True if new mail arrived."""
        assert self._box, "call connect() first"
        # imap_tools idle context
        with self._box.idle as idle:
            responses = idle.poll(timeout=timeout)
        return any("EXISTS" in r for r in responses)


@contextmanager
def imap_session(gmail: GmailConfig) -> Iterator[ImapClient]:
    client = ImapClient(gmail)
    client.connect()
    try:
        yield client
    finally:
        client.disconnect()
```

- [ ] **Step 4: Run, expect PASS**

```bash
uv run pytest tests/test_imap_client.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hermes/mail/imap_client.py tests/test_imap_client.py
git commit -m "feat(mail): IMAP client wrapper with Gmail label + IDLE support"
```

---

### Task 12: Mail daemon (the main classify-and-route loop)

**Files:**
- Create: `src/hermes/models.py`, `src/hermes/mail/daemon.py`, `tests/test_mail_daemon.py`

- [ ] **Step 1: Write `src/hermes/models.py`**

```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingMessage:
    uid: int
    message_id: str
    received_at: str  # ISO 8601
    sender: str
    subject: str
    raw_email: bytes  # full RFC822 bytes for reclassification
```

- [ ] **Step 2: Write the failing test**

`tests/test_mail_daemon.py`:
```python
from email import message_from_bytes
from pathlib import Path
from unittest.mock import MagicMock
from hermes.config import Config, GmailConfig, TelegramConfig, ClientConfig
from hermes.classifier.rules import RuleResult, ClientHint
from hermes.classifier.llm import LLMResult
from hermes.mail.daemon import process_message, classify_message, LLM_THRESHOLD

FIXTURES = Path(__file__).parent / "fixtures" / "mbox"


def _cfg() -> Config:
    return Config(
        gmail=GmailConfig(
            imap_user="u@g.com", imap_app_password="p",
            smtp_user="u@g.com", smtp_app_password="p",
            from_address="U <u@g.com>", reply_to="U <u@x.com>",
        ),
        telegram=TelegramConfig(bot_token="1:A", chat_id=0),
        digest_evening_report=False,
        clients={
            "boarhog": ClientConfig(
                name="boarhog", domain="boarhog.example",
                contact_email="billing@boarhog.example",
                billing_amount_cents=50000, billing_terms="Net 30",
                service_description="Hosting fees", active=True,
            )
        },
    )


def test_classify_message_uses_rules_first():
    msg = message_from_bytes((FIXTURES / "promo_amazon.eml").read_bytes())
    result = classify_message(msg, _cfg(), llm_classifier=None)
    assert result.label == "promo"
    assert result.classifier == "rule"


def test_classify_message_falls_to_llm_when_no_rule():
    msg = message_from_bytes((FIXTURES / "ambiguous.eml").read_bytes())
    fake_llm = MagicMock(return_value=LLMResult(label="client", confidence=0.85, why="x"))
    result = classify_message(msg, _cfg(), llm_classifier=fake_llm)
    assert result.label == "client"
    assert result.classifier == "qwen3"


def test_classify_message_below_threshold_becomes_review():
    msg = message_from_bytes((FIXTURES / "ambiguous.eml").read_bytes())
    fake_llm = MagicMock(return_value=LLMResult(label="client", confidence=0.5, why="x"))
    result = classify_message(msg, _cfg(), llm_classifier=fake_llm)
    assert result.label == "review"
    assert result.confidence == 0.5


def test_process_message_persists_and_labels(tmp_path):
    import sqlite3
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    process_message(uid=10, msg=msg, db=db, cfg=_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    rows = list(db.execute("SELECT uid, classification, label, classifier FROM messages"))
    assert rows == [(10, "client", "Hermes/Client/boarhog", "rule")]
    fake_imap.apply_label.assert_called_once_with(10, "Hermes/Client/boarhog")
```

- [ ] **Step 3: Run, expect FAIL**

```bash
uv run pytest tests/test_mail_daemon.py -v
```

- [ ] **Step 4: Implement `src/hermes/mail/daemon.py`**

```python
from __future__ import annotations
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
import sqlite3
import time
from typing import Callable
from loguru import logger
from hermes.config import Config, load_config
from hermes.classifier.rules import classify as rule_classify, ClientHint
from hermes.classifier.llm import classify_llm, LLMResult
from hermes.classifier.drafter import draft_reply
from hermes.db import connect, migrate
from hermes.mail.imap_client import ImapClient

LLM_THRESHOLD = 0.75


@dataclass(frozen=True)
class Classification:
    label: str          # promo|client|personal|transactional|review|other
    confidence: float
    classifier: str     # rule|qwen3|gemma4|user
    reason: str
    client: str | None = None


def _client_domains(cfg: Config) -> dict[str, ClientHint]:
    return {c.domain.lower(): ClientHint(client=c.name) for c in cfg.clients.values() if c.domain}


def classify_message(
    msg: Message,
    cfg: Config,
    llm_classifier: Callable[[Message], LLMResult] | None,
) -> Classification:
    rule = rule_classify(msg, _client_domains(cfg), personal_allowlist=set())
    if rule.label != "unknown":
        return Classification(
            label=rule.label,
            confidence=rule.confidence,
            classifier="rule",
            reason=rule.reason,
            client=rule.client,
        )
    if llm_classifier is None:
        return Classification(label="review", confidence=0.0, classifier="rule", reason="no llm")
    llm = llm_classifier(msg)
    label = llm.label if llm.confidence >= LLM_THRESHOLD else "review"
    return Classification(
        label=label, confidence=llm.confidence, classifier="qwen3", reason=llm.why
    )


def _label_for(c: Classification) -> str:
    if c.label == "client" and c.client:
        return f"Hermes/Client/{c.client}"
    return f"Hermes/{c.label.capitalize()}"


def process_message(
    uid: int,
    msg: Message,
    db: sqlite3.Connection,
    cfg: Config,
    imap: ImapClient,
    llm_classifier: Callable[[Message], LLMResult] | None,
    drafter: Callable[[Message, str], str] | None,
) -> None:
    classification = classify_message(msg, cfg, llm_classifier)
    label = _label_for(classification)
    message_id = msg.get("Message-ID", f"hermes-{uid}")
    received = msg.get("Date") or datetime.now(timezone.utc).isoformat()
    sender = msg.get("From", "")
    subject = msg.get("Subject", "")

    draft = ""
    if classification.label == "client" and drafter is not None:
        draft = drafter(msg, "Dylan")

    db.execute(
        "INSERT OR IGNORE INTO messages "
        "(uid, message_id, received_at, sender, subject, classification, classifier, "
        "confidence, label, draft_text, drafted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            uid, message_id, received, sender, subject,
            classification.label, classification.classifier, classification.confidence,
            label, draft, datetime.now(timezone.utc).isoformat() if draft else None,
        ),
    )
    try:
        imap.apply_label(uid, label)
        if classification.label == "promo":
            imap.archive(uid)
    except Exception as e:
        logger.error("imap label failed uid={} err={}", uid, e)


def _last_uid(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT MAX(uid) FROM messages").fetchone()
    return int(row[0] or 0)


def run_loop(cfg: Config, db: sqlite3.Connection, dry_run: bool = False) -> None:
    imap = ImapClient(cfg.gmail)
    imap.connect()
    try:
        while True:
            since = _last_uid(db)
            for fetched in imap.fetch_recent(since_uid=since, limit=200):
                process_message(
                    uid=fetched.uid,
                    msg=fetched.obj,
                    db=db,
                    cfg=cfg,
                    imap=imap,
                    llm_classifier=lambda m: classify_llm(m),
                    drafter=draft_reply if not dry_run else None,
                )
            try:
                imap.idle(timeout=29 * 60)
            except Exception as e:
                logger.warning("idle dropped: {}; reconnecting in 5s", e)
                imap.disconnect()
                time.sleep(5)
                imap.connect()
    finally:
        imap.disconnect()


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-mail")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--once", action="store_true", help="process pending and exit")
    p.add_argument("--db", default=str(__import__("pathlib").Path.home() / ".hermes" / "state.sqlite"))
    args = p.parse_args()

    cfg = load_config()
    from pathlib import Path as _P
    db = connect(_P(args.db))
    migrate(db)
    if args.once:
        imap = ImapClient(cfg.gmail)
        imap.connect()
        try:
            since = _last_uid(db)
            for fetched in imap.fetch_recent(since_uid=since, limit=200):
                process_message(
                    uid=fetched.uid, msg=fetched.obj, db=db, cfg=cfg, imap=imap,
                    llm_classifier=lambda m: classify_llm(m),
                    drafter=None if args.dry_run else draft_reply,
                )
        finally:
            imap.disconnect()
    else:
        run_loop(cfg, db, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run, expect PASS**

```bash
uv run pytest tests/test_mail_daemon.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/hermes/models.py src/hermes/mail/daemon.py tests/test_mail_daemon.py
git commit -m "feat(mail): daemon — classify, label, persist, draft on client mail"
```

---

### Task 13: Telegram client wrapper

**Files:**
- Create: `src/hermes/telegram/__init__.py`, `src/hermes/telegram/client.py`, `tests/test_telegram_client.py`

- [ ] **Step 1: Write the failing test**

`tests/test_telegram_client.py`:
```python
from unittest.mock import patch, MagicMock
from hermes.telegram.client import TelegramClient


def test_send_message_posts_to_correct_url():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.telegram.client.httpx.post", return_value=fake_resp) as p:
        c = TelegramClient(token="123:A", chat_id=42)
        c.send_message("hi")
    assert p.call_args.args[0].endswith("/bot123:A/sendMessage")
    body = p.call_args.kwargs["json"]
    assert body["chat_id"] == 42
    assert body["text"] == "hi"


def test_get_updates_returns_results():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"ok": True, "result": [{"update_id": 1, "message": {}}]}
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.telegram.client.httpx.get", return_value=fake_resp):
        c = TelegramClient(token="123:A", chat_id=42)
        u = c.get_updates(offset=0, timeout=1)
    assert u == [{"update_id": 1, "message": {}}]
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_telegram_client.py -v
```

- [ ] **Step 3: Implement `src/hermes/telegram/__init__.py`** (empty)

```bash
mkdir -p src/hermes/telegram && touch src/hermes/telegram/__init__.py
```

- [ ] **Step 4: Implement `src/hermes/telegram/client.py`**

```python
from __future__ import annotations
from pathlib import Path
import httpx
from loguru import logger


class TelegramClient:
    def __init__(self, token: str, chat_id: int):
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"

    def send_message(self, text: str, parse_mode: str = "Markdown") -> dict:
        resp = httpx.post(
            f"{self.base}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def send_document(self, path: Path, caption: str = "") -> dict:
        with path.open("rb") as f:
            files = {"document": (path.name, f, "application/octet-stream")}
            data = {"chat_id": str(self.chat_id), "caption": caption}
            resp = httpx.post(f"{self.base}/sendDocument", data=data, files=files, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def get_updates(self, offset: int, timeout: int = 30) -> list[dict]:
        resp = httpx.get(
            f"{self.base}/getUpdates",
            params={"offset": offset, "timeout": timeout},
            timeout=timeout + 5,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            logger.warning("telegram getUpdates not ok: {}", body)
            return []
        return body.get("result", [])
```

- [ ] **Step 5: Run, expect PASS**

```bash
uv run pytest tests/test_telegram_client.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/hermes/telegram/__init__.py src/hermes/telegram/client.py tests/test_telegram_client.py
git commit -m "feat(telegram): HTTP wrapper (sendMessage, sendDocument, getUpdates)"
```

---

### Task 14: Telegram command router

**Files:**
- Create: `src/hermes/telegram/commands.py`, `src/hermes/telegram/daemon.py`, `tests/test_telegram_commands.py`

- [ ] **Step 1: Write the failing test**

`tests/test_telegram_commands.py`:
```python
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


def test_other_chat_id_ignored(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    tg = MagicMock()
    update = {"update_id": 4, "message": {"chat": {"id": 99}, "text": "/status"}}
    handle_update(update, _cfg(), db, tg)
    tg.send_message.assert_not_called()
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_telegram_commands.py -v
```

- [ ] **Step 3: Implement `src/hermes/telegram/commands.py`**

```python
from __future__ import annotations
from datetime import datetime, timezone
import sqlite3
from loguru import logger
from hermes.config import Config
from hermes.mail.smtp_client import SmtpSender, build_message
from hermes.telegram.client import TelegramClient

HELP_TEXT = """*Hermes commands*
/status — daemon health + last-24h counts
/digest — on-demand morning report
/approve <id> — send queued draft/invoice
/reject <id> — discard
/invoice <client> [YYYY-MM] — generate invoice
/client list — show clients
/reclassify <uid> <label> — fix a wrong classification
/retry <id> — retry failed outbound
"""


def _do_send(cfg: Config, to_addr: str, subject: str, body: str) -> bool:
    """Hook so tests can patch SMTP without monkeypatching smtplib."""
    msg = build_message(cfg.gmail, to_addr, subject, body, [])
    try:
        SmtpSender(cfg.gmail).send(msg)
        return True
    except Exception as e:
        logger.error("send failed: {}", e)
        return False


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
        "/help": lambda *a: tg.send_message(HELP_TEXT),
        "/start": lambda *a: tg.send_message(HELP_TEXT),
    }
    fn = handlers.get(cmd)
    if not fn:
        tg.send_message(HELP_TEXT)
        return
    fn(args, cfg, db, tg)


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


def _cmd_approve(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /approve <outbox_id>")
        return
    try:
        oid = int(args[0])
    except ValueError:
        tg.send_message("Outbox id must be an integer")
        return
    row = db.execute(
        "SELECT to_addr, subject, body, status FROM outbox WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        tg.send_message(f"No outbox entry with id={oid}")
        return
    to_addr, subject, body, status = row
    if status != "queued":
        tg.send_message(f"Outbox {oid} status={status}, cannot send")
        return
    ok = _do_send(cfg, to_addr, subject, body)
    if ok:
        db.execute(
            "UPDATE outbox SET status='sent', sent_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), oid),
        )
        tg.send_message(f"Sent #{oid} to {to_addr}")
    else:
        db.execute("UPDATE outbox SET status='failed', last_error='send error' WHERE id=?", (oid,))
        tg.send_message(f"Failed to send #{oid} — see logs")


def _cmd_reject(args, cfg, db, tg):
    if not args:
        tg.send_message("Usage: /reject <outbox_id>")
        return
    try:
        oid = int(args[0])
    except ValueError:
        tg.send_message("Outbox id must be an integer")
        return
    res = db.execute("DELETE FROM outbox WHERE id=? AND status='queued'", (oid,))
    tg.send_message(f"Rejected #{oid}" if res.rowcount else f"Nothing to reject for id={oid}")
```

- [ ] **Step 4: Implement `src/hermes/telegram/daemon.py`**

```python
from __future__ import annotations
import argparse
from pathlib import Path
import time
from loguru import logger
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.telegram.client import TelegramClient
from hermes.telegram.commands import handle_update


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-tg")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    args = p.parse_args()

    cfg = load_config()
    db = connect(Path(args.db)); migrate(db)
    tg = TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id)
    offset = 0
    logger.info("hermes-tg started, chat_id={}", cfg.telegram.chat_id)
    while True:
        try:
            updates = tg.get_updates(offset=offset, timeout=30)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    handle_update(u, cfg, db, tg)
                except Exception as e:
                    logger.exception("handler failed: {}", e)
                    tg.send_message(f"⚠ handler error: {e}")
        except Exception as e:
            logger.warning("getUpdates failed: {}; sleeping 10s", e)
            time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run, expect PASS**

```bash
uv run pytest tests/test_telegram_commands.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/hermes/telegram/commands.py src/hermes/telegram/daemon.py tests/test_telegram_commands.py
git commit -m "feat(telegram): command router (/status /digest /approve /reject) + daemon"
```

---

## Phase C — Digest, invoice job, launchd (Tasks 15–19)

### Task 15: Digest builder

**Files:**
- Create: `src/hermes/digest/__init__.py`, `src/hermes/digest/build.py`, `tests/test_digest_build.py`

- [ ] **Step 1: Write the failing test**

`tests/test_digest_build.py`:
```python
from datetime import datetime, timedelta, timezone
from pathlib import Path
from hermes.config import Config, GmailConfig, TelegramConfig
from hermes.db import connect, migrate
from hermes.digest.build import build_morning_digest


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
    text = build_morning_digest(db, _cfg())
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
    text = build_morning_digest(db, _cfg())
    assert "promo: 2" in text.lower()
    assert "client: 1" in text.lower()


def test_digest_lists_pending_outbox(tmp_path):
    db = connect(tmp_path / "t.sqlite"); migrate(db)
    db.execute(
        "INSERT INTO outbox(kind, to_addr, subject, body, status, created_at) "
        "VALUES ('reply', 'a@b.com', 'Re: Q', 'b', 'queued', '2026-05-02')"
    )
    text = build_morning_digest(db, _cfg())
    assert "pending approval" in text.lower()
    assert "Re: Q" in text
```

- [ ] **Step 2: Run, expect FAIL**

```bash
uv run pytest tests/test_digest_build.py -v
```

- [ ] **Step 3: Implement files**

```bash
mkdir -p src/hermes/digest && touch src/hermes/digest/__init__.py
```

`src/hermes/digest/build.py`:
```python
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from hermes.config import Config


def build_morning_digest(db: sqlite3.Connection, cfg: Config) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    counts = dict(db.execute(
        "SELECT classification, COUNT(*) FROM messages "
        "WHERE received_at > datetime('now','-1 day') "
        "GROUP BY classification"
    ).fetchall())

    pending_rows = db.execute(
        "SELECT id, kind, to_addr, subject FROM outbox WHERE status='queued' ORDER BY id"
    ).fetchall()

    failed_count = db.execute(
        "SELECT COUNT(*) FROM outbox WHERE status='failed'"
    ).fetchone()[0]

    ledger_rows = db.execute(
        "SELECT direction, SUM(amount_cents) FROM ledger "
        "WHERE month = strftime('%Y-%m','now') GROUP BY direction"
    ).fetchall()
    ledger = {d: amt or 0 for d, amt in ledger_rows}

    lines: list[str] = [f"*Hermes morning report* — {now}", ""]

    classes = ["promo", "client", "personal", "transactional", "review", "other"]
    if not counts:
        lines.append("No messages in the last 24h.")
    else:
        lines.append("*Last 24h*")
        for c in classes:
            n = counts.get(c, 0)
            if n:
                lines.append(f"  {c}: {n}")

    client_rows = db.execute(
        "SELECT sender, subject FROM messages "
        "WHERE classification='client' AND received_at > datetime('now','-1 day') "
        "ORDER BY received_at DESC LIMIT 5"
    ).fetchall()
    if client_rows:
        lines.append("")
        lines.append("*Client mail*")
        for sender, subject in client_rows:
            lines.append(f"  • {sender} — {subject}")

    if pending_rows:
        lines.append("")
        lines.append(f"*Pending approval* ({len(pending_rows)})")
        for oid, kind, to_addr, subject in pending_rows[:10]:
            lines.append(f"  /approve {oid} — {kind} → {to_addr}: {subject}")

    if failed_count:
        lines.append("")
        lines.append(f"⚠ Failed sends: {failed_count} — `/retry <id>`")

    if ledger:
        lines.append("")
        lines.append("*This month*")
        out_dollars = ledger.get("out", 0) / 100
        in_dollars = ledger.get("in", 0) / 100
        lines.append(f"  Billed out: ${out_dollars:,.2f}")
        lines.append(f"  Spent: ${in_dollars:,.2f}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run, expect PASS**

```bash
uv run pytest tests/test_digest_build.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hermes/digest tests/test_digest_build.py
git commit -m "feat(digest): morning report builder"
```

---

### Task 16: Digest job entry point

**Files:**
- Create: `src/hermes/digest/job.py`

- [ ] **Step 1: Implement `src/hermes/digest/job.py`**

```python
from __future__ import annotations
import argparse
from pathlib import Path
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.digest.build import build_morning_digest
from hermes.telegram.client import TelegramClient


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-digest")
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--evening", action="store_true")
    p.add_argument("--print", action="store_true", help="print to stdout instead of Telegram")
    args = p.parse_args()
    cfg = load_config()
    db = connect(Path(args.db)); migrate(db)
    text = build_morning_digest(db, cfg)
    if args.print:
        print(text)
    else:
        TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id).send_message(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke test**

```bash
uv run hermes-digest --print
```
Expected: prints `*Hermes morning report* — ...` even on an empty DB.

- [ ] **Step 3: Commit**

```bash
git add src/hermes/digest/job.py
git commit -m "feat(digest): cron entry point (--evening, --print, default→Telegram)"
```

---

### Task 17: Monthly invoice job

**Files:**
- Create: `src/hermes/invoice/job.py`

- [ ] **Step 1: Implement `src/hermes/invoice/job.py`**

```python
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.invoice.cli import build_invoice_for_client, _compose_email
from hermes.telegram.client import TelegramClient


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-invoice")
    p.add_argument("--month", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--out-dir", default=str(Path.home() / ".hermes" / "invoices"))
    args = p.parse_args()

    cfg = load_config()
    db = connect(Path(args.db)); migrate(db)
    tg = TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id)

    year, mo = args.month.split("-")
    out_root = Path(args.out_dir) / year / mo

    queued: list[int] = []
    for key, client in cfg.clients.items():
        if not client.active or not client.billing_amount_cents:
            continue
        ctx, pdf_path = build_invoice_for_client(cfg, key, args.month, out_root)
        subject, body = _compose_email(ctx, cfg)
        cur = db.execute(
            "INSERT INTO outbox(kind, client, to_addr, subject, body, attachments, status, created_at) "
            "VALUES ('invoice', ?, ?, ?, ?, ?, 'queued', ?)",
            (key, ctx.client_email, subject, body, str(pdf_path),
             datetime.now(timezone.utc).isoformat()),
        )
        oid = cur.lastrowid
        queued.append(oid)
        logger.info("queued invoice id={} client={} amount=${:.2f}",
                    oid, key, ctx.total_cents / 100)
        tg.send_message(
            f"📄 *Invoice ready* (#{oid})\n"
            f"Client: {ctx.client_name}\n"
            f"Amount: ${ctx.total_cents / 100:,.2f}\n"
            f"Due: {ctx.due_date}\n"
            f"PDF: {pdf_path.name}\n\n"
            f"`/approve {oid}` to send · `/reject {oid}` to discard"
        )
        try:
            tg.send_document(pdf_path, caption=f"Invoice {ctx.invoice_number}")
        except Exception as e:
            logger.warning("could not upload PDF preview to telegram: {}", e)

    if not queued:
        tg.send_message("No active clients with billing configured — nothing to invoice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke test**

```bash
uv run hermes-invoice --month 2026-05
```
Expected: queues an invoice in `outbox`, posts approval card to Telegram (assuming valid token; otherwise the script exits with httpx error — that's fine, the queue is still written).

- [ ] **Step 3: Commit**

```bash
git add src/hermes/invoice/job.py
git commit -m "feat(invoice): monthly cron job — render, queue, post approval card"
```

---

### Task 18: launchd plists

**Files:**
- Create: `ops/launchd/com.beers.hermes-mail.plist`, `ops/launchd/com.beers.hermes-tg.plist`, `ops/launchd/com.beers.hermes-digest.plist`, `ops/launchd/com.beers.hermes-digest-evening.plist`, `ops/launchd/com.beers.hermes-invoice.plist`

- [ ] **Step 1: Write `ops/launchd/com.beers.hermes-mail.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.beers.hermes-mail</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/macmini/Projects/hermes/.venv/bin/hermes-mail</string>
  </array>
  <key>WorkingDirectory</key>  <string>/Users/macmini/Projects/hermes</string>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>ThrottleInterval</key>  <integer>30</integer>
  <key>StandardOutPath</key>   <string>/Users/macmini/.hermes/logs/mail.log</string>
  <key>StandardErrorPath</key> <string>/Users/macmini/.hermes/logs/mail.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
```

- [ ] **Step 2: Write `ops/launchd/com.beers.hermes-tg.plist`** (same template, swap Label and binary)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.beers.hermes-tg</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/macmini/Projects/hermes/.venv/bin/hermes-tg</string>
  </array>
  <key>WorkingDirectory</key>  <string>/Users/macmini/Projects/hermes</string>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>ThrottleInterval</key>  <integer>30</integer>
  <key>StandardOutPath</key>   <string>/Users/macmini/.hermes/logs/tg.log</string>
  <key>StandardErrorPath</key> <string>/Users/macmini/.hermes/logs/tg.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
```

- [ ] **Step 3: Write `ops/launchd/com.beers.hermes-digest.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.beers.hermes-digest</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/macmini/Projects/hermes/.venv/bin/hermes-digest</string>
  </array>
  <key>WorkingDirectory</key>  <string>/Users/macmini/Projects/hermes</string>
  <key>RunAtLoad</key>         <false/>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>   <integer>8</integer>
    <key>Minute</key> <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>   <string>/Users/macmini/.hermes/logs/digest.log</string>
  <key>StandardErrorPath</key> <string>/Users/macmini/.hermes/logs/digest.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
```

- [ ] **Step 4: Write `ops/launchd/com.beers.hermes-digest-evening.plist`** (Hour=18, ProgramArguments adds `--evening`, `Disabled=true` so opt-in)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.beers.hermes-digest-evening</string>
  <key>Disabled</key>          <true/>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/macmini/Projects/hermes/.venv/bin/hermes-digest</string>
    <string>--evening</string>
  </array>
  <key>WorkingDirectory</key>  <string>/Users/macmini/Projects/hermes</string>
  <key>RunAtLoad</key>         <false/>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>   <integer>18</integer>
    <key>Minute</key> <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>   <string>/Users/macmini/.hermes/logs/digest.log</string>
  <key>StandardErrorPath</key> <string>/Users/macmini/.hermes/logs/digest.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
```

- [ ] **Step 5: Write `ops/launchd/com.beers.hermes-invoice.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.beers.hermes-invoice</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/macmini/Projects/hermes/.venv/bin/hermes-invoice</string>
  </array>
  <key>WorkingDirectory</key>  <string>/Users/macmini/Projects/hermes</string>
  <key>RunAtLoad</key>         <false/>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Day</key>    <integer>1</integer>
    <key>Hour</key>   <integer>9</integer>
    <key>Minute</key> <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>   <string>/Users/macmini/.hermes/logs/invoice.log</string>
  <key>StandardErrorPath</key> <string>/Users/macmini/.hermes/logs/invoice.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
```

- [ ] **Step 6: Commit**

```bash
git add ops/launchd
git commit -m "feat(ops): launchd plists for mail/tg daemons + digest/invoice cron"
```

---

### Task 19: Install script

**Files:**
- Create: `ops/install.sh`

- [ ] **Step 1: Write `ops/install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${HOME}/Projects/hermes"
CFG_DIR="${HOME}/.config/hermes"
STATE_DIR="${HOME}/.hermes"
LAUNCH_DIR="${HOME}/Library/LaunchAgents"

echo "==> creating dirs"
mkdir -p "${CFG_DIR}" "${STATE_DIR}/logs" "${STATE_DIR}/invoices" "${LAUNCH_DIR}"
chmod 700 "${CFG_DIR}"

echo "==> ensuring uv"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh

echo "==> ensuring brew system deps for WeasyPrint"
for pkg in pango cairo gdk-pixbuf libffi; do
  brew list "${pkg}" >/dev/null 2>&1 || brew install "${pkg}"
done

echo "==> uv sync"
cd "${PROJECT_DIR}"
uv sync

if [ ! -f "${CFG_DIR}/secrets.toml" ]; then
  echo "==> writing secrets template (mode 600). EDIT THIS FILE before continuing."
  cat > "${CFG_DIR}/secrets.toml" <<'EOF'
[gmail]
imap_user = "dzbeers747@gmail.com"
imap_app_password = "REPLACE_ME"
smtp_user = "dzbeers747@gmail.com"
smtp_app_password = "REPLACE_ME"
from_address = "Dylan Beers <dzbeers747@gmail.com>"
reply_to = "Dylan Beers <dylan@ranger-beers.com>"

[telegram]
bot_token = "REPLACE_ME"
chat_id = 0

[digest]
evening_report = false
EOF
  chmod 600 "${CFG_DIR}/secrets.toml"
  echo "    edit ${CFG_DIR}/secrets.toml then re-run this script"
  exit 0
fi

echo "==> installing launchd plists"
for plist in ops/launchd/com.beers.hermes-*.plist; do
  name="$(basename "$plist")"
  cp -f "$plist" "${LAUNCH_DIR}/${name}"
  # bootstrap into user domain (idempotent)
  launchctl bootout "gui/$(id -u)/${name%.plist}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "${LAUNCH_DIR}/${name}"
  echo "    loaded ${name}"
done

echo "==> done"
echo "logs: ${STATE_DIR}/logs/"
echo "state: ${STATE_DIR}/state.sqlite"
echo "verify: launchctl list | grep com.beers.hermes"
```

- [ ] **Step 2: Make executable + commit**

```bash
chmod +x ops/install.sh
git add ops/install.sh
git commit -m "feat(ops): idempotent install.sh — uv sync, system deps, launchd bootstrap"
```

---

## Phase D — Deploy + smoke test (Task 20)

### Task 20: Deploy Phase B/C/D to ghengis

- [ ] **Step 1: Push updated tree**

```bash
rsync -av --exclude '.venv' --exclude '__pycache__' --exclude '*.sqlite' \
      /Users/beers/Projects/active/hermes/ macmini@ghengis:~/Projects/hermes/
```

- [ ] **Step 2: Run installer**

```bash
ssh macmini@ghengis 'bash -lc "cd ~/Projects/hermes && bash ops/install.sh"'
```
Expected on first run: prompts to edit secrets.toml. Fill in real values (Gmail app password, existing Telegram bot token from `~/Documents/telegram-bot.py`, your chat_id — get it by texting the bot once and reading `getUpdates`). Then re-run installer.

- [ ] **Step 3: Get chat_id**

In a Telegram conversation with the bot, send any message. Then on ghengis:

```bash
ssh macmini@ghengis 'bash -lc "curl -s https://api.telegram.org/bot$(awk -F\\\" \"/bot_token/{print \$2}\" ~/.config/hermes/secrets.toml)/getUpdates | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d[\\\"result\\\"][-1][\\\"message\\\"][\\\"chat\\\"][\\\"id\\\"])\""'
```
Copy the integer into `secrets.toml` `chat_id`, save, re-run `ops/install.sh`.

- [ ] **Step 4: Verify daemons loaded**

```bash
ssh macmini@ghengis 'launchctl list | grep com.beers.hermes'
```
Expected: 3 lines (mail, tg, invoice — digest fires only at 8am, evening is disabled).

- [ ] **Step 5: Send `/status` from Telegram**

Expected: bot replies with `Hermes status` and last-24h counts.

- [ ] **Step 6: Force a digest now**

```bash
ssh macmini@ghengis 'bash -lc "cd ~/Projects/hermes && uv run hermes-digest"'
```
Expected: morning report posted to Telegram chat.

- [ ] **Step 7: Tail logs**

```bash
ssh macmini@ghengis 'tail -n 30 ~/.hermes/logs/mail.log ~/.hermes/logs/tg.log'
```
Expected: `imap connected as dzbeers747@gmail.com`, `hermes-tg started, chat_id=...`.

- [ ] **Step 8: Send a test email** to `dzbeers747@gmail.com` with subject `Hermes test`. Within 30 s expect:
  - new row in `messages` table
  - Gmail label `Hermes/<class>` applied (visible in Gmail UI)
  - if classified as `client`, real-time Telegram ping

- [ ] **Step 9: Tag release**

```bash
cd /Users/beers/Projects/active/hermes
git tag v0.1.0 -m "Hermes v1.0 — full mail triage + invoicing on ghengis"
```

**v1 done.**

---

## Self-review checklist (already run; recorded for transparency)

1. **Spec coverage** — every section in the spec has a task:
   - Architecture diagram ✓ (Tasks 4, 11, 12, 13, 14)
   - Tier-1 rule classifier ✓ (Task 8)
   - Tier-2 LLM + threshold 0.75 ✓ (Tasks 9, 12)
   - Drafter ✓ (Task 10)
   - Outbound + Reply-To ✓ (Task 4)
   - Telegram commands ✓ (Task 14)
   - Boarhog onboarding via first-run prompt — implemented as Task 7 manual step (set clients.toml on the box) and as part of Task 14's `/client` (deferred to v1.1; Task 14 only ships /status /digest /approve /reject — `/client`, `/invoice <client>`, `/reclassify`, `/retry` are noted in HELP_TEXT but not yet wired). **Gap acknowledged: a v1.1 follow-up plan should cover these four commands.**
   - SQLite schema ✓ (Task 3)
   - Daily morning digest at 8am ✓ (Tasks 15, 16, 18)
   - Evening digest opt-in ✓ (Task 18, `Disabled=true`)
   - Monthly invoice cron ✓ (Tasks 17, 18)
   - launchd plists ✓ (Task 18)
   - Install script ✓ (Task 19)
   - Phase A invoice today ✓ (Tasks 1–7)

2. **Placeholder scan** — no TBDs, no "implement later", every code block is complete.

3. **Type consistency** — `Classification` defined in Task 12, used consistently. `RuleResult.label` returns `unknown` (Task 8) which `classify_message` (Task 12) handles by promoting to `review` after LLM. `LLMResult.label` enum aligns with `ALLOWED_LABELS` (Task 9).

4. **Ambiguity** — confidence threshold (0.75), label dedup, time zones, all explicit.

**Known v1.1 follow-ups (not in this plan):**
- `/client add|list|remove`, `/invoice <client>`, `/reclassify`, `/retry` command handlers (stubs are in `HELP_TEXT` but not wired to code).
- Notification dedup window (30 min/sender) — currently every classified-`client` mail sends a separate ping.
- Personal allowlist UI (currently hard-coded empty set in `mail/daemon.py`).
- Zoho Mail Free migration (swap SMTP creds + DNS).
- Inbound invoice tracking → `ledger` direction `'in'`.

These ship after v1 is stable; each is a 1–3 task add-on.
