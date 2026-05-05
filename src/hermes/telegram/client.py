from __future__ import annotations
from pathlib import Path
import httpx
from loguru import logger


class TelegramClient:
    def __init__(self, token: str, chat_id: int):
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"

    def send_message(
        self,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup: dict | None = None,
    ) -> dict:
        body: dict = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        resp = httpx.post(f"{self.base}/sendMessage", json=body, timeout=15)
        if resp.status_code == 400 and parse_mode:
            # Telegram rejects the Markdown — retry as plain text so the user
            # sees the message even if formatting breaks (long file paths, code,
            # raw error bodies often contain `_*[]` that confuse the Markdown
            # parser). Keep the reply_markup on retry — the keyboard is
            # parser-independent and the user still needs to tap the buttons.
            logger.warning(
                "telegram 400 with parse_mode={}; retrying as plain text", parse_mode
            )
            retry_body = {k: v for k, v in body.items() if k != "parse_mode"}
            resp = httpx.post(f"{self.base}/sendMessage", json=retry_body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def edit_message_text(
        self,
        message_id: int,
        text: str,
        parse_mode: str = "Markdown",
        reply_markup: dict | None = None,
    ) -> dict:
        """Edit a previously-sent message in this chat. Used after a callback
        to reflect the new state ('✓ done')."""
        body: dict = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        resp = httpx.post(f"{self.base}/editMessageText", json=body, timeout=15)
        if resp.status_code == 400 and parse_mode:
            logger.warning(
                "telegram 400 editMessageText parse_mode={}; retrying plain", parse_mode
            )
            retry_body = {k: v for k, v in body.items() if k != "parse_mode"}
            resp = httpx.post(f"{self.base}/editMessageText", json=retry_body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def answer_callback_query(self, callback_id: str, text: str = "") -> dict:
        """Acknowledge a button tap so the spinner clears. Optional flash text."""
        body: dict = {"callback_query_id": callback_id}
        if text:
            body["text"] = text
        resp = httpx.post(f"{self.base}/answerCallbackQuery", json=body, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def send_document(self, path: Path, caption: str = "") -> dict:
        with path.open("rb") as f:
            files = {"document": (path.name, f, "application/octet-stream")}
            data = {"chat_id": str(self.chat_id), "caption": caption}
            resp = httpx.post(f"{self.base}/sendDocument", data=data, files=files, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def get_updates(self, offset: int, timeout: int = 30) -> list[dict]:
        resp = httpx.get(
            f"{self.base}/getUpdates",
            params={"offset": offset, "timeout": timeout},
            timeout=timeout + 5,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            logger.warning("telegram getUpdates not ok: {}", body)
            return []
        return body.get("result", [])
