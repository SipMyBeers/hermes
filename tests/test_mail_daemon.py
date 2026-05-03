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
    assert result.classifier == "llm"


def test_classify_message_below_threshold_becomes_review():
    msg = message_from_bytes((FIXTURES / "ambiguous.eml").read_bytes())
    fake_llm = MagicMock(return_value=LLMResult(label="client", confidence=0.5, why="x"))
    result = classify_message(msg, _cfg(), llm_classifier=fake_llm)
    assert result.label == "review"
    assert result.confidence == 0.5


def test_process_message_persists_and_labels(tmp_path):
    from hermes.db import connect, migrate
    db = connect(tmp_path / "t.sqlite")
    migrate(db)
    msg = message_from_bytes((FIXTURES / "client_boarhog.eml").read_bytes())
    fake_imap = MagicMock()
    process_message(uid=10, msg=msg, db=db, cfg=_cfg(), imap=fake_imap, llm_classifier=None, drafter=None)
    rows = list(db.execute("SELECT uid, classification, label, classifier FROM messages"))
    assert rows == [(10, "client", "Hermes/Client/boarhog", "rule")]
    fake_imap.apply_label.assert_called_once_with(10, "Hermes/Client/boarhog")
