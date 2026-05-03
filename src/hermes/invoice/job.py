from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from hermes.config import load_config
from hermes.db import connect, migrate
from hermes.invoice.cli import build_invoice_for_client, _compose_email
from hermes.telegram.client import TelegramClient


def main() -> int:
    p = argparse.ArgumentParser(prog="hermes-invoice")
    p.add_argument("--month", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    p.add_argument("--db", default=str(Path.home() / ".hermes" / "state.sqlite"))
    p.add_argument("--out-dir", default=str(Path.home() / ".hermes" / "invoices"))
    args = p.parse_args()

    cfg = load_config()
    db = connect(Path(args.db)); migrate(db)
    tg = TelegramClient(cfg.telegram.bot_token, cfg.telegram.chat_id)

    year, mo = args.month.split("-")
    invoices_root = Path(args.out_dir)
    out_root = invoices_root / year / mo

    queued: list[int] = []
    for key, client in cfg.clients.items():
        if not client.active or not client.billing_amount_cents:
            continue
        ctx, pdf_path = build_invoice_for_client(
            cfg, key, args.month, out_root, invoices_root=invoices_root
        )
        subject, body = _compose_email(ctx, cfg)
        cur = db.execute(
            "INSERT INTO outbox(kind, client, to_addr, subject, body, attachments, status, created_at) "
            "VALUES ('invoice', ?, ?, ?, ?, ?, 'queued', ?)",
            (
                key,
                ctx.client_email,
                subject,
                body,
                str(pdf_path),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        oid = cur.lastrowid
        queued.append(oid)
        logger.info(
            "queued invoice id={} client={} amount=${:.2f}",
            oid, key, ctx.total_cents / 100,
        )
        tg.send_message(
            f"📄 *Invoice ready* (#{oid})\n"
            f"Client: {ctx.bill_to_company or ctx.client_name}\n"
            f"Amount: ${ctx.total_cents / 100:,.2f}\n"
            f"Due: {ctx.due_text or ctx.due_date}\n"
            f"PDF: {pdf_path.name}\n\n"
            f"`/approve {oid}` to send · `/reject {oid}` to discard"
        )
        try:
            tg.send_document(pdf_path, caption=f"Invoice {ctx.invoice_number}")
        except Exception as e:
            logger.warning("could not upload PDF preview to telegram: {}", e)

    if not queued:
        tg.send_message("No active clients with billing configured — nothing to invoice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
