"""Unit tests for the compact tool-argument preview helper.

``summarize_tool_arguments()`` renders tool-call arguments as a single,
length-capped diagnostics string. These tests cover the formatting rules,
value truncation, multiline collapsing, and malformed input.

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import summarize_tool_arguments

pytestmark = pytest.mark.unit


class TestNoArguments:
    """Tools that take no arguments produce an empty signature."""

    @pytest.mark.parametrize("value", [None, "", {}])
    def test_empty_variants_render_as_empty_parentheses(self, value):
        assert summarize_tool_arguments(value) == "()"


class TestFormatting:
    """Values are quoted, keys are not, and the output is one line."""

    def test_string_values_are_quoted(self):
        assert summarize_tool_arguments({"path": "README.md"}) == (
            '(path="README.md")'
        )

    def test_non_string_values_use_json_tokens(self):
        result = summarize_tool_arguments(
            {"start_line": 10, "regex": True, "end_line": None}
        )

        assert result == "(start_line=10, regex=true, end_line=null)"

    def test_keys_are_shown_in_full(self):
        long_key = "a_very_long_parameter_name_that_exceeds_the_value_limit"

        assert summarize_tool_arguments({long_key: 1}) == f"({long_key}=1)"

    def test_argument_order_is_preserved(self):
        result = summarize_tool_arguments({"b": 1, "a": 2})

        assert result == "(b=1, a=2)"

    def test_json_string_arguments_are_parsed(self):
        result = summarize_tool_arguments('{"path": "README.md", "regex": false}')

        assert result == '(path="README.md", regex=false)'


class TestTruncation:
    """Long values are capped and marked; short values are untouched."""

    def test_long_value_is_truncated_with_ellipsis(self):
        result = summarize_tool_arguments({"path": "x" * 80})

        assert result == "(path=" + '"' + "x" * 40 + "\u2026\")"

    def test_value_at_the_limit_is_not_truncated(self):
        result = summarize_tool_arguments({"path": "y" * 40})

        assert result == '(path="' + "y" * 40 + '")'
        assert "\u2026" not in result

    def test_custom_limit_is_honored(self):
        result = summarize_tool_arguments({"path": "abcdef"}, max_chars=3)

        assert result == '(path="abc\u2026")'


class TestSingleLine:
    """Multiline and whitespace-heavy values collapse onto one line."""

    def test_newlines_are_collapsed(self):
        result = summarize_tool_arguments({"content": "line1\nline2\nline3"})

        assert "\n" not in result
        assert result == '(content="line1 line2 line3")'

    def test_whitespace_runs_are_collapsed(self):
        result = summarize_tool_arguments({"cmd": "echo   a\t\tb"})

        assert result == '(cmd="echo a b")'


class TestFallbacks:
    """Malformed or unexpected inputs degrade without raising."""

    def test_invalid_json_string_is_treated_as_a_plain_value(self):
        result = summarize_tool_arguments("not json {")

        assert result == '("not json {")'

    def test_nested_structures_fall_back_to_compact_json(self):
        result = summarize_tool_arguments({"opts": {"a": 1}})

        assert result.startswith("(opts=")

    def test_non_serializable_value_does_not_raise(self):
        result = summarize_tool_arguments({"obj": object()})

        assert result.startswith("(obj=")
