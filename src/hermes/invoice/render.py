from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

# WeasyPrint is imported lazily inside render_pdf because it loads native
# libraries (pango, cairo, glib) at import time. Importing this module on a
# system that only needs render_html (HTML preview, tests) should not require
# the system deps.

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


@dataclass
class InvoiceContext:
    invoice_number: str
    issue_date: date
    due_date: date
    client_name: str
    client_email: str
    from_name: str
    from_email: str
    line_items: list[tuple[str, int]]  # (description, amount_cents)
    currency: str = "USD"
    terms: str = "Net 30"
    notes: str = ""
    # Optional rich-render fields. Empty defaults reproduce the v1 generic
    # invoice; set per-client to produce a branded layout.
    from_subtitle: str = ""
    from_location: str = ""
    bill_to_company: str = ""           # falls back to client_name in template
    bill_to_attn: str = ""
    bill_to_address_lines: tuple[str, ...] = ()
    description_subtitle: str = ""
    description_bullets: tuple[str, ...] = ()
    payment_methods: tuple[tuple[str, tuple[str, ...]], ...] = ()
    signoff: str = ""
    due_text: str = ""                  # if set, rendered instead of due_date

    @property
    def total_cents(self) -> int:
        return sum(c for _, c in self.line_items)


def render_html(ctx: InvoiceContext) -> str:
    tmpl = _env.get_template("standard.html")
    return tmpl.render(**ctx.__dict__, total_cents=ctx.total_cents)


def render_pdf(ctx: InvoiceContext, out_path: Path) -> Path:
    from weasyprint import HTML  # lazy: requires native libs at runtime

    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(ctx)
    HTML(string=html).write_pdf(str(out_path))
    return out_path
