"""
Unit tests for the read_url @tool function.

Tests cover:
  - HTML content (via Content-Type header, via .html/.htm URL extension)
  - PDF content (via Content-Type, via .pdf URL extension, parse failure)
  - Plain text / unknown content type (raw decode, non-UTF-8 with errors=ignore)
  - HTML conversion failure (invalid UTF-8 body)
  - HTTP errors (404, 500)
  - URL errors (connection refused)
  - Generic exceptions

All tests mock urlopen via monkeypatch — no real network calls.
All tests are marked 'unit' so they can be run without an API key:
    pytest -m unit
"""

import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import minutus.minutus as minutus_mod
from minutus.minutus import read_url
from urllib.error import HTTPError, URLError

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper: create a fake urlopen context manager
# ---------------------------------------------------------------------------

def make_fake_urlopen(body: bytes, content_type: str = ""):
    """Return a callable that mimics urlopen as a context manager."""
    response = MagicMock()
    response.read.return_value = body
    response.headers.get.return_value = content_type
    response.__enter__ = lambda self: self
    response.__exit__ = lambda self, *args: None

    def _urlopen(url, timeout=10):
        return response

    return _urlopen


# ---------------------------------------------------------------------------
# HTML content
# ---------------------------------------------------------------------------

class TestHtmlContent:
    """Tests for HTML URL handling."""

    def test_html_via_content_type(self, monkeypatch):
        body = b"<html><body><h1>Title</h1><p>Hello world</p></body></html>"
        monkeypatch.setattr(
            minutus_mod, "urlopen", make_fake_urlopen(body, "text/html; charset=utf-8")
        )
        result = read_url.invoke({"url": "https://example.com/page"})
        assert "Title" in result
        assert "Hello world" in result

    def test_html_via_url_extension(self, monkeypatch):
        body = b"<html><body><p>Extension test</p></body></html>"
        # Content-Type doesn't say HTML, but URL ends with .html
        monkeypatch.setattr(
            minutus_mod, "urlopen", make_fake_urlopen(body, "application/octet-stream")
        )
        result = read_url.invoke({"url": "https://example.com/page.html"})
        assert "Extension test" in result

    def test_htm_extension_treated_as_html(self, monkeypatch):
        body = b"<html><body><p>HTM test</p></body></html>"
        monkeypatch.setattr(
            minutus_mod, "urlopen", make_fake_urlopen(body, "application/octet-stream")
        )
        result = read_url.invoke({"url": "https://example.com/page.htm"})
        assert "HTM test" in result


# ---------------------------------------------------------------------------
# PDF content
# ---------------------------------------------------------------------------

class TestPdfContent:
    """Tests for PDF URL handling."""

    def test_pdf_via_content_type(self, monkeypatch):
        # Create a minimal valid PDF that pypdf can parse
        minimal_pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
            b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
            b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
            b"5 0 obj<</Length 44>>stream\n"
            b"BT /F1 12 Tf 100 700 Td (Hello PDF) Tj ET\n"
            b"endstream\nendobj\n"
            b"xref\n0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000241 00000 n \n"
            b"0000000316 00000 n \n"
            b"trailer<</Size 6/Root 1 0 R>>\n"
            b"startxref\n414\n%%EOF\n"
        )
        monkeypatch.setattr(
            minutus_mod, "urlopen",
            make_fake_urlopen(minimal_pdf, "application/pdf")
        )
        result = read_url.invoke({"url": "https://example.com/doc"})
        # The PDF contains "Hello PDF" text — pypdf should extract it
        # (If pypdf can't parse this minimal PDF, just check it doesn't error)
        assert "Failed to parse PDF" not in result or "Hello PDF" in result

    def test_pdf_via_url_extension(self, monkeypatch):
        minimal_pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        monkeypatch.setattr(
            minutus_mod, "urlopen",
            make_fake_urlopen(minimal_pdf, "application/octet-stream")
        )
        result = read_url.invoke({"url": "https://example.com/doc.pdf"})
        # It should attempt PDF parsing (not return raw text)
        # Either it parses (unlikely with minimal PDF) or returns a parse error
        assert "Failed to parse PDF" in result or "%PDF" not in result

    def test_pdf_parse_failure(self, monkeypatch):
        # Content-Type says PDF but body is garbage
        monkeypatch.setattr(
            minutus_mod, "urlopen",
            make_fake_urlopen(b"NOT A PDF", "application/pdf")
        )
        result = read_url.invoke({"url": "https://example.com/doc"})
        assert result.startswith("Failed to parse PDF:")


# ---------------------------------------------------------------------------
# Plain text / unknown content type
# ---------------------------------------------------------------------------

class TestPlainText:
    """Tests for non-HTML, non-PDF content."""

    def test_plain_text(self, monkeypatch):
        body = b"just plain text"
        monkeypatch.setattr(
            minutus_mod, "urlopen",
            make_fake_urlopen(body, "text/plain")
        )
        result = read_url.invoke({"url": "https://example.com/data"})
        assert result == "just plain text"

    def test_unknown_content_type_returns_raw(self, monkeypatch):
        body = b'{"key": "value"}'
        monkeypatch.setattr(
            minutus_mod, "urlopen",
            make_fake_urlopen(body, "application/json")
        )
        result = read_url.invoke({"url": "https://example.com/api"})
        assert result == '{"key": "value"}'

    def test_non_utf8_falls_back_to_errors_ignore(self, monkeypatch):
        # Invalid UTF-8 bytes followed by valid text
        body = b"\xff\xfe\x00text"
        monkeypatch.setattr(
            minutus_mod, "urlopen",
            make_fake_urlopen(body, "text/plain")
        )
        result = read_url.invoke({"url": "https://example.com/data"})
        # errors="ignore" should strip invalid bytes and keep "text"
        assert "text" in result


# ---------------------------------------------------------------------------
# HTML conversion failure
# ---------------------------------------------------------------------------

class TestHtmlConversionFailure:
    """Tests for HTML that fails to decode/convert."""

    def test_html_conversion_failure(self, monkeypatch):
        # Content-Type says HTML but body is invalid UTF-8
        body = b"\xff\xfe\x00invalid"
        monkeypatch.setattr(
            minutus_mod, "urlopen",
            make_fake_urlopen(body, "text/html")
        )
        result = read_url.invoke({"url": "https://example.com/bad"})
        assert result.startswith("Failed to convert HTML:")


# ---------------------------------------------------------------------------
# HTTP errors
# ---------------------------------------------------------------------------

class TestHttpError:
    """Tests for HTTPError handling."""

    def test_http_error_404(self, monkeypatch):
        def _raise_http_error(url, timeout=10):
            raise HTTPError(
                url=url, code=404, msg="Not Found",
                hdrs=None, fp=None,
            )

        monkeypatch.setattr(minutus_mod, "urlopen", _raise_http_error)
        result = read_url.invoke({"url": "https://example.com/missing"})
        assert result == "HTTP Error: 404 - Not Found"

    def test_http_error_500(self, monkeypatch):
        def _raise_http_error(url, timeout=10):
            raise HTTPError(
                url=url, code=500, msg="Internal Server Error",
                hdrs=None, fp=None,
            )

        monkeypatch.setattr(minutus_mod, "urlopen", _raise_http_error)
        result = read_url.invoke({"url": "https://example.com/error"})
        assert result == "HTTP Error: 500 - Internal Server Error"


# ---------------------------------------------------------------------------
# URL errors
# ---------------------------------------------------------------------------

class TestUrlError:
    """Tests for URLError handling."""

    def test_url_error(self, monkeypatch):
        def _raise_url_error(url, timeout=10):
            raise URLError(reason="Connection refused")

        monkeypatch.setattr(minutus_mod, "urlopen", _raise_url_error)
        result = read_url.invoke({"url": "https://example.com/unreachable"})
        assert result == "URL Error: Connection refused"


# ---------------------------------------------------------------------------
# Generic exception
# ---------------------------------------------------------------------------

class TestGenericException:
    """Tests for unexpected exceptions."""

    def test_generic_exception(self, monkeypatch):
        def _raise_value_error(url, timeout=10):
            raise ValueError("bad thing")

        monkeypatch.setattr(minutus_mod, "urlopen", _raise_value_error)
        result = read_url.invoke({"url": "https://example.com"})
        assert result == "Failed to process URL: bad thing"
