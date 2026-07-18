"""Unit tests for the minimal CLI exit-status contract."""

import asyncio

import pytest
import typer

from minutus import minutus

pytestmark = pytest.mark.unit


def run(coro):
    return asyncio.run(coro)


def test_success_returns_normally(capsys):
    @minutus.cli_error_boundary
    async def command(debug=False):
        return "ok"

    assert run(command(debug=False)) == "ok"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_unexpected_error_is_concise_by_default(capsys):
    @minutus.cli_error_boundary
    async def command(debug=False):
        raise RuntimeError("boom")

    with pytest.raises(typer.Exit) as exit_info:
        run(command(debug=False))

    captured = capsys.readouterr()
    assert exit_info.value.exit_code != 0
    assert captured.out == ""
    assert captured.err == "Error: boom\n"
    assert "Traceback" not in captured.err


def test_debug_prints_traceback(capsys):
    @minutus.cli_error_boundary
    async def command(debug=False):
        raise RuntimeError("boom")

    with pytest.raises(typer.Exit) as exit_info:
        run(command(debug=True))

    captured = capsys.readouterr()
    assert exit_info.value.exit_code != 0
    assert captured.out == ""
    assert "Traceback" in captured.err
    assert "RuntimeError: boom" in captured.err


def test_fail_writes_stderr_and_exits_nonzero(capsys):
    with pytest.raises(typer.Exit) as exit_info:
        minutus.fail("Error: invalid input")

    captured = capsys.readouterr()
    assert exit_info.value.exit_code != 0
    assert captured.out == ""
    assert captured.err == "Error: invalid input\n"
