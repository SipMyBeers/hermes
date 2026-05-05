from __future__ import annotations
from dataclasses import dataclass
from email.message import Message
from typing import TYPE_CHECKING
import json
import httpx
from loguru import logger
from hermes.classifier.llm import _body_text

if TYPE_CHECKING:
    from hermes.config import UserConfig


@dataclass(frozen=True)
class Tool:
    name: str
    description: str   # one-line description from LLM general knowledge


@dataclass(frozen=True)
class EmailBrief:
    summary: str                 # one sentence; empty when the email is noise
    actions: tuple[str, ...]     # 1-3 imperative bullets, may be empty
    tools: tuple[Tool, ...]      # tools/products/services mentioned, may be empty
    importance: str = "normal"   # 'urgent' | 'normal' | 'low' | 'noise'


_PROMPT = """You are reading an email for {who_label}. Their entities: {entities}.
Your job is to surface what *the recipient* needs to know or do — NOT what the
sender wants from someone else, NOT what's already happened.

Output JSON ONLY:
{{
  "summary": "<one sentence, max 200 chars; what is this email and why does it matter to the recipient. EMPTY STRING if the email is generic noise (promotional sale alert with no item the recipient cares about, automated digest, routine confirmation that needs no action).>",
  "actions": ["<imperative verb-led, concrete; from the RECIPIENT's perspective; max 3>"],
  "tools": [{{"name": "<product/library/service mentioned>", "description": "<one-line description; ONLY if you are confident; OMIT entirely if you don't know it>"}}],
  "importance": "<urgent|normal|low|noise>"
}}

Rules — read carefully:
- The recipient is {who_label}. If they wrote/sent the original message and this
  is a confirmation/forwarded copy, return importance='noise' and summary=''.
- Actions are what THE RECIPIENT does next, not what someone else should do.
- 'noise' = automated promos/digests/sale alerts with nothing personal. Empty
  summary means the brief filters this out.
- 'low' = transactional FYI (account sync nag, infrequent service notice).
- 'normal' = real work (client message, partner request, ops alert).
- 'urgent' = time-sensitive (client deadline this week, security alert,
  payment due in <48h, deployment is broken).
- For tools: if you're not confident about a name (proprietary platform, niche
  term), OMIT it entirely. Wrong descriptions are worse than no description.

Email:
From: {sender}
Subject: {subject}
Body (first 3000 chars):
{body}
"""


_EMPTY = EmailBrief(summary="", actions=(), tools=(), importance="normal")


def _format_who(user: "UserConfig") -> str:
    if user.name and user.role:
        return f"{user.name} ({user.role})"
    if user.name:
        return user.name
    return "the recipient"


def summarize_for_brief(
    msg: Message,
    user: "UserConfig | None" = None,
    model: str = "gemma4",
    host: str = "http://localhost:11434",
    timeout: float = 90.0,
) -> EmailBrief:
    """Run gemma4 to produce a per-email brief. Graceful: returns empty
    EmailBrief on Ollama errors / unparseable output so the digest still renders.

    user: identity context so the LLM can disambiguate 'you' vs counterparty.
    Defaults to an empty UserConfig() if not provided (back-compat for tests
    and any callers that haven't been updated)."""
    # Lazy import to avoid circular dependency (config imports nothing from
    # digest, but enrich is imported from digest paths during cfg-less tests).
    from hermes.config import UserConfig
    if user is None:
        user = UserConfig()

    prompt = _PROMPT.format(
        who_label=_format_who(user),
        entities=", ".join(user.entities) if user.entities else "(none configured)",
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
        importance = str(data.get("importance", "normal")).lower().strip()
        if importance not in {"urgent", "normal", "low", "noise"}:
            importance = "normal"
        return EmailBrief(
            summary=summary,
            actions=actions,
            tools=tuple(tools_list),
            importance=importance,
        )
    except (AttributeError, TypeError) as e:
        logger.warning("enrich response malformed shape err={}", e)
        return _EMPTY
