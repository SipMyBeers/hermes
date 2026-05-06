"""Web URL extraction. Prefers webclaw (TLS-fingerprinted, LLM-optimized,
handles PDFs and Cloudflare-protected sites); falls back to trafilatura
for the simple-static case."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import httpx
from loguru import logger
from hermes.ingest.webclaw import extract as _webclaw_extract


@dataclass(frozen=True)
class ExtractedDoc:
    title: str
    text: str
    url: str
    # Vision-pipeline sidecar fields. Empty for web extraction; populated by
    # the video extractor with sampled keyframes and per-frame OCR text.
    keyframes: tuple[Path, ...] = field(default_factory=tuple)
    ocr_snippets: tuple[str, ...] = field(default_factory=tuple)
    # When set, the orchestrator should `shutil.rmtree(workdir)` after the
    # downstream summarize() call has finished reading any keyframe bytes.
    # None for web extraction.
    workdir: Path | None = None


def _is_pdf_url(url: str) -> bool:
    """URL clearly points at a PDF — skip trafilatura, only webclaw handles it."""
    return url.lower().split("?", 1)[0].rstrip("/").endswith(".pdf")


def extract_web(url: str, timeout: float = 30.0, depth: int = 0) -> ExtractedDoc:
    """Fetch url. Tries webclaw first (better at modern sites + PDFs);
    falls back to trafilatura for simple HTML.

    depth>0 forces webclaw with --crawl --depth N. Caller (Telegram /read)
    surfaces this for docs-site ingestion. If webclaw is missing the
    --depth is silently ignored and we degrade to a single-page fetch.
    """
    # PDF: only webclaw handles it.
    if _is_pdf_url(url):
        result = _webclaw_extract(url, output_format="llm", timeout=int(timeout * 4))
        if result is not None:
            return ExtractedDoc(title=result.title, text=result.text, url=url)
        # webclaw missing AND it's a PDF — return an empty doc; caller surfaces it.
        logger.warning("webclaw missing; cannot extract PDF: {}", url)
        return ExtractedDoc(title="", text="", url=url)

    # Crawl mode: only webclaw can.
    if depth > 0:
        result = _webclaw_extract(
            url, output_format="llm", depth=depth,
            timeout=int(timeout * 4),
        )
        if result is not None:
            return ExtractedDoc(title=result.title, text=result.text, url=url)
        logger.warning("webclaw missing; --depth ignored, doing single-page fetch")
        depth = 0

    # Try webclaw single-page first.
    result = _webclaw_extract(url, output_format="llm", timeout=int(timeout * 2))
    if result is not None and result.text:
        return ExtractedDoc(title=result.title, text=result.text, url=url)

    # Fall back to trafilatura.
    return _trafilatura_extract(url, timeout=timeout)


def _trafilatura_extract(url: str, timeout: float = 30.0) -> ExtractedDoc:
    """Original lightweight HTML path: httpx GET → trafilatura.extract.

    Empty text means extraction failed (e.g. heavy JS site with no static
    content). Caller surfaces this as "no readable content extracted"."""
    import trafilatura
    resp = httpx.get(
        url, follow_redirects=True, timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (Hermes/0.1)"},
    )
    resp.raise_for_status()
    html = resp.text
    text = trafilatura.extract(
        html, include_comments=False, include_tables=False, no_fallback=False,
    ) or ""
    metadata = trafilatura.extract_metadata(html)
    title = (metadata.title if metadata and metadata.title else "") or ""
    return ExtractedDoc(title=title, text=text, url=url)
