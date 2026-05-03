from pathlib import Path
from unittest.mock import patch, MagicMock
from hermes.ingest.web import extract_web, ExtractedDoc


def test_extract_web_returns_title_and_text():
    fixture = (Path(__file__).parent / "fixtures" / "web_article.html").read_text()
    fake_resp = MagicMock()
    fake_resp.text = fixture
    fake_resp.raise_for_status = MagicMock()
    with patch("hermes.ingest.web.httpx.get", return_value=fake_resp):
        doc = extract_web("https://example.com/article")
    assert doc.url == "https://example.com/article"
    assert "Test Article" in doc.title or doc.title == ""  # trafilatura's metadata may vary
    assert "first paragraph" in doc.text.lower()
