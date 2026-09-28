"""
Unit tests for parse_document() HTML -> Markdown conversion.

These pin down the markdownify behavior that replaced html2text, so a
regression to a plain-text stripper (or a change of markdownify options)
fails loudly. The converted Markdown is fed to the model, so these document
the shape of the context that HTML sources contribute.

Tests cover:
  - Headings become Markdown (setext) headings
  - Links are preserved (not stripped); bare URLs autolink
  - Images become Markdown images (previously dropped by html2text)
  - Bold / emphasis become Markdown markup
  - Plain paragraph text still comes through readably

All tests are marked 'unit' so they can be run without an API key:
    pytest -m unit
"""

import pytest

from minutus.minutus import parse_document

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------

class TestHeadings:
    """HTML headings should survive as Markdown headings.

    markdownify's default heading_style is UNDERLINED (setext), so <h1> is
    underlined with '=' and <h2> with '-'.
    """

    def test_html_heading_becomes_markdown_heading(self):
        result = parse_document(b"<h1>Title</h1>", "page.html")
        assert result == "Title\n====="

    def test_second_level_heading_uses_dash_underline(self):
        result = parse_document(b"<h2>Subtitle</h2>", "page.html")
        assert result == "Subtitle\n" + "-" * len("Subtitle")


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

class TestLinks:
    """Links used to be kept (ignore_links = False); that must not regress."""

    def test_link_is_preserved(self):
        html = b'<p><a href="http://example.com">Example</a></p>'
        result = parse_document(html, "page.html")
        assert result == "[Example](http://example.com)"

    def test_bare_url_autolinks(self):
        # autolinks is enabled by default: when the anchor text equals the
        # href, the shortcut <url> syntax is used.
        html = b'<p><a href="http://example.com">http://example.com</a></p>'
        result = parse_document(html, "page.html")
        assert result == "<http://example.com>"


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

class TestImages:
    """Behavior change: html2text dropped images; markdownify emits them."""

    def test_image_becomes_markdown_image(self):
        html = b'<img src="pic.png" alt="Alt text">'
        result = parse_document(html, "page.html")
        assert result == "![Alt text](pic.png)"

    def test_image_inside_paragraph_is_inlined(self):
        html = b'<p>Before <img src="pic.png" alt="Alt text"> After</p>'
        result = parse_document(html, "page.html")
        assert "![Alt text](pic.png)" in result
        assert "Before" in result
        assert "After" in result


# ---------------------------------------------------------------------------
# Emphasis
# ---------------------------------------------------------------------------

class TestEmphasis:
    """Inline emphasis should be converted to Markdown markup."""

    def test_bold_becomes_strong(self):
        result = parse_document(b"<p><b>Yay</b></p>", "page.html")
        assert result == "**Yay**"

    def test_italic_becomes_emphasis(self):
        result = parse_document(b"<p><em>Wow</em></p>", "page.html")
        assert result == "*Wow*"


# ---------------------------------------------------------------------------
# Plain text sanity
# ---------------------------------------------------------------------------

class TestPlainText:
    """Ordinary content must still come through readably / unchanged."""

    def test_paragraph_text_is_preserved(self):
        html = b"<html><body><h1>Title</h1><p>Hello world</p></body></html>"
        result = parse_document(html, "page.html")
        assert "Title" in result
        assert "Hello world" in result

    def test_html_content_type_routes_through_converter(self):
        html = b"<p><b>Bold</b></p>"
        result = parse_document(html, "page", content_type="text/html")
        assert result == "**Bold**"

    def test_plain_text_is_decoded_unchanged(self):
        # Non-HTML content should not be routed through the converter.
        result = parse_document(b"just some text", "notes.txt")
        assert result == "just some text"
