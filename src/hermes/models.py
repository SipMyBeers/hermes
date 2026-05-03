from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingMessage:
    uid: int
    message_id: str
    received_at: str  # ISO 8601
    sender: str
    subject: str
    raw_email: bytes  # full RFC822 bytes for reclassification
