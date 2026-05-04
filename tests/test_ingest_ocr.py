from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

from hermes.ingest.ocr import ocr_image, ocr_images


def _img(tmp_path: Path, name: str = "frame.jpg") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0fakejpg")
    return p


def test_ocr_image_returns_stdout(tmp_path: Path):
    img = _img(tmp_path)
    fake = MagicMock(returncode=0, stdout=b"Buy now!\nFREE SHIPPING\n")
    with patch("hermes.ingest.ocr.subprocess.run", return_value=fake):
        text = ocr_image(img)
    assert text == "Buy now!\nFREE SHIPPING"


def test_ocr_image_nonzero_returns_empty(tmp_path: Path):
    img = _img(tmp_path)
    with patch("hermes.ingest.ocr.subprocess.run",
               return_value=MagicMock(returncode=1, stdout=b"")):
        text = ocr_image(img)
    assert text == ""


def test_ocr_image_missing_file_returns_empty(tmp_path: Path):
    text = ocr_image(tmp_path / "missing.jpg")
    assert text == ""


def test_ocr_image_tesseract_not_installed_returns_empty(tmp_path: Path):
    img = _img(tmp_path)
    with patch("hermes.ingest.ocr.subprocess.run",
               side_effect=FileNotFoundError("tesseract not found")):
        text = ocr_image(img)
    assert text == ""


def test_ocr_image_timeout_returns_empty(tmp_path: Path):
    img = _img(tmp_path)
    with patch("hermes.ingest.ocr.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="tesseract", timeout=30)):
        text = ocr_image(img)
    assert text == ""


def test_ocr_image_decodes_utf8_with_replacement(tmp_path: Path):
    """Bad bytes shouldn't crash the OCR pipeline."""
    img = _img(tmp_path)
    fake = MagicMock(returncode=0, stdout=b"hello \xff\xfe world")
    with patch("hermes.ingest.ocr.subprocess.run", return_value=fake):
        text = ocr_image(img)
    assert "hello" in text
    assert "world" in text


def test_ocr_images_mixed_success_and_failure(tmp_path: Path):
    a = _img(tmp_path, "a.jpg")
    b = _img(tmp_path, "b.jpg")
    c = _img(tmp_path, "c.jpg")

    def fake_run(cmd, **kwargs):
        target = Path(cmd[1]).name
        if target == "a.jpg":
            return MagicMock(returncode=0, stdout=b"alpha text")
        if target == "b.jpg":
            return MagicMock(returncode=1, stdout=b"")
        return MagicMock(returncode=0, stdout=b"gamma")

    with patch("hermes.ingest.ocr.subprocess.run", side_effect=fake_run):
        texts = ocr_images([a, b, c])

    assert texts == ["alpha text", "", "gamma"]


def test_ocr_images_empty_list_returns_empty():
    assert ocr_images([]) == []
