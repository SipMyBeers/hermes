from __future__ import annotations
import argparse
from datetime import date, timedelta
from pathlib import Path
import calendar
from loguru import logger
from hermes.config import Config, load_config
from hermes.invoice.render import InvoiceContext, render_pdf
from hermes.mail.smtp_client import SmtpSender, build_message

_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _next_invoice_number(out_dir: Path, month: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    nums = []
    for p in out_dir.glob(f"INV-{month}-*.pdf"):
        # filename like "INV-2026-05-007.pdf" -> tail "007"
        tail = p.stem.rsplit("-", 1)[-1]
        try:
            nums.append(int(tail))
        except ValueError:
            continue
    n = (max(nums) + 1) if nums else 1
    return f"INV-{month}-{n:03d}"


def build_invoice_for_client(
    cfg: Config, client_key: str, month: str, out_dir: Path
) -> tuple[InvoiceContext, Path]:
    if client_key not in cfg.clients:
        raise KeyError(f"client {client_key!r} not in clients.toml")
    client = cfg.clients[client_key]
    if not client.active:
        raise ValueError(f"client {client_key} is inactive in clients.toml")
    if not client.billing_amount_cents:
        raise ValueError(f"client {client_key} has no billing_amount_cents")
    year, mo = map(int, month.split("-"))
    issue = date(year, mo, 1)
    last_day = calendar.monthrange(year, mo)[1]
    end_of_month = date(year, mo, last_day)
    terms_lower = client.billing_terms.lower()
    if terms_lower.startswith("due on receipt"):
        due = issue
    elif terms_lower.startswith("net "):
        try:
            days = int(terms_lower.split()[1])
        except (IndexError, ValueError):
            days = 30  # safe default
        due = issue + timedelta(days=days)
    else:
        due = end_of_month  # fallback for unrecognized terms
    invoice_number = _next_invoice_number(out_dir, month)
    line_label = f"{client.service_description} — {_MONTH_NAMES[mo]} {year}"
    ctx = InvoiceContext(
        invoice_number=invoice_number,
        issue_date=issue,
        due_date=due,
        client_name=client.name,
        client_email=client.contact_email,
        from_name=cfg.gmail.from_address.split(" <")[0].strip(),
        from_email=cfg.gmail.reply_to.split("<")[-1].rstrip(">").strip(),
        line_items=[(line_label, client.billing_amount_cents)],
        terms=client.billing_terms,
    )
    pdf_path = out_dir / f"{invoice_number}.pdf"
    if pdf_path.exists():
        raise FileExistsError(f"refusing to overwrite existing invoice: {pdf_path}")
    render_pdf(ctx, pdf_path)
    return ctx, pdf_path


def _compose_email(ctx: InvoiceContext, cfg: Config) -> tuple[str, str]:
    subject = f"Invoice {ctx.invoice_number} — {ctx.client_name}"
    total_dollars = f"${ctx.total_cents / 100:,.2f}"
    body = (
        f"Hi,\n\n"
        f"Attached is invoice {ctx.invoice_number} for {total_dollars}, "
        f"due {ctx.due_date.strftime('%B %d, %Y')} ({ctx.terms}).\n\n"
        f"Reply to this email if anything looks off.\n\n"
        f"Thanks,\n"
        f"{ctx.from_name}"
    )
    return subject, body


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-invoice-cli")
    p.add_argument("--client", required=True, help="client key from clients.toml")
    p.add_argument("--month", required=True, help="YYYY-MM")
    p.add_argument("--send", action="store_true", help="actually send (omit for dry-run)")
    p.add_argument("--out-dir", default=str(Path.home() / ".hermes" / "invoices"))
    args = p.parse_args()

    try:
        year_i, mo_i = map(int, args.month.split("-"))
        if not (1 <= mo_i <= 12 and 2000 <= year_i <= 2100):
            raise ValueError("month out of range")
    except ValueError:
        p.error(f"--month must be YYYY-MM with 4-digit year and 1-12 month, got {args.month!r}")
    month_norm = f"{year_i:04d}-{mo_i:02d}"
    year, mo = month_norm.split("-")

    cfg = load_config()
    out_dir = Path(args.out_dir) / year / mo
    ctx, pdf_path = build_invoice_for_client(cfg, args.client, month_norm, out_dir)
    subject, body = _compose_email(ctx, cfg)

    print(f"Invoice: {ctx.invoice_number}")
    print(f"Client:  {ctx.client_name} <{ctx.client_email}>")
    print(f"Amount:  ${ctx.total_cents / 100:,.2f}")
    print(f"Due:     {ctx.due_date}")
    print(f"PDF:     {pdf_path}")
    print(f"Subject: {subject}")
    print(f"--- Body ---\n{body}\n---")

    if not args.send:
        print("\nDry-run (use --send to actually email). PDF written above.")
        return 0

    msg = build_message(cfg.gmail, ctx.client_email, subject, body, [pdf_path])
    SmtpSender(cfg.gmail).send(msg)
    logger.info("invoice sent: {} to {}", ctx.invoice_number, ctx.client_email)
    print("\nSent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
