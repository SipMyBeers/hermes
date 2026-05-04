"""Web URL extraction via trafilatura (lightweight, no JS rendering)."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import httpx


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


def extract_web(url: str, timeout: float = 30.0) -> ExtractedDoc:
    """Fetch the URL and run trafilatura over the HTML.

    Returns ExtractedDoc with title and main text. Empty text means
    extraction failed (e.g. heavy JS site with no static content).
    """
    import trafilatura
    resp = httpx.get(url, follow_redirects=True, timeout=timeout,
                     headers={"User-Agent": "Mozilla/5.0 (Hermes/0.1)"})
    resp.raise_for_status()
    html = resp.text
    text = trafilatura.extract(html, include_comments=False,
                               include_tables=False, no_fallback=False) or ""
    metadata = trafilatura.extract_metadata(html)
    title = (metadata.title if metadata and metadata.title else "") or ""
    return ExtractedDoc(title=title, text=text, url=url)
