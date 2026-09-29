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
    contact_email: str
    active: bool
    # Routing-only clients (Phase 1) carry just contact_email (+ optionally
    # domain); billing fields stay zero/empty and the invoice job skips them.
    domain: str = ""
    billing_amount_cents: int = 0
    billing_terms: str = ""
    service_description: str = ""
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
    # Recipient addresses that must never leave the inbox, whatever the
    # classifier decides. Every rule keys off From, so mail forwarded in to a
    # business address (Cloudflare Email Routing -> Gmail) gets judged on its
    # sender and archived out of sight. Matched against To/Cc and the
    # forwarding headers; classification and labelling are unaffected.
    always_inbox: tuple[str, ...] = ()
    # Sender domains that must never leave the inbox (marketplaces: a missed
    # Fiverr/Upwork buyer message costs the order). Subdomains match too.
    always_inbox_senders: tuple[str, ...] = ()


@dataclass(frozen=True)
class UserConfig:
    """Identity context for the LLM. Populated from secrets.toml [user]
    so gemma4 can disambiguate 'you' vs counterparty when reading email."""
    name: str = ""                       # "Dylan Beers"
    entities: tuple[str, ...] = ()       # ("Beers Labs LLC", "Beers Capital LLC", "Ranger Beers")
    role: str = ""                       # "founder / web dev consultant"


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
class LeadsConfig:
    """Lead-engine knobs from clients.toml [leads]."""
    form_address: str = ""                  # contact-form submissions land here
    ignore_domains: tuple[str, ...] = ()    # never leads (e.g. kumquat funnel)


@dataclass(frozen=True)
class Config:
    gmail: GmailConfig
    telegram: TelegramConfig
    digest_evening_report: bool
    clients: Mapping[str, ClientConfig] = field(default_factory=lambda: MappingProxyType({}))
    mail: MailConfig = field(default_factory=MailConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    user: UserConfig = field(default_factory=UserConfig)
    # Partners are relationship-holders (e.g. uncle Josh) who CC/forward
    # client mail. Keyed by partner name -> lowercased addresses.
    partners: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    leads: LeadsConfig = field(default_factory=LeadsConfig)


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
        raw_always = mail_section.get("always_inbox", [])
        if isinstance(raw_always, str) or not isinstance(raw_always, (list, tuple)):
            raise ConfigError(
                f"{secrets_path}:[mail] always_inbox must be a list of addresses, "
                f"got {type(raw_always).__name__}"
            )
        always_inbox = tuple(str(a).strip().lower() for a in raw_always if str(a).strip())
        raw_senders = mail_section.get("always_inbox_senders", [])
        if isinstance(raw_senders, str) or not isinstance(raw_senders, (list, tuple)):
            raise ConfigError(
                f"{secrets_path}:[mail] always_inbox_senders must be a list of domains, "
                f"got {type(raw_senders).__name__}"
            )
        always_inbox_senders = tuple(
            str(d).strip().lower().lstrip("@") for d in raw_senders if str(d).strip()
        )
        mail_cfg = MailConfig(
            delete_promos=delete_promos,
            always_inbox=always_inbox,
            always_inbox_senders=always_inbox_senders,
        )
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

    try:
        user_section = s.get("user", {})
        user_cfg = UserConfig(
            name=str(user_section.get("name", "")),
            entities=tuple(str(e) for e in user_section.get("entities", [])),
            role=str(user_section.get("role", "")),
        )
    except (TypeError, AttributeError) as e:
        raise ConfigError(f"{secrets_path}: malformed [user] section - {e}") from e

    # [partners] is a reserved table, not a client: name -> list of addresses.
    partners: dict[str, tuple[str, ...]] = {}
    try:
        for pname, addrs in c.get("partners", {}).items():
            partners[str(pname)] = tuple(str(a).lower() for a in addrs)
    except (TypeError, AttributeError) as e:
        raise ConfigError(f"{clients_path}: malformed [partners] section - {e}") from e

    # [leads] is a reserved table, not a client.
    try:
        leads_section = c.get("leads", {})
        leads_cfg = LeadsConfig(
            form_address=str(leads_section.get("form_address", "")).lower(),
            ignore_domains=tuple(
                str(d).lower() for d in leads_section.get("ignore_domains", [])
            ),
        )
    except (TypeError, AttributeError) as e:
        raise ConfigError(f"{clients_path}: malformed [leads] section - {e}") from e

    clients: dict[str, ClientConfig] = {}
    for key, body in c.items():
        if key in ("partners", "leads"):
            continue
        try:
            payment_methods_raw = body.get("payment_methods", [])
            payment_methods: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
                (str(pm["label"]), tuple(str(v) for v in pm.get("values", [])))
                for pm in payment_methods_raw
            )
            clients[key] = ClientConfig(
                name=key,
                domain=str(body.get("domain", "")),
                contact_email=body["contact_email"],
                billing_amount_cents=int(body.get("billing_amount_cents", 0)),
                billing_terms=str(body.get("billing_terms", "")),
                service_description=str(body.get("service_description", "")),
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
        user=user_cfg,
        partners=MappingProxyType(partners),
        leads=leads_cfg,
    )
