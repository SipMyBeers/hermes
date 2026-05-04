"""gemma4-backed summarizer for ingested content (web articles, video transcripts).

gemma4 is multimodal (Capabilities: completion, vision, audio, tools, thinking),
so when the source is a video we pass keyframe images alongside the transcript
and OCR snippets via Ollama's `/api/generate` `images` field.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import httpx
from loguru import logger


@dataclass(frozen=True)
class ContentSummary:
    summary: str           # 2-4 sentence overview, max ~400 chars
    actions: tuple[str, ...]
    tools: tuple[dict, ...]    # [{"name": str, "description": str}]


_PROMPT = """You are summarizing content the recipient saved for later. The content may include a spoken transcript, on-screen text from video keyframes, and the keyframe images themselves (visible to you). Output JSON only:

{{
  "summary": "<2-4 sentences, max 400 chars; describe what this content is and the core takeaway. Combine spoken words AND visual context (what's on screen, demos shown, code visible, products shown).>",
  "actions": ["<concrete next step the reader should take>", ...],
  "tools": [{{"name": "<product/library/service mentioned or shown>", "description": "<one-line description from your knowledge>"}}, ...]
}}

Rules:
- Skip greetings, navigation, ads, footers.
- Reference visual elements when they add information not in the transcript (e.g. "shows a code editor with `scrape()` function").
- Actions are concrete (visit X, evaluate Y, install Z); empty array if none.
- Tools include any product, library, framework, SaaS, AI model named or visibly shown.
- Output ONLY valid JSON.

Title: {title}{body_section}{ocr_section}
"""


def summarize(
    title: str,
    text: str,
    images: list[Path] | None = None,
    ocr_snippets: list[str] | None = None,
    model: str = "gemma4",
    host: str = "http://localhost:11434",
    timeout: float = 180.0,
) -> ContentSummary:
    """Summarize content with optional vision input (gemma4 is multimodal).

    images: paths to keyframe JPEGs. Each is base64-encoded and sent via
        Ollama's `images` field on /api/generate. Set when the source is a
        video.
    ocr_snippets: text strings extracted from the same keyframes via Tesseract.
        Concatenated into the prompt as 'On-screen text:' so gemma4 sees both
        (a) the spoken transcript via `text`, (b) the visual scene via
        `images`, and (c) deterministic OCR text in case image+vision misses
        small fonts or stylized lettering.
    """
    if not (text or "").strip() and not images:
        return ContentSummary(summary="", actions=(), tools=())

    body_section = (
        f"\nSpoken transcript (first 6000 chars):\n{(text or '')[:6000]}\n"
        if text else ""
    )
    ocr_section = ""
    if ocr_snippets:
        joined = "\n---\n".join(s.strip() for s in ocr_snippets if s and s.strip())
        if joined:
            ocr_section = f"\nOn-screen text (OCR from keyframes):\n{joined[:3000]}\n"

    prompt = _PROMPT.format(
        title=title or "(no title)",
        body_section=body_section,
        ocr_section=ocr_section,
    )
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    if images:
        import base64
        b64_imgs: list[str] = []
        for p in images:
            try:
                b64_imgs.append(base64.b64encode(Path(p).read_bytes()).decode("ascii"))
            except OSError as e:
                logger.warning("could not read keyframe {}: {}", p, e)
        if b64_imgs:
            payload["images"] = b64_imgs

    try:
        resp = httpx.post(f"{host}/api/generate", json=payload, timeout=timeout)
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
