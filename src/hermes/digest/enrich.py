from __future__ import annotations
from dataclasses import dataclass
from email.message import Message
import json
import httpx
from loguru import logger
from hermes.classifier.llm import _body_text


@dataclass(frozen=True)
class Tool:
    name: str
    description: str   # one-line description from LLM general knowledge


@dataclass(frozen=True)
class EmailBrief:
    summary: str                 # one-or-two-sentence LLM summary
    actions: tuple[str, ...]     # 1-3 imperative bullets, may be empty
    tools: tuple[Tool, ...]      # tools/products/services mentioned, may be empty


_PROMPT = """You are reading an email for a busy founder. Produce a brief actionable summary in JSON:

{{
  "summary": "<one or two sentences, max 220 chars; what this email is and why it matters>",
  "actions": ["<imperative verb-led action 1>", "<action 2 if any>", "<action 3 if any>"],
  "tools": [{{"name": "<tool name>", "description": "<one-line description from your knowledge>"}}]
}}

Rules:
- Drop greetings/signatures from the summary.
- Actions are concrete next steps the recipient should take. Skip if none. Max 3.
- "tools" includes any product, service, library, framework, SaaS app, or AI model named in the email. One-line descriptions from your training knowledge. Empty array if none.
- Output ONLY valid JSON. No prose.

Email:
From: {sender}
Subject: {subject}
Body (first 3000 chars):
{body}
"""


_EMPTY = EmailBrief(summary="", actions=(), tools=())


def summarize_for_brief(
    msg: Message,
    model: str = "gemma4",
    host: str = "http://localhost:11434",
    timeout: float = 90.0,
) -> EmailBrief:
    """Run gemma4 to produce a per-email brief. Graceful: returns empty
    EmailBrief on Ollama errors / unparseable output so the digest still renders."""
    prompt = _PROMPT.format(
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        body=_body_text(msg)[:3000],
    )
    try:
        resp = httpx.post(
            f"{host}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        data = json.loads(raw)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("enrich failed for sender={!r} err={}", msg.get("From"), e)
        return _EMPTY

    try:
        summary = str(data.get("summary", "")).strip()
        actions = tuple(
            str(a).strip()
            for a in (data.get("actions") or [])
            if a is not None and str(a).strip()
        )
        tools_raw = data.get("tools") or []
        tools_list: list[Tool] = []
        for t in tools_raw:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name", "")).strip()
            if not name:
                continue
            desc = str(t.get("description", "")).strip()
            tools_list.append(Tool(name=name, description=desc))
        return EmailBrief(summary=summary, actions=actions, tools=tuple(tools_list))
    except (AttributeError, TypeError) as e:
        logger.warning("enrich response malformed shape err={}", e)
        return _EMPTY
