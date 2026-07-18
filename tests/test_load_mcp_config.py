"""
Unit tests for three closely-related file-loading utility functions:

  - load_mcp_config(config_path)  — loads JSON from a file as a dict
  - load_json_schema(schema_path) — loads JSON schema, injects default title
  - load_file_content(file_path)  — reads raw text from a file

Tests cover:
  - Valid JSON / text input
  - Invalid JSON
  - Missing files
  - Title injection logic for load_json_schema
  - Empty files
  - Unicode content

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import json
import sys
from pathlib import Path

import pytest
import typer

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import load_mcp_config, load_json_schema, load_file_content

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# load_mcp_config
# ---------------------------------------------------------------------------

class TestLoadMcpConfig:
    """Tests for load_mcp_config()."""

    def test_valid_json_config(self, tmp_path):
        config = {"server": {"transport": "stdio", "command": "python"}}
        f = tmp_path / "mcp.json"
        f.write_text(json.dumps(config))
        result = load_mcp_config(str(f))
        assert result == config

    def test_valid_json_with_multiple_servers(self, tmp_path):
        config = {
            "time": {"transport": "stdio", "command": "python", "args": ["-m", "mcp_server_time"]},
            "db": {"transport": "stdio", "command": "python", "args": ["server.py"]},
        }
        f = tmp_path / "mcp.json"
        f.write_text(json.dumps(config))
        result = load_mcp_config(str(f))
        assert result == config
        assert "time" in result
        assert "db" in result

    def test_invalid_json_raises_typer_exit(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json")
        with pytest.raises(typer.Exit) as exc_info:
            load_mcp_config(str(f))
        assert exc_info.value.exit_code != 0

    def test_nonexistent_file_raises_typer_exit(self, tmp_path):
        with pytest.raises(typer.Exit) as exc_info:
            load_mcp_config(str(tmp_path / "no_such.json"))
        assert exc_info.value.exit_code != 0


# ---------------------------------------------------------------------------
# load_json_schema
# ---------------------------------------------------------------------------

class TestLoadJsonSchema:
    """Tests for load_json_schema(), including title injection logic."""

    def test_valid_schema_with_title_preserved(self, tmp_path):
        schema = {"type": "object", "title": "my_schema", "properties": {}}
        f = tmp_path / "schema.json"
        f.write_text(json.dumps(schema))
        result = load_json_schema(str(f))
        assert result == schema
        assert result["title"] == "my_schema"

    def test_valid_schema_without_title_gets_default(self, tmp_path):
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        f = tmp_path / "schema.json"
        f.write_text(json.dumps(schema))
        result = load_json_schema(str(f))
        assert result["title"] == "structured_output"
        # Other keys preserved
        assert result["type"] == "object"
        assert "properties" in result

    def test_schema_with_empty_title_preserved(self, tmp_path):
        """A schema with title="" should not get the default injected."""
        schema = {"type": "object", "title": "", "properties": {}}
        f = tmp_path / "schema.json"
        f.write_text(json.dumps(schema))
        result = load_json_schema(str(f))
        assert result["title"] == ""

    def test_json_array_returned_as_is(self, tmp_path):
        """A JSON array is not a dict, so no title is injected."""
        f = tmp_path / "schema.json"
        f.write_text(json.dumps([1, 2, 3]))
        result = load_json_schema(str(f))
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_invalid_json_raises_typer_exit(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json at all")
        with pytest.raises(typer.Exit) as exc_info:
            load_json_schema(str(f))
        assert exc_info.value.exit_code != 0

    def test_nonexistent_file_raises_typer_exit(self, tmp_path):
        with pytest.raises(typer.Exit) as exc_info:
            load_json_schema(str(tmp_path / "no_such.json"))
        assert exc_info.value.exit_code != 0


# ---------------------------------------------------------------------------
# load_file_content
# ---------------------------------------------------------------------------

class TestLoadFileContent:
    """Tests for load_file_content()."""

    def test_valid_text_file(self, tmp_path):
        f = tmp_path / "prompt.txt"
        f.write_text("Hello, world!")
        result = load_file_content(str(f))
        assert result == "Hello, world!"

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = load_file_content(str(f))
        assert result == ""

    def test_multiline_file(self, tmp_path):
        f = tmp_path / "multi.txt"
        f.write_text("line1\nline2\nline3\n")
        result = load_file_content(str(f))
        assert result == "line1\nline2\nline3\n"

    def test_unicode_content(self, tmp_path):
        f = tmp_path / "unicode.txt"
        f.write_text("héllo wörld 🎉")
        result = load_file_content(str(f))
        assert result == "héllo wörld 🎉"

    def test_nonexistent_file_raises_typer_exit(self, tmp_path):
        with pytest.raises(typer.Exit) as exc_info:
            load_file_content(str(tmp_path / "no_such.txt"))
        assert exc_info.value.exit_code != 0

    def test_directory_raises_typer_exit(self, tmp_path):
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        with pytest.raises(typer.Exit) as exc_info:
            load_file_content(str(subdir))
        assert exc_info.value.exit_code != 0
