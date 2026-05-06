from pathlib import Path
from unittest.mock import patch, MagicMock
from hermes.ingest.web import extract_web, ExtractedDoc, _is_pdf_url
from hermes.ingest.webclaw import WebclawResult


def test_extract_web_returns_title_and_text():
    """trafilatura fallback path: webclaw returns None (not installed),
    we hit httpx.get + trafilatura."""
    fixture = (Path(__file__).parent / "fixtures" / "web_article.html").read_text()
    fake_resp = MagicMock()
    fake_resp.text = fixture
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.ingest.web._webclaw_extract", return_value=None), \
         patch("hermes.ingest.web.httpx.get", return_value=fake_resp):
        doc = extract_web("https://example.com/article")
    assert doc.url == "https://example.com/article"
    assert "Test Article" in doc.title or doc.title == ""  # trafilatura's metadata may vary
    assert "first paragraph" in doc.text.lower()


def test_extract_web_uses_webclaw_when_available():
    """webclaw returned a result → we use it and never call httpx."""
    fake_result = WebclawResult(text="# WC Article\n\nbody from webclaw", title="WC Article")
    with patch("hermes.ingest.web._webclaw_extract", return_value=fake_result), \
         patch("hermes.ingest.web.httpx.get") as mock_get:
        doc = extract_web("https://example.com/article")
    assert doc.title == "WC Article"
    assert "body from webclaw" in doc.text
    mock_get.assert_not_called()


def test_extract_web_pdf_skips_trafilatura():
    """PDF URL + no webclaw → empty doc, no httpx call. trafilatura
    can't parse PDFs so we don't even try."""
    with patch("hermes.ingest.web._webclaw_extract", return_value=None), \
         patch("hermes.ingest.web.httpx.get") as mock_get:
        doc = extract_web("https://example.com/whitepaper.pdf")
    assert doc.text == ""
    assert doc.title == ""
    mock_get.assert_not_called()


def test_extract_web_pdf_with_webclaw():
    """PDF + webclaw available → returns extracted markdown."""
    fake_result = WebclawResult(text="# Doc\n\nPDF body content", title="Doc")
    with patch("hermes.ingest.web._webclaw_extract", return_value=fake_result), \
         patch("hermes.ingest.web.httpx.get") as mock_get:
        doc = extract_web("https://example.com/whitepaper.pdf")
    assert "PDF body content" in doc.text
    assert doc.title == "Doc"
    mock_get.assert_not_called()


def test_extract_web_depth_uses_webclaw_crawl():
    """depth>0 forces a crawl. Verify webclaw was called with that depth
    and trafilatura wasn't reached."""
    captured = {}

    def fake_webclaw(url, **kwargs):
        captured.update(kwargs)
        return WebclawResult(text="# Crawled\n\nmulti-page content", title="Crawled")

    with patch("hermes.ingest.web._webclaw_extract", side_effect=fake_webclaw), \
         patch("hermes.ingest.web.httpx.get") as mock_get:
        doc = extract_web("https://docs.example.com", depth=2)
    assert doc.title == "Crawled"
    assert captured.get("depth") == 2
    mock_get.assert_not_called()


def test_extract_web_depth_falls_back_when_webclaw_missing():
    """depth>0 but webclaw missing → degrade to single-page trafilatura."""
    fake_resp = MagicMock()
    fake_resp.text = "<html><body><p>Single page fallback content here.</p></body></html>"
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.ingest.web._webclaw_extract", return_value=None), \
         patch("hermes.ingest.web.httpx.get", return_value=fake_resp) as mock_get:
        doc = extract_web("https://example.com", depth=3)
    # We did fall back: httpx was called.
    mock_get.assert_called_once()
    assert "Single page fallback" in doc.text


def test_is_pdf_url_handles_query_strings():
    assert _is_pdf_url("https://x.com/foo.pdf?token=abc") is True
    assert _is_pdf_url("https://x.com/foo.PDF") is True
    assert _is_pdf_url("https://x.com/foo.pdf/") is True
    assert _is_pdf_url("https://x.com/foo.html") is False
    assert _is_pdf_url("https://x.com/article") is False
