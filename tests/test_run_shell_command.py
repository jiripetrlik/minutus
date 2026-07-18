"""
Unit tests for the run_shell_command @tool function.

Tests cover:
  - Successful commands (stdout capture, stderr-only, both, neither)
  - Failed commands (non-zero exit code, output still shown)
  - Timeout (mocked subprocess.run)
  - FileNotFoundError (mocked subprocess.run)
  - Generic exception (mocked subprocess.run)

All tests are pure unit tests — no API calls, no network.  Real subprocess
calls are used for success/failure tests (fast commands like echo, true,
false).  Error-path tests use monkeypatch to inject exceptions.

All tests are marked 'unit' so they can be run without an API key:
    pytest -m unit
"""

import subprocess
import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import minutus.minutus as minutus_mod
from minutus.minutus import run_shell_command

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Successful commands
# ---------------------------------------------------------------------------

class TestSuccessfulCommand:
    """Tests for commands that exit with code 0."""

    def test_simple_echo(self):
        result = run_shell_command.invoke({"command": "echo hello"})
        assert "Command executed successfully." in result
        assert "STDOUT:" in result
        assert "hello" in result

    def test_stdout_capture(self):
        result = run_shell_command.invoke({"command": "echo -n test123"})
        assert "test123" in result
        assert "Command executed successfully." in result

    def test_command_with_stderr_only(self):
        result = run_shell_command.invoke({"command": "echo err >&2"})
        assert "STDERR:" in result
        assert "err" in result
        # No STDOUT section since stdout is empty
        assert "STDOUT:" not in result

    def test_command_with_both_stdout_stderr(self):
        result = run_shell_command.invoke({
            "command": 'echo "out" && echo "err" >&2'
        })
        assert "STDOUT:" in result
        assert "out" in result
        assert "STDERR:" in result
        assert "err" in result

    def test_command_with_no_stdout_no_stderr(self):
        result = run_shell_command.invoke({"command": "true"})
        assert "Command executed successfully." in result
        assert "STDOUT:" not in result
        assert "STDERR:" not in result


# ---------------------------------------------------------------------------
# Failed commands
# ---------------------------------------------------------------------------

class TestFailedCommand:
    """Tests for commands that exit with non-zero code."""

    def test_nonzero_exit_code(self):
        result = run_shell_command.invoke({"command": "false"})
        assert "Command failed with exit code 1." in result

    def test_failed_command_shows_output(self):
        result = run_shell_command.invoke({
            "command": 'echo "partial" && exit 5'
        })
        assert "Command failed with exit code 5." in result
        assert "partial" in result


# ---------------------------------------------------------------------------
# Timeout (mocked)
# ---------------------------------------------------------------------------

class TestTimeout:
    """Tests for command timeout handling."""

    def test_timeout_returns_error_message(self, monkeypatch):
        def _raise_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="sleep 999", timeout=30)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        result = run_shell_command.invoke({"command": "sleep 999"})
        assert result == "Error: Command timed out after 30 seconds."


# ---------------------------------------------------------------------------
# FileNotFoundError (mocked)
# ---------------------------------------------------------------------------

class TestFileNotFoundError:
    """Tests for missing shell binary."""

    def test_file_not_found_error(self, monkeypatch):
        def _raise_fnf(*args, **kwargs):
            raise FileNotFoundError("No such file or directory")

        monkeypatch.setattr(subprocess, "run", _raise_fnf)
        result = run_shell_command.invoke({"command": "nonexistent_cmd"})
        assert result == "Error: System shell not found."


# ---------------------------------------------------------------------------
# Generic exception (mocked)
# ---------------------------------------------------------------------------

class TestGenericException:
    """Tests for unexpected exceptions during command execution."""

    def test_generic_exception_returns_error(self, monkeypatch):
        def _raise_oserror(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(subprocess, "run", _raise_oserror)
        result = run_shell_command.invoke({"command": "echo test"})
        assert "Error executing command: disk full" in result


# ---------------------------------------------------------------------------
# Configuration and shell context
# ---------------------------------------------------------------------------

class TestShellConfiguration:
    def test_custom_timeout_is_used(self, monkeypatch):
        def _raise_timeout(*args, **kwargs):
            assert kwargs["timeout"] == 12.5
            raise subprocess.TimeoutExpired(cmd="sleep", timeout=12.5)

        monkeypatch.setattr(subprocess, "run", _raise_timeout)
        tool = minutus_mod.create_shell_command_tool(12.5)

        result = tool.invoke({"command": "sleep 99"})

        assert result == "Error: Command timed out after 12.5 seconds."
        assert tool.name == "run_shell_command"
        assert "timeout" not in tool.args

    def test_append_shell_context_preserves_prompt_and_appends_workspace(self):
        workspace = Path("/workspace/project")
        result = minutus_mod.append_shell_context("Be helpful.\n", "sh", workspace)

        assert result.startswith("Be helpful.\n\n")
        assert result.endswith(
            "Shell tool environment:\n"
            "- Shell: sh\n"
            "- Workspace directory: /workspace/project"
        )

    def test_append_shell_context_handles_empty_prompt(self):
        result = minutus_mod.append_shell_context("", "cmd.exe", Path("work"))
        assert result.startswith("Shell tool environment:")
        assert "- Shell: cmd.exe" in result

    def test_posix_shell_detection_resolves_bin_sh(self, monkeypatch):
        monkeypatch.setattr(
            minutus_mod.os.path, "realpath", lambda path: "/usr/bin/dash"
        )
        assert minutus_mod.get_system_shell_name("posix") == "dash"

    def test_windows_shell_detection_uses_comspec_basename(self, monkeypatch):
        monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
        assert minutus_mod.get_system_shell_name("nt") == "cmd.exe"


class TestShellTimeoutValidation:
    @pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
    def test_invalid_timeout_is_rejected(self, timeout, capsys):
        with pytest.raises(minutus_mod.typer.Exit):
            minutus_mod.validate_shell_command_timeout(timeout)

        assert "must be a positive finite number" in capsys.readouterr().err

    @pytest.mark.parametrize("timeout", [0.1, 30.0, 120])
    def test_positive_finite_timeout_is_accepted(self, timeout):
        minutus_mod.validate_shell_command_timeout(timeout)
