"""
Unit tests for tool approval prompt formatting helpers:

  - format_tool_arguments() — formats arguments as readable multiline JSON
  - wrap_approval_prompt()  — wraps prompt lines to fit the terminal dialog

Tests cover:
  - Dictionary arguments
  - JSON-encoded string arguments
  - Long argument values without whitespace
  - Preservation of blank lines and all prompt content

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import format_tool_arguments, wrap_approval_prompt

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# format_tool_arguments
# ---------------------------------------------------------------------------

class TestFormatToolArguments:
    """Tests for readable formatting of tool-call arguments."""

    def test_dictionary_is_formatted_as_pretty_json(self):
        formatted = format_tool_arguments(
            {
                "path": "src/minutus/minutus.py",
                "content": "first line\nsecond line",
            }
        )

        assert formatted.startswith("{\n")
        assert '\n  "path":' in formatted
        assert '"content": "first line\\nsecond line"' in formatted

    def test_json_string_is_parsed_and_formatted(self):
        formatted = format_tool_arguments(
            '{"path":"README.md","content":"value"}'
        )

        assert formatted == (
            '{\n  "path": "README.md",\n  "content": "value"\n}'
        )


# ---------------------------------------------------------------------------
# wrap_approval_prompt
# ---------------------------------------------------------------------------

class TestWrapApprovalPrompt:
    """Tests for wrapping complete approval prompts inside the dialog."""

    def test_long_argument_text_is_wrapped_without_content_loss(self):
        long_value = "x" * 120
        prompt = f"Arguments: {long_value}\n\nAllow execution tool call?"

        wrapped = wrap_approval_prompt(prompt, width=40)

        assert "".join(wrapped.split()) == "".join(prompt.split())
        assert all(len(line) <= 36 for line in wrapped.splitlines())
        assert "\n\nAllow execution tool call?" in wrapped
