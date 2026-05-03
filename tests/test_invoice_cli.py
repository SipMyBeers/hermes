from pathlib import Path
import pytest
from hermes.config import Config, GmailConfig, TelegramConfig, ClientConfig
from hermes.invoice.cli import build_invoice_for_client, _next_invoice_number


def _weasyprint_available() -> bool:
    try:
        from weasyprint import HTML  # noqa: F401

        # Native libs load lazily in cffi; trigger by attempting a tiny render.
        HTML(string="<p>x</p>").render()
        return True
    except Exception:
        return False


_HAS_WEASYPRINT = _weasyprint_available()


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


@pytest.mark.skipif(
    not _HAS_WEASYPRINT,
    reason="WeasyPrint native libs (pango/cairo/glib) not installed locally; runs on ghengis",
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
