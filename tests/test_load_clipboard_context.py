"""
Unit tests for load_clipboard_context() — reads from the system clipboard
via pyperclip.paste() and wraps the content in a markdown code block.

Tests cover:
  - Normal clipboard content
  - Multi-line clipboard content
  - Empty clipboard
  - Error handling (pyperclip raises an exception)

All tests mock pyperclip.paste via monkeypatch — no real clipboard needed.
All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import sys
from pathlib import Path

import pytest
import typer

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import minutus.minutus as minutus_mod
from minutus.minutus import load_clipboard_context

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Clipboard content
# ---------------------------------------------------------------------------

class TestClipboardContent:
    """Tests for normal clipboard content formatting."""

    def test_normal_clipboard_content(self, monkeypatch):
        monkeypatch.setattr(minutus_mod.pyperclip, "paste", lambda: "clipboard text")
        result = load_clipboard_context()
        assert result == "```clipboard\nclipboard text\n```"

    def test_multiline_clipboard_content(self, monkeypatch):
        monkeypatch.setattr(
            minutus_mod.pyperclip, "paste", lambda: "line1\nline2\nline3"
        )
        result = load_clipboard_context()
        assert result == "```clipboard\nline1\nline2\nline3\n```"

    def test_empty_clipboard_content(self, monkeypatch):
        monkeypatch.setattr(minutus_mod.pyperclip, "paste", lambda: "")
        result = load_clipboard_context()
        assert result == "```clipboard\n\n```"

    def test_unicode_clipboard_content(self, monkeypatch):
        monkeypatch.setattr(minutus_mod.pyperclip, "paste", lambda: "héllo wörld 🎉")
        result = load_clipboard_context()
        assert "héllo wörld 🎉" in result
        assert result == "```clipboard\nhéllo wörld 🎉\n```"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Tests for error handling when pyperclip fails."""

    def test_pyperclip_exception_raises_typer_exit(self, monkeypatch):
        def raise_error():
            raise Exception("clipboard not available")

        monkeypatch.setattr(minutus_mod.pyperclip, "paste", raise_error)
        with pytest.raises(typer.Exit) as exc_info:
            load_clipboard_context()
        assert exc_info.value.exit_code != 0

    def test_pyperclip_permission_error_raises_typer_exit(self, monkeypatch):
        def raise_perm_error():
            raise PermissionError("Access denied")

        monkeypatch.setattr(minutus_mod.pyperclip, "paste", raise_perm_error)
        with pytest.raises(typer.Exit) as exc_info:
            load_clipboard_context()
        assert exc_info.value.exit_code != 0
