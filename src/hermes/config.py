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
