from pathlib import Path
import pytest
from hermes.config import load_config, Config, GmailConfig, TelegramConfig, ClientConfig, MailConfig, UserConfig


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


def test_active_must_be_real_bool(tmp_path: Path):
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
    clients.write_text("""
[boarhog]
domain = "boarhog.example"
contact_email = "billing@boarhog.example"
billing_amount_cents = 50000
billing_terms = "Net 30"
service_description = "Hosting fees"
active = "yes"
""")
    from hermes.config import ConfigError
    with pytest.raises(ConfigError, match="active"):
        load_config(secrets_path=secrets, clients_path=clients)


def test_missing_gmail_key_reports_path(tmp_path: Path):
    secrets = tmp_path / "secrets.toml"
    secrets.write_text("""
[gmail]
imap_user = "u@g.com"

[telegram]
bot_token = "1:A"
chat_id = 0
""")
    clients = tmp_path / "clients.toml"
    clients.write_text("# empty")
    from hermes.config import ConfigError
    with pytest.raises(ConfigError) as exc:
        load_config(secrets_path=secrets, clients_path=clients)
    assert "secrets.toml" in str(exc.value) or str(secrets) in str(exc.value)


def test_clients_is_immutable(tmp_path: Path):
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
    with pytest.raises(TypeError):
        cfg.clients["injected"] = None  # type: ignore[index]


def test_load_config_mail_delete_promos_default_true(tmp_path: Path):
    """Omitting [mail] entirely yields delete_promos=True (matches the user's explicit ask)."""
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
    assert isinstance(cfg.mail, MailConfig)
    assert cfg.mail.delete_promos is True


def test_load_config_mail_delete_promos_explicit_false(tmp_path: Path):
    """Explicit [mail] delete_promos=false flips to archive-only behaviour."""
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

[mail]
delete_promos = false
""")
    clients = tmp_path / "clients.toml"
    clients.write_text("# empty")
    cfg = load_config(secrets_path=secrets, clients_path=clients)
    assert cfg.mail.delete_promos is False


def test_load_config_mail_delete_promos_must_be_bool(tmp_path: Path):
    """Strings like \"false\" are rejected — fail loud rather than silent truthiness."""
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

[mail]
delete_promos = "false"
""")
    clients = tmp_path / "clients.toml"
    clients.write_text("# empty")
    from hermes.config import ConfigError
    with pytest.raises(ConfigError, match="delete_promos"):
        load_config(secrets_path=secrets, clients_path=clients)


def test_load_config_user_section_full(tmp_path: Path):
    """[user] section round-trips name + entities + role."""
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

[user]
name = "Dylan Beers"
entities = ["Beers Labs LLC", "Beers Capital LLC", "Ranger Beers"]
role = "founder / web dev consultant"
""")
    clients = tmp_path / "clients.toml"
    clients.write_text("# empty")
    cfg = load_config(secrets_path=secrets, clients_path=clients)
    assert isinstance(cfg.user, UserConfig)
    assert cfg.user.name == "Dylan Beers"
    assert cfg.user.entities == ("Beers Labs LLC", "Beers Capital LLC", "Ranger Beers")
    assert cfg.user.role == "founder / web dev consultant"


def test_load_config_user_section_omitted_returns_empty_defaults(tmp_path: Path):
    """No [user] section yields a UserConfig() with empty fields — never raises."""
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
    assert isinstance(cfg.user, UserConfig)
    assert cfg.user.name == ""
    assert cfg.user.entities == ()
    assert cfg.user.role == ""


def test_two_clients_loaded(tmp_path: Path):
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
    clients.write_text("""
[boarhog]
domain = "boarhog.example"
contact_email = "billing@boarhog.example"
billing_amount_cents = 50000
billing_terms = "Net 30"
service_description = "Hosting fees"
active = true

[acme]
domain = "acme.example"
contact_email = "ap@acme.example"
billing_amount_cents = 75000
billing_terms = "Due on receipt"
service_description = "Retainer"
active = false
""")
    cfg = load_config(secrets_path=secrets, clients_path=clients)
    assert set(cfg.clients.keys()) == {"boarhog", "acme"}
    assert cfg.clients["acme"].active is False
    assert cfg.clients["acme"].billing_amount_cents == 75000
