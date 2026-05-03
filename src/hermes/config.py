from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
import tomllib


class ConfigError(ValueError):
    """Raised when secrets.toml or clients.toml is malformed."""


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
    clients: Mapping[str, ClientConfig] = field(default_factory=lambda: MappingProxyType({}))


def _require_bool(value, *, key: str, source: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(
            f"{source}: {key!r} must be a TOML bool (true/false), "
            f"got {type(value).__name__}: {value!r}"
        )
    return value


def _default_secrets_path() -> Path:
    return Path.home() / ".config" / "hermes" / "secrets.toml"


# Assumes repo-relative install (uv sync with package=true).
# In a non-editable install the source location moves into site-packages
# and this resolution breaks. Pass clients_path explicitly in that case.
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

    try:
        gmail = GmailConfig(**s["gmail"])
    except KeyError as e:
        raise ConfigError(f"{secrets_path}: missing section or key {e}") from e
    except TypeError as e:
        raise ConfigError(f"{secrets_path}: malformed [gmail] section - {e}") from e

    try:
        telegram = TelegramConfig(
            bot_token=s["telegram"]["bot_token"],
            chat_id=int(s["telegram"]["chat_id"]),
        )
    except KeyError as e:
        raise ConfigError(f"{secrets_path}: missing section or key {e}") from e
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{secrets_path}: malformed [telegram] section - {e}") from e

    try:
        digest_section = s.get("digest")
        if digest_section is None or "evening_report" not in digest_section:
            digest_evening = False
        else:
            digest_evening = _require_bool(
                digest_section["evening_report"],
                key="evening_report",
                source=f"{secrets_path}:[digest]",
            )
    except ConfigError:
        raise
    except (TypeError, AttributeError) as e:
        raise ConfigError(f"{secrets_path}: malformed [digest] section - {e}") from e

    clients: dict[str, ClientConfig] = {}
    for key, body in c.items():
        try:
            clients[key] = ClientConfig(
                name=key,
                domain=body["domain"],
                contact_email=body["contact_email"],
                billing_amount_cents=int(body["billing_amount_cents"]),
                billing_terms=body["billing_terms"],
                service_description=body["service_description"],
                active=_require_bool(
                    body["active"],
                    key="active",
                    source=f"{clients_path}:[{key}]",
                ),
            )
        except ConfigError:
            raise
        except KeyError as e:
            raise ConfigError(f"{clients_path}: client [{key}] missing key {e}") from e
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{clients_path}: client [{key}] - {e}") from e

    return Config(
        gmail=gmail,
        telegram=telegram,
        digest_evening_report=digest_evening,
        clients=MappingProxyType(clients),
    )
