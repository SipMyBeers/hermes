"""Run tesseract OCR over an image and return extracted text."""
from __future__ import annotations
import subprocess
from pathlib import Path
from loguru import logger


def ocr_image(image_path: Path, timeout: int = 30) -> str:
    """Returns OCR text. Empty string on failure or no detected text.

    Uses tesseract's stdout output (`-` as the output prefix). The tesseract
    CLI handles JPEG/PNG natively via leptonica; no Pillow needed."""
    image_path = Path(image_path)
    if not image_path.exists():
        return ""
    cmd = ["tesseract", str(image_path), "-", "-l", "eng", "--psm", "6"]
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("tesseract not available or timed out: {}", e)
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace").strip()


def ocr_images(image_paths: list[Path], timeout_each: int = 30) -> list[str]:
    """OCR a list of images, return their texts in input order. Empty entries
    for images that yielded no text or failed."""
    return [ocr_image(p, timeout=timeout_each) for p in image_paths]
