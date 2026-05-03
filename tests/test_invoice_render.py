from datetime import date
import pytest
from hermes.invoice.render import render_html, render_pdf, InvoiceContext


def _weasyprint_available() -> bool:
    try:
        from weasyprint import HTML  # noqa: F401

        # Native libs load lazily in cffi; trigger by attempting a tiny render.
        HTML(string="<p>x</p>").render()
        return True
    except Exception:
        return False


_HAS_WEASYPRINT = _weasyprint_available()


def _ctx() -> InvoiceContext:
    return InvoiceContext(
        invoice_number="INV-2026-05-001",
        issue_date=date(2026, 5, 2),
        due_date=date(2026, 6, 1),
        client_name="Boarhog LLC",
        client_email="billing@boarhog.example",
        from_name="Dylan Beers",
        from_email="dylan@ranger-beers.com",
        line_items=[("Hosting fees — May 2026", 50000)],
        currency="USD",
        terms="Net 30",
        notes="Thank you for your business.",
    )


def test_render_html_contains_amount():
    html = render_html(_ctx())
    assert "Boarhog LLC" in html
    assert "INV-2026-05-001" in html
    assert "Hosting fees" in html
    assert "$500.00" in html  # 50000 cents formatted
    assert "Net 30" in html


def test_render_html_total_sums_lines(tmp_path):
    ctx = _ctx()
    ctx.line_items.append(("Setup", 10000))
    html = render_html(ctx)
    assert "$600.00" in html  # total


@pytest.mark.skipif(
    not _HAS_WEASYPRINT,
    reason="WeasyPrint native libs (pango/cairo/glib) not installed locally; runs on ghengis",
)
def test_render_pdf_writes_file(tmp_path):
    out = tmp_path / "invoice.pdf"
    render_pdf(_ctx(), out)
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
