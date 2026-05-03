from __future__ import annotations
from dataclasses import dataclass, field
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
