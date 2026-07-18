"""
Unit tests for prepare_message_content() — assembles the final message
content by combining prompt, file context, clipboard context, and
optionally an image.

Tests cover:
  - Prompt only (returns str)
  - Prompt + file context (returns str)
  - Prompt + clipboard (returns str)
  - Prompt + file context + clipboard (returns str)
  - Prompt + image (returns list with text + image dicts)
  - All combined (file + clipboard + image → list)
  - Return type verification

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import base64
import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import prepare_message_content

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Prompt only
# ---------------------------------------------------------------------------

class TestPromptOnly:
    """Tests with just a prompt, no context or image."""

    def test_prompt_only_returns_string(self):
        result = prepare_message_content("hello", "", None, None)
        # extended_context is "", then prompt = "" + "\n\n" + "hello" = "\n\nhello"
        assert result == "\n\nhello"

    def test_prompt_only_is_str_type(self):
        result = prepare_message_content("hello", "", None, None)
        assert isinstance(result, str)

    def test_empty_prompt_only(self):
        result = prepare_message_content("", "", None, None)
        # extended_context is "", so prompt = "" + "\n\n" + "" = "\n\n"
        assert result == "\n\n"


# ---------------------------------------------------------------------------
# Prompt + file context
# ---------------------------------------------------------------------------

class TestPromptWithFileContext:
    """Tests with prompt and file context."""

    def test_prompt_with_file_context(self):
        result = prepare_message_content("question", "file_ctx", None, None)
        assert result == "file_ctx\n\nquestion"

    def test_prompt_with_file_context_is_str(self):
        result = prepare_message_content("question", "file_ctx", None, None)
        assert isinstance(result, str)

    def test_file_context_is_included_once(self):
        result = prepare_message_content("question", "file_ctx", None, None)
        assert result.count("file_ctx") == 1

    def test_prompt_with_multiline_file_context(self):
        result = prepare_message_content("q", "line1\nline2", None, None)
        assert result == "line1\nline2\n\nq"


# ---------------------------------------------------------------------------
# Prompt + clipboard
# ---------------------------------------------------------------------------

class TestPromptWithClipboard:
    """Tests with prompt and clipboard context (no file context)."""

    def test_prompt_with_clipboard_no_file_context(self):
        # When file_context is "" (falsy), extended_context stays "".
        # Then clipboard is appended: "" + "\n\n" + "clip_ctx" = "\n\nclip_ctx"
        # Then prompt = "\n\nclip_ctx" + "\n\n" + "question" = "\n\nclip_ctx\n\nquestion"
        result = prepare_message_content("question", "", None, "clip_ctx")
        assert result == "\n\nclip_ctx\n\nquestion"

    def test_prompt_with_clipboard_no_file_context_is_str(self):
        result = prepare_message_content("question", "", None, "clip_ctx")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Prompt + file context + clipboard
# ---------------------------------------------------------------------------

class TestPromptWithFileAndClipboard:
    """Tests with prompt, file context, and clipboard context."""

    def test_prompt_with_file_and_clipboard(self):
        result = prepare_message_content("question", "file_ctx", None, "clip_ctx")
        # extended_context = "file_ctx", then "file_ctx\n\nclip_ctx"
        # prompt = "file_ctx\n\nclip_ctx\n\nquestion"
        assert result == "file_ctx\n\nclip_ctx\n\nquestion"

    def test_prompt_with_file_and_clipboard_is_str(self):
        result = prepare_message_content("question", "file_ctx", None, "clip_ctx")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Prompt + image
# ---------------------------------------------------------------------------

class TestPromptWithImage:
    """Tests with prompt and an image path."""

    def test_prompt_with_image_returns_list(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        result = prepare_message_content("describe", "", str(img), None)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_prompt_with_image_text_element(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        result = prepare_message_content("describe", "", str(img), None)
        assert result[0]["type"] == "text"
        # extended_context is "", then prompt = "" + "\n\n" + "describe" = "\n\ndescribe"
        assert result[0]["text"] == "\n\ndescribe"

    def test_prompt_with_image_image_element(self, tmp_path):
        original = b"\x89PNG\r\n\x1a\n\x00\x01\x02\xff"
        img = tmp_path / "test.png"
        img.write_bytes(original)
        result = prepare_message_content("describe", "", str(img), None)
        assert result[1]["type"] == "image"
        assert result[1]["mime_type"] == "image/png"
        assert base64.b64decode(result[1]["base64"]) == original

    def test_prompt_with_file_context_and_image(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        result = prepare_message_content("describe", "file_ctx", str(img), None)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "file_ctx\n\ndescribe"
        assert result[1]["type"] == "image"


# ---------------------------------------------------------------------------
# All combined
# ---------------------------------------------------------------------------

class TestAllCombined:
    """Tests with file context, clipboard, and image all present."""

    def test_all_combined_returns_list(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        result = prepare_message_content(
            "describe", "file_ctx", str(img), "clip_ctx"
        )
        assert isinstance(result, list)
        assert len(result) == 2

    def test_all_combined_text_element(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        result = prepare_message_content(
            "describe", "file_ctx", str(img), "clip_ctx"
        )
        assert result[0]["type"] == "text"
        # extended_context = "file_ctx", then "file_ctx\n\nclip_ctx"
        # prompt = "file_ctx\n\nclip_ctx\n\ndescribe"
        assert result[0]["text"] == "file_ctx\n\nclip_ctx\n\ndescribe"

    def test_all_combined_image_element(self, tmp_path):
        original = b"\x89PNG\r\n\x1a\n\xff"
        img = tmp_path / "test.png"
        img.write_bytes(original)
        result = prepare_message_content(
            "describe", "file_ctx", str(img), "clip_ctx"
        )
        assert result[1]["type"] == "image"
        assert result[1]["mime_type"] == "image/png"
        assert base64.b64decode(result[1]["base64"]) == original


# ---------------------------------------------------------------------------
# Return type verification
# ---------------------------------------------------------------------------

class TestReturnTypes:
    """Verify return types for all combinations."""

    def test_without_image_returns_str(self, tmp_path):
        result = prepare_message_content("q", "ctx", None, "clip")
        assert isinstance(result, str)

    def test_with_image_returns_list(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"data")
        result = prepare_message_content("q", "ctx", str(img), "clip")
        assert isinstance(result, list)

    def test_with_image_no_context_returns_list(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"data")
        result = prepare_message_content("q", "", str(img), None)
        assert isinstance(result, list)

    def test_without_image_no_context_returns_str(self):
        result = prepare_message_content("q", "", None, None)
        assert isinstance(result, str)
