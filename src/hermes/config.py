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
    # Optional fields for invoice rendering. Defaults reproduce the v1
    # generic invoice; override per-client to produce branded invoices.
    invoice_prefix: str = "INV"
    invoice_seed: int = 1
    bill_to_company: str = ""           # falls back to name.title() if empty
    bill_to_attn: str = ""
    bill_to_address_lines: tuple[str, ...] = ()
    description_subtitle: str = ""
    description_bullets: tuple[str, ...] = ()
    from_entity: str = ""               # invoice "From" line; defaults to from_name on Config
    from_subtitle: str = ""
    from_location: str = ""
    from_email_display: str = ""
    payment_methods: tuple[tuple[str, tuple[str, ...]], ...] = ()
    signoff: str = ""
    due_text: str = ""                  # if set, prints in place of due_date


@dataclass(frozen=True)
class MailConfig:
    delete_promos: bool = True   # default: send classified promos to Trash (auto-purge in 30d)


@dataclass(frozen=True)
class IngestConfig:
    """URL ingestion (yt-dlp) auth config. Both fields optional; pick the one
    that works for your setup.

    cookies_path: absolute path to a Netscape-format cookies.txt file. Use
    when you've exported cookies from a browser via an extension. Persists
    across browser restarts; survives logouts.

    cookies_from_browser: tuple of browser names yt-dlp tries IN ORDER. First
    one that produces an authenticated request wins. Common values: 'firefox',
    'chrome', 'safari', 'edge'. Note: macOS Chrome locks its cookie database
    while running — Firefox and Safari are friendlier to live extraction.
    """
    cookies_path: str = ""
    cookies_from_browser: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    gmail: GmailConfig
    telegram: TelegramConfig
    digest_evening_report: bool
    clients: Mapping[str, ClientConfig] = field(default_factory=lambda: MappingProxyType({}))
    mail: MailConfig = field(default_factory=MailConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)


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

    try:
        mail_section = s.get("mail", {})
        if "delete_promos" in mail_section:
            delete_promos = _require_bool(
                mail_section["delete_promos"],
                key="delete_promos",
                source=f"{secrets_path}:[mail]",
            )
        else:
            delete_promos = True
        mail_cfg = MailConfig(delete_promos=delete_promos)
    except ConfigError:
        raise
    except (TypeError, AttributeError) as e:
        raise ConfigError(f"{secrets_path}: malformed [mail] section - {e}") from e

    try:
        ingest_section = s.get("ingest", {})
        ingest_cfg = IngestConfig(
            cookies_path=str(ingest_section.get("cookies_path", "")),
            cookies_from_browser=tuple(
                str(b) for b in ingest_section.get("cookies_from_browser", [])
            ),
        )
    except (TypeError, AttributeError) as e:
        raise ConfigError(f"{secrets_path}: malformed [ingest] section - {e}") from e

    clients: dict[str, ClientConfig] = {}
    for key, body in c.items():
        try:
            payment_methods_raw = body.get("payment_methods", [])
            payment_methods: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
                (str(pm["label"]), tuple(str(v) for v in pm.get("values", [])))
                for pm in payment_methods_raw
            )
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
                invoice_prefix=str(body.get("invoice_prefix", "INV")),
                invoice_seed=int(body.get("invoice_seed", 1)),
                bill_to_company=str(body.get("bill_to_company", "")),
                bill_to_attn=str(body.get("bill_to_attn", "")),
                bill_to_address_lines=tuple(
                    str(line) for line in body.get("bill_to_address_lines", [])
                ),
                description_subtitle=str(body.get("description_subtitle", "")),
                description_bullets=tuple(
                    str(b) for b in body.get("description_bullets", [])
                ),
                from_entity=str(body.get("from_entity", "")),
                from_subtitle=str(body.get("from_subtitle", "")),
                from_location=str(body.get("from_location", "")),
                from_email_display=str(body.get("from_email_display", "")),
                payment_methods=payment_methods,
                signoff=str(body.get("signoff", "")),
                due_text=str(body.get("due_text", "")),
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
        mail=mail_cfg,
        ingest=ingest_cfg,
    )
