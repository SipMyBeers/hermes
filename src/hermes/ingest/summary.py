"""gemma4-backed summarizer for ingested content (web articles, video transcripts)."""
from __future__ import annotations
from dataclasses import dataclass
import json
import httpx
from loguru import logger


@dataclass(frozen=True)
class ContentSummary:
    summary: str           # 2-4 sentence overview, max ~400 chars
    actions: tuple[str, ...]
    tools: tuple[dict, ...]    # [{"name": str, "description": str}]


_PROMPT = """You are summarizing content the recipient saved for later. Output JSON only:

{{
  "summary": "<2-4 sentences, max 400 chars; what this content is about and the core takeaway>",
  "actions": ["<concrete next step the reader should take>", ...],
  "tools": [{{"name": "<product/library/service mentioned>", "description": "<one-line description from your knowledge>"}}, ...]
}}

Rules:
- Skip greetings, navigation, ads, footers.
- Actions are concrete (visit X, evaluate Y, install Z); empty array if none.
- Tools include any product, library, framework, SaaS, or AI model named.
- Output ONLY valid JSON.

Title: {title}

Content (first 6000 chars):
{text}
"""


def summarize(
    title: str,
    text: str,
    model: str = "gemma4",
    host: str = "http://localhost:11434",
    timeout: float = 120.0,
) -> ContentSummary:
    if not text.strip():
        return ContentSummary(summary="", actions=(), tools=())
    prompt = _PROMPT.format(title=title or "(no title)", text=text[:6000])
    try:
        resp = httpx.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        data = json.loads(raw)
        summary = str(data.get("summary", "")).strip()
        actions = tuple(str(a).strip() for a in data.get("actions", []) if a)
        tools_raw = data.get("tools", []) or []
        tools = tuple(
            {"name": str(t.get("name", "")).strip(),
             "description": str(t.get("description", "")).strip()}
            for t in tools_raw if t.get("name")
        )
        return ContentSummary(summary=summary, actions=actions, tools=tools)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
        logger.warning("ingest summary failed: {}", e)
        return ContentSummary(summary="", actions=(), tools=())
