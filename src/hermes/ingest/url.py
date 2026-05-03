"""URL ingestion entry point — fetch, extract, summarize, persist.

Public API: ingest(url, db, source_chat_id=None) -> Digestion (or raises).
The classifier dispatches to web or video extractors based on the host."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3
from urllib.parse import urlparse
from loguru import logger
from hermes.ingest.web import extract_web, ExtractedDoc
from hermes.ingest.summary import summarize, ContentSummary

URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)

_VIDEO_HOSTS = {
    "instagram.com", "www.instagram.com",
    "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com",
    "x.com", "twitter.com",  # status URLs may include video; classifier returns video
}


@dataclass(frozen=True)
class Digestion:
    id: int
    url: str
    kind: str            # 'web' | 'video' | 'unknown'
    title: str
    summary: str
    actions: tuple[str, ...]
    tools: tuple[dict, ...]   # [{"name": ..., "description": ...}]
    error: str = ""


def classify_url(url: str) -> str:
    """Return 'web' or 'video' based on host."""
    host = (urlparse(url).hostname or "").lower()
    return "video" if host in _VIDEO_HOSTS else "web"


def find_url(text: str) -> str | None:
    """Extract the first URL from a text blob, or None."""
    m = URL_RE.search(text or "")
    return m.group(0) if m else None


def ingest(
    url: str,
    db: sqlite3.Connection,
    source_chat_id: int | None = None,
) -> Digestion:
    """Fetch, extract, summarize, persist. Returns the Digestion row.

    On extraction failure: still inserts a row with error populated and
    returns it so the caller can ack the user with a useful message."""
    kind = classify_url(url)
    error = ""
    title = ""
    text = ""
    summary_obj = ContentSummary(summary="", actions=(), tools=())
    try:
        if kind == "web":
            doc = extract_web(url)
            title, text = doc.title, doc.text
            if text:
                summary_obj = summarize(title=title, text=text)
            else:
                error = "no readable content extracted"
        else:
            from hermes.ingest.video import extract_video
            doc = extract_video(url)  # raises NotImplementedError in v1.2
            title, text = doc.title, doc.text
            summary_obj = summarize(title=title, text=text)
    except NotImplementedError as e:
        error = f"video ingestion deferred to v1.3: {e}"
    except Exception as e:
        logger.exception("ingest failed for {}: {}", url, e)
        error = f"{type(e).__name__}: {e}"

    cur = db.execute(
        "INSERT INTO digestions(url, kind, title, content_text, summary, "
        "actions, tools, source_chat_id, ingested_at, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            url, kind, title, text, summary_obj.summary,
            json.dumps(list(summary_obj.actions)),
            json.dumps([{"name": t.get("name", ""), "description": t.get("description", "")}
                        for t in summary_obj.tools]),
            source_chat_id,
            datetime.now(timezone.utc).isoformat(),
            error,
        ),
    )
    return Digestion(
        id=cur.lastrowid,
        url=url, kind=kind, title=title,
        summary=summary_obj.summary,
        actions=summary_obj.actions,
        tools=tuple(dict(name=t.get("name", ""), description=t.get("description", ""))
                    for t in summary_obj.tools),
        error=error,
    )
