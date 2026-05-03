from __future__ import annotations
from pathlib import Path
import httpx
from loguru import logger


class TelegramClient:
    def __init__(self, token: str, chat_id: int):
        self.token = token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{token}"

    def send_message(self, text: str, parse_mode: str = "Markdown") -> dict:
        resp = httpx.post(
            f"{self.base}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": parse_mode},
            timeout=15,
        )
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
