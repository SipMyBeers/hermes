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
    n1 = _next_invoice_number(tmp_path, "INV")
    (tmp_path / f"{n1}.pdf").touch()
    n2 = _next_invoice_number(tmp_path, "INV")
    assert n1 != n2
    assert n1.startswith("INV-")
    assert n2.startswith("INV-")


def test_next_invoice_number_uses_max_not_count(tmp_path):
    """Regression: deleting an invoice must not cause re-numbering collision."""
    (tmp_path / "INV-001.pdf").touch()
    (tmp_path / "INV-002.pdf").touch()
    (tmp_path / "INV-001.pdf").unlink()  # delete first invoice
    n = _next_invoice_number(tmp_path, "INV")
    assert n == "INV-003"  # max(2) + 1


def test_next_invoice_number_skips_non_numeric_tails(tmp_path):
    (tmp_path / "INV-001.pdf").touch()
    (tmp_path / "INV-draft.pdf").touch()  # garbage filename
    n = _next_invoice_number(tmp_path, "INV")
    assert n == "INV-002"  # max(numeric) + 1, ignores 'draft'


def test_next_invoice_number_seeds_when_empty(tmp_path):
    """Fresh install with seed=3 should return prefix-003."""
    n = _next_invoice_number(tmp_path, "BH", seed=3)
    assert n == "BH-003"


def test_next_invoice_number_recursive_glob(tmp_path):
    """Cross-month sequence: BH-001 in 2026/03, BH-002 in 2026/04, next is BH-003."""
    (tmp_path / "2026" / "03").mkdir(parents=True)
    (tmp_path / "2026" / "04").mkdir(parents=True)
    (tmp_path / "2026" / "03" / "BH-001.pdf").touch()
    (tmp_path / "2026" / "04" / "BH-002.pdf").touch()
    n = _next_invoice_number(tmp_path, "BH", seed=1)
    assert n == "BH-003"


def test_compose_email_includes_invoice_metadata():
    from datetime import date
    from hermes.invoice.cli import _compose_email
    from hermes.invoice.render import InvoiceContext
    ctx = InvoiceContext(
        invoice_number="INV-2026-05-001",
        issue_date=date(2026, 5, 1),
        due_date=date(2026, 5, 31),
        client_name="Boarhog",
        client_email="b@example.com",
        from_name="Dylan Beers",
        from_email="dylan@ranger-beers.com",
        line_items=[("Hosting", 50000)],
        terms="Net 30",
    )
    subject, body = _compose_email(ctx, cfg=None)
    assert "INV-2026-05-001" in subject
    assert "Boarhog" in subject
    assert "$500.00" in body
    assert "May 31, 2026" in body
    assert "Net 30" in body
    assert body.endswith("Dylan Beers")


def test_inactive_client_refuses_to_bill(tmp_path):
    cfg = _config(tmp_path)
    inactive_client = ClientConfig(
        name="boarhog", domain="boarhog.example",
        contact_email="b@example.com",
        billing_amount_cents=50000, billing_terms="Net 30",
        service_description="Hosting fees", active=False,
    )
    cfg2 = Config(
        gmail=cfg.gmail, telegram=cfg.telegram,
        digest_evening_report=False,
        clients={"boarhog": inactive_client},
    )
    with pytest.raises(ValueError, match="inactive"):
        build_invoice_for_client(cfg2, "boarhog", "2026-05", tmp_path)


@pytest.mark.skipif(
    not _HAS_WEASYPRINT,
    reason="WeasyPrint native libs not loadable; runs on ghengis",
)
def test_due_date_net_30_is_30_days_from_issue(tmp_path):
    cfg = _config(tmp_path)
    ctx, _ = build_invoice_for_client(cfg, "boarhog", "2026-05", tmp_path)
    from datetime import date, timedelta
    assert ctx.issue_date == date(2026, 5, 1)
    assert ctx.due_date == date(2026, 5, 1) + timedelta(days=30)


def test_due_date_terms_parsing_no_pdf():
    """Test the date logic without WeasyPrint by stubbing render_pdf."""
    from datetime import date, timedelta
    from unittest.mock import patch
    from hermes.config import Config, GmailConfig, TelegramConfig, ClientConfig
    cfg = Config(
        gmail=GmailConfig(
            imap_user="u@g.com", imap_app_password="p",
            smtp_user="u@g.com", smtp_app_password="p",
            from_address="Dylan Beers <u@g.com>",
            reply_to="Dylan Beers <dylan@ranger-beers.com>",
        ),
        telegram=TelegramConfig(bot_token="1:A", chat_id=0),
        digest_evening_report=False,
        clients={
            "net15": ClientConfig(
                name="net15", domain="x.example", contact_email="b@x.example",
                billing_amount_cents=10000, billing_terms="Net 15",
                service_description="X", active=True,
            ),
            "receipt": ClientConfig(
                name="receipt", domain="y.example", contact_email="b@y.example",
                billing_amount_cents=10000, billing_terms="Due on receipt",
                service_description="Y", active=True,
            ),
        },
    )
    with patch("hermes.invoice.cli.render_pdf") as render_pdf_mock:
        render_pdf_mock.return_value = None
        # Make sure pdf_path doesn't exist (mocked render won't create it)
        from pathlib import Path
        out_dir = Path("/tmp/hermes-test-stub")
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in out_dir.glob("INV-*.pdf"):
            f.unlink()
        ctx_n15, _ = build_invoice_for_client(cfg, "net15", "2026-05", out_dir)
        ctx_dor, _ = build_invoice_for_client(cfg, "receipt", "2026-05", out_dir)
    assert ctx_n15.due_date == date(2026, 5, 1) + timedelta(days=15)
    assert ctx_dor.due_date == date(2026, 5, 1)
