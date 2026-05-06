"""webclaw subprocess wrapper. Returns None when webclaw isn't installed
or fails — caller falls back to trafilatura."""
from __future__ import annotations
import shutil
import subprocess
from dataclasses import dataclass
from loguru import logger


@dataclass(frozen=True)
class WebclawResult:
    text: str             # markdown / extracted text
    title: str = ""       # webclaw outputs a leading "# Title" line; we lift it


def is_available() -> bool:
    """True iff webclaw is on PATH."""
    return shutil.which("webclaw") is not None


def extract(
    url: str,
    *,
    output_format: str = "llm",   # 'llm' | 'markdown' | 'text' | 'json'
    depth: int = 0,                # 0 = single page; >0 = crawl
    max_pages: int = 50,
    timeout: int = 120,
) -> WebclawResult | None:
    """Run webclaw against url. Returns None on failure (caller falls back)."""
    if not is_available():
        return None
    cmd = ["webclaw", url, "-f", output_format]
    if depth > 0:
        cmd += ["--crawl", "--depth", str(depth), "--max-pages", str(max_pages)]
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("webclaw subprocess error: {}", e)
        return None
    if result.returncode != 0:
        logger.warning(
            "webclaw failed (rc={}): {}",
            result.returncode,
            result.stderr.decode("utf-8", errors="replace")[:300],
        )
        return None
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    # First H1 is the page title; everything else is body.
    title = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return WebclawResult(text=text, title=title)
