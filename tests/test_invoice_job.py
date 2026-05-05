"""Phase K: invoice job posts the approval card with inline buttons."""
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
from hermes.config import Config, GmailConfig, TelegramConfig, ClientConfig
from hermes.invoice.render import InvoiceContext


def _cfg() -> Config:
    return Config(
        gmail=GmailConfig(
            imap_user="u@g.com", imap_app_password="p",
            smtp_user="u@g.com", smtp_app_password="p",
            from_address="Dylan <u@g.com>",
            reply_to="Dylan <dylan@example.com>",
        ),
        telegram=TelegramConfig(bot_token="1:A", chat_id=42),
        digest_evening_report=False,
        clients={
            "boarhog": ClientConfig(
                name="boarhog", domain="boarhog.example",
                contact_email="b@boarhog.example",
                billing_amount_cents=50000, billing_terms="Net 30",
                service_description="Hosting", active=True,
            )
        },
    )


def _ctx(pdf_path: Path) -> InvoiceContext:
    return InvoiceContext(
        invoice_number="INV-2026-05-001",
        issue_date=date(2026, 5, 1),
        due_date=date(2026, 5, 31),
        client_name="boarhog",
        client_email="b@boarhog.example",
        from_name="Dylan",
        from_email="dylan@example.com",
        line_items=[("Hosting", 50000)],
        terms="Net 30",
    )


def test_invoice_job_posts_approval_button(tmp_path, monkeypatch):
    """The Telegram message announcing a queued invoice carries an inline
    keyboard with approve:<id> and reject:<id> callbacks — no slash command
    text required."""
    pdf = tmp_path / "INV-2026-05-001.pdf"
    pdf.write_bytes(b"%PDF stub")

    fake_tg = MagicMock()
    cfg = _cfg()

    import hermes.invoice.job as job
    monkeypatch.setattr(job, "load_config", lambda: cfg)
    monkeypatch.setattr(job, "TelegramClient", lambda token, chat_id: fake_tg)
    monkeypatch.setattr(
        job, "build_invoice_for_client",
        lambda cfg, key, month, out_root, invoices_root=None: (_ctx(pdf), pdf),
    )
    monkeypatch.setattr(
        job, "_compose_email",
        lambda ctx, cfg: ("Invoice INV-2026-05-001", "body"),
    )

    db_path = tmp_path / "t.sqlite"
    with patch("sys.argv",
               ["hermes-invoice", "--month", "2026-05",
                "--db", str(db_path),
                "--out-dir", str(tmp_path / "invoices")]):
        rc = job.main()
    assert rc == 0

    # Find the approval card call (the card text starts with 📄).
    card_calls = [c for c in fake_tg.send_message.call_args_list
                  if "Invoice ready" in c.args[0]]
    assert len(card_calls) == 1
    kb = card_calls[0].kwargs.get("reply_markup")
    assert kb is not None
    cbs = [b["callback_data"] for r in kb["inline_keyboard"] for b in r]
    # The outbox row was id=1, so the buttons must point at approve:1 / reject:1.
    assert "approve:1" in cbs
    assert "reject:1" in cbs
    # The old slash-command hint must be gone — tap, don't type.
    assert "/approve" not in card_calls[0].args[0]
    assert "/reject" not in card_calls[0].args[0]
