"""
Unit tests for load_image() — the image-loading utility that reads a file
in binary mode, determines its MIME type from the extension, and returns
base64-encoded data.

Tests cover:
  - Each supported MIME type (png, jpg, jpeg, gif, bmp, tiff, webp, svg)
  - Unknown extension fallback (image/jpeg)
  - Case-insensitive extension matching
  - Base64 encoding correctness
  - Errors: non-existent file, directory path

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import base64
import sys
from pathlib import Path

import pytest
import typer

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import load_image

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _write_image(tmp_path, filename, content=b"\x89PNG\r\n\x1a\n"):
    """Create a temp file with the given name and (binary) content."""
    f = tmp_path / filename
    f.write_bytes(content)
    return str(f)


# ---------------------------------------------------------------------------
# MIME types for each extension
# ---------------------------------------------------------------------------

class TestMimeTypes:
    """Tests that each file extension maps to the correct MIME type."""

    @pytest.mark.parametrize("ext,expected_mime", [
        (".png", "image/png"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".gif", "image/gif"),
        (".bmp", "image/bmp"),
        (".tiff", "image/tiff"),
        (".webp", "image/webp"),
        (".svg", "image/svg+xml"),
    ])
    def test_mime_type_for_extension(self, tmp_path, ext, expected_mime):
        path = _write_image(tmp_path, f"image{ext}")
        _, mime_type = load_image(path)
        assert mime_type == expected_mime

    def test_unknown_extension_defaults_to_jpeg(self, tmp_path):
        path = _write_image(tmp_path, "image.xyz")
        _, mime_type = load_image(path)
        assert mime_type == "image/jpeg"

    def test_no_extension_defaults_to_jpeg(self, tmp_path):
        path = _write_image(tmp_path, "image_no_ext")
        _, mime_type = load_image(path)
        assert mime_type == "image/jpeg"


# ---------------------------------------------------------------------------
# Case-insensitive extension matching
# ---------------------------------------------------------------------------

class TestUpperCaseExtension:
    """Tests that extension matching is case-insensitive."""

    @pytest.mark.parametrize("ext,expected_mime", [
        (".PNG", "image/png"),
        (".JPG", "image/jpeg"),
        (".JPEG", "image/jpeg"),
        (".GIF", "image/gif"),
        (".WEBP", "image/webp"),
        (".SVG", "image/svg+xml"),
    ])
    def test_uppercase_ext(self, tmp_path, ext, expected_mime):
        path = _write_image(tmp_path, f"IMAGE{ext}")
        _, mime_type = load_image(path)
        assert mime_type == expected_mime

    def test_mixed_case_ext(self, tmp_path):
        path = _write_image(tmp_path, "image.PnG")
        _, mime_type = load_image(path)
        assert mime_type == "image/png"


# ---------------------------------------------------------------------------
# Base64 encoding correctness
# ---------------------------------------------------------------------------

class TestBase64Encoding:
    """Tests that the returned base64 data correctly decodes to the original bytes."""

    def test_base64_decodes_to_original_bytes(self, tmp_path):
        original = b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff"
        path = _write_image(tmp_path, "test.png", content=original)
        b64_data, _ = load_image(path)
        assert base64.b64decode(b64_data) == original

    def test_base64_is_utf8_string(self, tmp_path):
        path = _write_image(tmp_path, "test.png", content=b"hello")
        b64_data, _ = load_image(path)
        assert isinstance(b64_data, str)
        # Decoding the base64 string should give back the original
        assert base64.b64decode(b64_data.encode("utf-8")) == b"hello"

    def test_empty_file_base64(self, tmp_path):
        path = _write_image(tmp_path, "empty.png", content=b"")
        b64_data, _ = load_image(path)
        assert base64.b64decode(b64_data) == b""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TestErrors:
    """Tests for error conditions."""

    def test_nonexistent_file_raises_typer_exit(self, tmp_path):
        with pytest.raises(typer.Exit) as exc_info:
            load_image(str(tmp_path / "no_such.png"))
        assert exc_info.value.exit_code != 0

    def test_directory_raises_typer_exit(self, tmp_path):
        # tmp_path itself is a directory, not a file
        with pytest.raises(typer.Exit) as exc_info:
            load_image(str(tmp_path))
        assert exc_info.value.exit_code != 0
