"""
Unit tests for retry_with_backoff() — an async utility that calls an async
function with bounded retries and exponential backoff.

Tests cover:
  - Immediate success (no retries)
  - Retry then success (N failures then success)
  - Backoff timing (2**attempt seconds between retries)
  - KeyboardInterrupt propagates immediately (no retry)
  - *args and **kwargs passed through to the callable
  - Retry diagnostics printed to stderr
  - Bounded retry behavior

All tests patch asyncio.sleep to avoid real waiting.
All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import minutus.minutus as minutus_mod
from minutus.minutus import retry_with_backoff

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper: run an async callable synchronously
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine in a fresh event loop."""
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Immediate success
# ---------------------------------------------------------------------------

class TestImmediateSuccess:
    """Tests for the case where func succeeds on the first call."""

    def test_returns_value_on_first_call(self, monkeypatch):
        mock_func = AsyncMock(return_value="ok")
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        result = run_async(retry_with_backoff(mock_func))
        assert result == "ok"
        mock_func.assert_awaited_once()

    def test_no_sleep_called_on_success(self, monkeypatch):
        mock_func = AsyncMock(return_value="ok")
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        run_async(retry_with_backoff(mock_func))
        sleep_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Retry then success
# ---------------------------------------------------------------------------

class TestRetriesThenSuccess:
    """Tests where func fails N times then succeeds."""

    def test_two_failures_then_success(self, monkeypatch):
        mock_func = AsyncMock(
            side_effect=[ValueError("fail"), ValueError("fail"), "ok"]
        )
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        result = run_async(retry_with_backoff(mock_func))
        assert result == "ok"
        assert mock_func.await_count == 3

    def test_sleep_called_once_per_retry(self, monkeypatch):
        mock_func = AsyncMock(
            side_effect=[ValueError("fail"), ValueError("fail"), "ok"]
        )
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        run_async(retry_with_backoff(mock_func))
        assert sleep_mock.await_count == 2

    def test_five_failures_then_success(self, monkeypatch):
        mock_func = AsyncMock(
            side_effect=[RuntimeError(f"err{i}") for i in range(5)] + ["done"]
        )
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        result = run_async(retry_with_backoff(mock_func, max_retries=5))
        assert result == "done"
        assert mock_func.await_count == 6
        assert sleep_mock.await_count == 5


# ---------------------------------------------------------------------------
# Backoff timing
# ---------------------------------------------------------------------------

class TestBackoffTiming:
    """Tests that sleep durations follow the 2**attempt pattern."""

    def test_backoff_durations_are_powers_of_two(self, monkeypatch):
        mock_func = AsyncMock(
            side_effect=[ValueError("e1"), ValueError("e2"), ValueError("e3"), "ok"]
        )
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        run_async(retry_with_backoff(mock_func))

        # attempt 1 → 2**1 = 2, attempt 2 → 2**2 = 4, attempt 3 → 2**3 = 8
        actual_durations = [call.args[0] for call in sleep_mock.await_args_list]
        assert actual_durations == [2, 4, 8]

    def test_backoff_first_wait_is_two_seconds(self, monkeypatch):
        mock_func = AsyncMock(side_effect=[ValueError("e"), "ok"])
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        run_async(retry_with_backoff(mock_func))
        first_sleep_arg = sleep_mock.await_args_list[0].args[0]
        assert first_sleep_arg == 2

    def test_backoff_sequence_for_five_retries(self, monkeypatch):
        mock_func = AsyncMock(
            side_effect=[ValueError(f"e{i}") for i in range(5)] + ["ok"]
        )
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        run_async(retry_with_backoff(mock_func, max_retries=5))
        actual_durations = [call.args[0] for call in sleep_mock.await_args_list]
        assert actual_durations == [2, 4, 8, 16, 32]


# ---------------------------------------------------------------------------
# KeyboardInterrupt propagation
# ---------------------------------------------------------------------------

class TestKeyboardInterrupt:
    """Tests that KeyboardInterrupt propagates immediately without retry."""

    def test_keyboard_interrupt_propagates(self, monkeypatch):
        mock_func = AsyncMock(side_effect=KeyboardInterrupt())
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        with pytest.raises(KeyboardInterrupt):
            run_async(retry_with_backoff(mock_func))
        mock_func.assert_awaited_once()

    def test_no_sleep_on_keyboard_interrupt(self, monkeypatch):
        mock_func = AsyncMock(side_effect=KeyboardInterrupt())
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        with pytest.raises(KeyboardInterrupt):
            run_async(retry_with_backoff(mock_func))
        sleep_mock.assert_not_awaited()

    def test_keyboard_interrupt_among_other_errors(self, monkeypatch):
        # First call raises ValueError, second raises KeyboardInterrupt
        mock_func = AsyncMock(side_effect=[ValueError("retry me"), KeyboardInterrupt()])
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)
        with pytest.raises(KeyboardInterrupt):
            run_async(retry_with_backoff(mock_func))
        # Should have slept once (for the ValueError retry), but not for KeyboardInterrupt
        assert sleep_mock.await_count == 1
        assert mock_func.await_count == 2


# ---------------------------------------------------------------------------
# Args and kwargs passed through
# ---------------------------------------------------------------------------

class TestArgsKwargsPassedThrough:
    """Tests that *args and **kwargs are forwarded to func."""

    def test_positional_and_keyword_args(self, monkeypatch):
        mock_func = AsyncMock(return_value="ok")
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        run_async(
            retry_with_backoff(mock_func, "pos1", "pos2", key1="val1", key2="val2")
        )
        mock_func.assert_awaited_once_with("pos1", "pos2", key1="val1", key2="val2")

    def test_no_args_no_kwargs(self, monkeypatch):
        mock_func = AsyncMock(return_value="ok")
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        run_async(retry_with_backoff(mock_func))
        mock_func.assert_awaited_once_with()

    def test_args_passed_on_retry(self, monkeypatch):
        mock_func = AsyncMock(side_effect=[ValueError("e"), "ok"])
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        run_async(retry_with_backoff(mock_func, "arg1", kw="v"))
        # Both calls should have the same args
        first_call = mock_func.await_args_list[0]
        second_call = mock_func.await_args_list[1]
        assert first_call.args == ("arg1",)
        assert first_call.kwargs == {"kw": "v"}
        assert second_call.args == ("arg1",)
        assert second_call.kwargs == {"kw": "v"}


# ---------------------------------------------------------------------------
# Retry message printed
# ---------------------------------------------------------------------------

class TestRetryMessage:
    """Tests that retry diagnostics are isolated on stderr."""

    def test_retry_message_printed(self, monkeypatch, capsys):
        mock_func = AsyncMock(side_effect=[ValueError("fail"), "ok"])
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        run_async(retry_with_backoff(mock_func))
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "[Retry]" in captured.err
        assert "fail" in captured.err
        assert "attempt 1" in captured.err

    def test_no_retry_message_on_success(self, monkeypatch, capsys):
        mock_func = AsyncMock(return_value="ok")
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        run_async(retry_with_backoff(mock_func))
        captured = capsys.readouterr()
        assert "[Retry]" not in captured.out
        assert "[Retry]" not in captured.err

    def test_retry_message_shows_attempt_number(self, monkeypatch, capsys):
        mock_func = AsyncMock(
            side_effect=[ValueError("e1"), ValueError("e2"), "ok"]
        )
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        run_async(retry_with_backoff(mock_func))
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "attempt 1" in captured.err
        assert "attempt 2" in captured.err

    def test_retry_message_shows_wait_time(self, monkeypatch, capsys):
        mock_func = AsyncMock(side_effect=[ValueError("e"), "ok"])
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        run_async(retry_with_backoff(mock_func))
        captured = capsys.readouterr()
        # First retry: wait = 2**1 = 2
        assert captured.out == ""
        assert "Retrying in 2s" in captured.err


# ---------------------------------------------------------------------------
# Bounded retries
# ---------------------------------------------------------------------------

class TestBoundedRetries:
    """Tests that retries stop after the configured maximum."""

    def test_three_retries_then_final_exception(self, monkeypatch):
        error = ValueError("final failure")
        mock_func = AsyncMock(side_effect=[error] * 4)
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        with pytest.raises(ValueError, match="final failure"):
            run_async(retry_with_backoff(mock_func))

        assert mock_func.await_count == 4
        assert [call.args[0] for call in sleep_mock.await_args_list] == [2, 4, 8]

    def test_zero_retries_calls_once(self, monkeypatch):
        mock_func = AsyncMock(side_effect=RuntimeError("failure"))
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        with pytest.raises(RuntimeError, match="failure"):
            run_async(retry_with_backoff(mock_func, max_retries=0))

        mock_func.assert_awaited_once()
        sleep_mock.assert_not_awaited()

    def test_custom_retry_limit(self, monkeypatch):
        mock_func = AsyncMock(side_effect=[ValueError("fail"), "ok"])
        sleep_mock = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep_mock)

        result = run_async(retry_with_backoff(mock_func, max_retries=1))

        assert result == "ok"
        assert mock_func.await_count == 2
        assert [call.args[0] for call in sleep_mock.await_args_list] == [2]

    def test_negative_retry_limit_rejected(self):
        mock_func = AsyncMock(return_value="ok")
        with pytest.raises(ValueError, match="non-negative"):
            run_async(retry_with_backoff(mock_func, max_retries=-1))
        mock_func.assert_not_awaited()

    def test_different_exception_types_are_retried(self, monkeypatch):
        mock_func = AsyncMock(
            side_effect=[ValueError("v"), TypeError("t"), KeyError("k"), "ok"]
        )
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        result = run_async(retry_with_backoff(mock_func))

        assert result == "ok"
        assert mock_func.await_count == 4
