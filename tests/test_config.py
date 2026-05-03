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
