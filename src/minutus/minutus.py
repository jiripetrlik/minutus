#!/usr/bin/env python3

import base64
import json
import pyperclip
import sys
import typer
import re
from async_typer import AsyncTyper
from langchain.tools import tool, ToolRuntime
from langchain.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy, ToolStrategy
from langchain.tools import ToolException
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core._api import suppress_langchain_beta_warning
from langchain_core.runnables import RunnableConfig
from langchain.agents.middleware import SummarizationMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware
from langchain.agents import AgentState
from langchain_mcp_adapters.tools import load_mcp_tools
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from io import BytesIO
import html2text
from html import escape
import pypdf
import os
from typing import Any, Callable, Awaitable, Optional
from contextlib import AsyncExitStack
from enum import Enum
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.validation import Validator, ValidationError
from prompt_toolkit.styles import Style
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.output.defaults import create_output
from pathlib import Path
import asyncio
import subprocess
import logging
import hashlib
import secrets
import shutil
import textwrap
import traceback
import math
import ntpath
import pathspec
from functools import wraps


async def retry_with_backoff(
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    max_retries: int = 3,
    **kwargs: Any,
) -> Any:
    """Call an async function with bounded retries and exponential backoff.

    Catches any exception except KeyboardInterrupt. Wait times follow
    2, 4, 8, ... seconds (2**attempt). The initial call is not counted as a
    retry; by default, the function is called at most four times.

    Args:
        func: An async callable to invoke.
        *args: Positional arguments passed to func.
        max_retries: Maximum number of retries after the initial call. A value
            of zero disables retries.
        **kwargs: Keyword arguments passed to func. Retry diagnostics are
            written to stderr and never mixed with command results on stdout.

    Returns:
        The return value of func on success.

    Raises:
        ValueError: If max_retries is negative.
        KeyboardInterrupt: If the user presses Ctrl+C during retry wait.
        Exception: The final exception raised by func after all retries are
            exhausted.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative")

    attempt = 1
    while True:
        try:
            return await func(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if attempt > max_retries:
                raise

            wait = 2 ** attempt
            print(
                f"[Retry] Error: {e}. Retrying in {wait}s..."
                f" (attempt {attempt})",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(wait)
            attempt += 1


# Initialize html2text converter once to reuse settings
_converter = html2text.HTML2Text()
_converter.ignore_links = False  # Keep links if needed, or set to True to ignore
_converter.ignore_images = True

# 1. Establish the Trusted Root (Current working directory, fully resolved)
TRUSTED_ROOT = Path.cwd().resolve()

# Directories to ignore to save context tokens
IGNORE_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build"}

# Ignore files honored by the workspace tools, in evaluation order. Both use the
# gitignore pattern syntax. Files closer to the target are evaluated later, so
# they win; within a directory ".gitignore" is evaluated before ".aiignore", so
# ".aiignore" can add rules or re-include ("!") paths ignored by ".gitignore".
IGNORE_FILES = (".gitignore", ".aiignore")

DEFAULT_MAX_INPUT_LINES = 5000


class WorkspaceIgnore:
    """Evaluates gitignore-style ignore files for the workspace tools.

    Two ignore files are honored per directory: ``.gitignore`` and
    ``.aiignore`` (see ``IGNORE_FILES``). Patterns are matched relative to the
    directory that contains the ignore file, using the ``gitwildmatch`` syntax
    (so ``!`` negation, anchoring, ``**``, and directory-only ``dir/`` patterns
    all behave as in Git).

    Evaluation walks from the workspace root down to the target's parent. Files
    closer to the target are evaluated later and therefore take precedence;
    within one directory ``.gitignore`` is evaluated before ``.aiignore``.

    Parsed patterns are cached and invalidated when an ignore file's size or
    modification time changes, so edits made during a long session are picked
    up. The workspace root is read from the caller on every query rather than
    captured at construction time.
    """

    def __init__(self, enabled: bool = True, filenames: tuple[str, ...] = IGNORE_FILES):
        self.enabled = enabled
        self.filenames = tuple(filenames)
        # (ignore_file_path) -> (mtime_ns, size, spec_or_None)
        self._cache: dict[Path, tuple[int, int, pathspec.PathSpec | None]] = {}

    def _load_spec(self, ignore_file: Path) -> pathspec.PathSpec | None:
        """Return the parsed spec for ``ignore_file`` or None when absent/unreadable."""
        try:
            stat = ignore_file.stat()
        except OSError:
            self._cache.pop(ignore_file, None)
            return None
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(ignore_file)
        if cached is not None and cached[0] == key[0] and cached[1] == key[1]:
            return cached[2]
        try:
            text = ignore_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._cache[ignore_file] = (key[0], key[1], None)
            return None
        lines = text.splitlines()
        try:
            spec = _build_spec(lines)
        except Exception:
            spec = None
        self._cache[ignore_file] = (key[0], key[1], spec)
        return spec

    def _decide(self, rel_path: Path, is_dir: bool) -> tuple[bool, Path | None]:
        """Evaluate each ignore file that governs ``rel_path`` (root .. parent).

        ``rel_path`` is relative to the workspace root. For every ancestor
        directory the candidate is matched relative to that directory, so
        per-directory ignore files apply to their own subtree only.
        """
        root = TRUSTED_ROOT
        parts = rel_path.parts
        ignored = False
        source: Path | None = None

        # depth 0 is the workspace root; depth d is the d-th ancestor directory.
        # Ascending depth means deeper (closer) ignore files are applied last
        # and therefore take precedence.
        for depth in range(len(parts)):
            directory = root.joinpath(*parts[:depth])
            target = "/".join(parts[depth:])
            if is_dir and not target.endswith("/"):
                target = f"{target}/"
            for filename in self.filenames:
                ignore_file = directory / filename
                spec = self._load_spec(ignore_file)
                if spec is None:
                    continue
                decision = _spec_decision(spec, target)
                if decision is not None:
                    ignored, source = decision, ignore_file

        return ignored, source

    def decision(self, path: Path, is_dir: bool = False) -> tuple[bool, Path | None]:
        """Return ``(ignored, source)`` for an absolute ``path``.

        ``source`` is the ignore file that produced the deciding rule, or None
        when no rule matched. Directories must pass ``is_dir=True`` so that
        directory-only patterns such as ``build/`` are honored.

        Mirroring Git, a file cannot be re-included when one of its ancestor
        directories is excluded, so an ignored ancestor forces the result.
        """
        if not self.enabled:
            return False, None

        try:
            rel_path = path.relative_to(TRUSTED_ROOT)
        except ValueError:
            return False, None
        if not rel_path.parts:
            # The workspace root itself can never be ignored.
            return False, None

        ignored, source = self._decide(rel_path, is_dir=is_dir)

        # An ignored ancestor directory always wins over a re-inclusion.
        for depth in range(1, len(rel_path.parts)):
            ancestor_rel = Path(*rel_path.parts[:depth])
            ancestor_ignored, ancestor_source = self._decide(ancestor_rel, is_dir=True)
            if ancestor_ignored:
                ignored, source = True, ancestor_source

        return ignored, source

    def check(self, path: Path, is_dir: bool = False) -> Path | None:
        """Return the deciding ignore file when ``path`` is ignored, else None."""
        ignored, source = self.decision(path, is_dir=is_dir)
        return source if ignored else None


def get_workspace_ignore(enabled: bool) -> WorkspaceIgnore:
    """Build an ignore matcher for the current trusted workspace root."""
    return WorkspaceIgnore(enabled=enabled)


def _build_spec(lines: list[str]) -> "pathspec.PathSpec":
    """Build a spec from gitignore lines.

    ``GitIgnoreSpec`` (pathspec >= 0.11) implements the precise last-match-wins
    Git semantics; fall back to a ``gitwildmatch`` ``PathSpec`` otherwise.
    """
    gitignore_spec = getattr(pathspec, "GitIgnoreSpec", None)
    if gitignore_spec is not None:
        return gitignore_spec.from_lines(lines)
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def _spec_decision(spec: "pathspec.PathSpec", target: str) -> bool | None:
    """Return True (ignored), False (re-included), or None (no rule matched).

    Uses ``check_file`` when available so that ``!`` negation is distinguished
    from "no rule matched"; falls back to ``match_file`` otherwise.
    """
    check_file = getattr(spec, "check_file", None)
    if check_file is not None:
        try:
            result = check_file(target)
        except Exception:
            return None
        index = getattr(result, "index", None)
        if index is None:
            return None
        return bool(getattr(result, "include", False))
    try:
        return bool(spec.match_file(target))
    except Exception:
        return None


def ensure_not_ignored(
    safe_path: Path, user_path: str, ignore: WorkspaceIgnore, is_dir: bool = False
) -> str | None:
    """Return an error message when ``safe_path`` is excluded by ignore rules."""
    source = ignore.check(safe_path, is_dir=is_dir)
    if source is None:
        return None
    try:
        source_display = source.relative_to(TRUSTED_ROOT).as_posix()
    except ValueError:
        source_display = str(source)
    return (
        f"Error: Path '{user_path}' is excluded by ignore rules "
        f"({source_display})."
    )


def validate_max_input_lines(max_input_lines: int) -> None:
    """Reject non-positive input line limits."""
    if max_input_lines <= 0:
        fail("Error: max-input-lines must be a positive integer")


def truncate_first_lines(content: str, max_lines: int) -> str:
    """Keep the first ``max_lines`` lines and describe any truncation."""
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content
    retained = "\n".join(lines[:max_lines])
    return (
        f"{retained}\n... Truncated: showing first {max_lines:,} "
        f"of {len(lines):,} lines."
    )


def truncate_last_lines(content: str, max_lines: int) -> str:
    """Keep the last ``max_lines`` lines and describe any truncation."""
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content
    retained = "\n".join(lines[-max_lines:])
    return (
        f"... Truncated: showing last {max_lines:,} of {len(lines):,} lines.\n"
        f"{retained}"
    )

app = AsyncTyper()


def write_result(text: str, *, end: str = "\n", flush: bool = False) -> None:
    """Write command result data to stdout."""
    print(text, end=end, file=sys.stdout, flush=flush)


def write_diagnostic(text: str, *, end: str = "\n", flush: bool = False) -> None:
    """Write status, warning, and diagnostic text to stderr."""
    print(text, end=end, file=sys.stderr, flush=flush)


def fail(message: str) -> None:
    """Report a command failure and exit with a non-zero status."""
    write_diagnostic(message)
    raise typer.Exit(1)


def validate_auto_run_tools(
    auto_run_tools: list[str], available_tool_names: list[str]
) -> None:
    """Reject auto-run names that do not identify an enabled tool.

    Validation happens after tool discovery because MCP tool names are only known
    once the configured servers have started and advertised their tools.
    """
    available = set(available_tool_names)
    unknown = sorted(set(auto_run_tools) - available)
    if not unknown:
        return

    available_display = ", ".join(sorted(available)) if available else "(none)"
    fail(
        "Error: unknown tool name(s) in --auto-run-tools: "
        f"{', '.join(unknown)}. Available tools: {available_display}"
    )


def resolve_api_key(api_key: Optional[str], api_key_file: Optional[str]) -> str:
    """Resolve an API key from the supported CLI and environment sources."""
    if api_key is not None and api_key_file is not None:
        fail("Error: api-key and api-key-file cannot be set at the same time")

    if api_key is not None:
        resolved_key = api_key.strip()
    elif api_key_file is not None:
        try:
            resolved_key = (
                Path(api_key_file)
                .expanduser()
                .read_text(encoding="utf-8")
                .strip()
            )
        except (OSError, UnicodeError) as error:
            fail(f"Error reading API key file: {error}")
    else:
        resolved_key = os.getenv("MINUTUS_OPENAI_API_KEY", "").strip()

    if not resolved_key:
        fail(
            "Error: API key is required. Use --api-key, --api-key-file, or set "
            "MINUTUS_OPENAI_API_KEY."
        )

    return resolved_key


def cli_error_boundary(command):
    """Keep unexpected CLI failures concise unless debug mode is enabled."""
    @wraps(command)
    async def wrapped(*args, **kwargs):
        try:
            return await command(*args, **kwargs)
        except typer.Exit:
            raise
        except KeyboardInterrupt:
            write_diagnostic("Interrupted.")
            raise typer.Exit(1)
        except Exception as error:
            if kwargs.get("debug", False):
                traceback.print_exc(file=sys.stderr)
            else:
                write_diagnostic(f"Error: {error}")
            raise typer.Exit(1)

    return wrapped


def stderr_prompt_output():
    """Create a prompt_toolkit output renderer backed by stderr."""
    return create_output(stdout=sys.stderr)


class StructuredOutputStrategy(str, Enum):
    """Available strategies for generating structured output."""

    provider = "provider"
    tool = "tool"


NON_INTERACTIVE_REJECTION_MESSAGE = (
    "Tool call rejected because interactive approval is unavailable. "
    "This tool is unavailable for the remainder of this run. Continue without "
    "it and do not request it again."
)


class ToolRunningMiddleware(AgentMiddleware):
    """Report each tool immediately before its handler begins execution."""

    @staticmethod
    def _tool_name(request: ToolCallRequest) -> str:
        return str(request.tool_call.get("name", "unknown"))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Report and execute a synchronous tool call."""
        write_diagnostic(f"Running tool: {self._tool_name(request)}", flush=True)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Report and execute an asynchronous tool call."""
        write_diagnostic(f"Running tool: {self._tool_name(request)}", flush=True)
        return await handler(request)


# Workaround to handle tool call errors (https://github.com/langchain-ai/langchain/issues/33504)
class ToolErrorHandlingMiddleware(AgentMiddleware):
    """Middleware that converts invalid_tool_calls to ToolMessages.

    This allows tools to raise ToolException for recoverable errors and
    handles JSON parsing errors from invalid_tool_calls that the LLM can see.
    """

    def after_model(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Convert invalid_tool_calls to ToolMessages so the LLM can see the error."""
        messages = state.get("messages", [])
        if not messages:
            return None

        last_message = messages[-1]
        if not isinstance(last_message, AIMessage):
            return None

        invalid_tool_calls = getattr(last_message, "invalid_tool_calls", None)
        if not invalid_tool_calls:
            return None

        # Convert each invalid tool call to a ToolMessage with error
        error_messages: list[ToolMessage] = []
        for invalid_call in invalid_tool_calls:
            tool_call_id = invalid_call.get("id", "")
            error_msg = invalid_call.get("error") or "Unknown parsing error"

            # Create ToolMessage with the parsing error
            tool_message = ToolMessage(
                content=f"Tool error: Please check your input and try again. ({error_msg})",
                tool_call_id=tool_call_id,
                status="error",
            )
            error_messages.append(tool_message)

        # Append error messages to state so LLM can see them
        if error_messages:
            return {"messages": error_messages}

        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Wrap tool call to catch ToolException and convert to ToolMessage."""
        try:
            return handler(request)
        except ToolException as e:
            return ToolMessage(
                content=f"Tool error: Please check your input and try again. ({e})",
                tool_call_id=request.tool_call.get("id", ""),
                status="error",
            )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async wrapper for tool call to catch ToolException and convert to ToolMessage."""
        try:
            return await handler(request)
        except ToolException as e:
            return ToolMessage(
                content=f"Tool error: Please check your input and try again. ({e})",
                tool_call_id=request.tool_call.get("id", ""),
                status="error",
            )


def format_tool_arguments(arguments: Any) -> str:
    """Return readable, multiline text for arguments in an approval dialog."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return arguments
    try:
        return json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(arguments)


def wrap_approval_prompt(prompt: str, width: Optional[int] = None) -> str:
    """Wrap every line of an approval prompt to fit inside its dialog.

    ``prompt_toolkit`` frames do not make a long prompt fragment scroll or
    reflow reliably. In particular, serialized tool arguments often contain
    no convenient whitespace, so letting the renderer wrap them can clip the
    remainder. Insert line breaks ourselves, including within long words.
    """
    terminal_width = width or shutil.get_terminal_size(fallback=(100, 24)).columns
    # Leave room for the frame borders and a small safety margin used by
    # prompt_toolkit when calculating the input window's available width.
    content_width = max(20, terminal_width - 4)
    wrapped_lines = []
    for line in prompt.split("\n"):
        if not line:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            textwrap.wrap(
                line,
                width=content_width,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return "\n".join(wrapped_lines)


async def get_allow_reject_input(prompt="Please enter 'allow' or 'reject': "):
    """
    Get an allow/reject input from the user with validation using prompt_toolkit.

    Args:
        prompt (str): The message to display to the user

    Returns:
        bool: True for 'allow', False for 'reject'
    """
    # Create prompt session
    session = PromptSession(
        bottom_toolbar=lambda: HTML(
            "<b>Tip:</b> Press <b>A</b> to allow, <b>R</b>"
            "to reject, <b>Tab</b> for completions"
        ),
        style=Style.from_dict(
            {
                "frame.border": "#808080",
                "toolbar": "#ffffff bg:#333333",
            }
        ),
        include_default_pygments_style=False,
        output=stderr_prompt_output(),
    )

    # Define valid completions
    completer = WordCompleter(["allow", "reject", "a", "r"], ignore_case=True)

    # Define validator
    class AllowRejectValidator(Validator):
        def validate(self, document):
            text = document.text.strip().lower()
            if text not in ["allow", "reject", "a", "r"]:
                raise ValidationError(
                    message="Please enter 'allow'," "'reject', 'a', or 'r'",
                    cursor_position=len(text),
                )

    # Create key bindings for quick input
    kb = KeyBindings()

    @kb.add("a")
    def _(event):
        event.app.exit(result="allow")

    @kb.add("r")
    def _(event):
        event.app.exit(result="reject")

    while True:
        try:
            result = await session.prompt_async(
                HTML(f"<b>{escape(wrap_approval_prompt(prompt))}</b>"),
                completer=completer,
                validator=AllowRejectValidator(),
                bottom_toolbar=get_allow_reject_input.bottom_toolbar,
                mouse_support=False,
                multiline=False,
                wrap_lines=True,
                show_frame=True,
                key_bindings=kb,
            )

            # Handle empty result (shouldn't happen due to validation, but safe)
            if not result:
                write_diagnostic("Please enter 'allow' or 'reject'")
                continue

            if result.strip().lower() in ["allow", "a"]:
                return True
            elif result.strip().lower() in ["reject", "r"]:
                return False
            else:
                write_diagnostic("Please enter 'allow' or 'reject' (or 'a'/'r').")
        except KeyboardInterrupt:
            write_diagnostic("\nCancelled")
            return False


# Add bottom toolbar function to the function itself for persistence
get_allow_reject_input.bottom_toolbar = lambda: HTML(
    "<b>Tip:</b> Press <b>A</b> to allow, <b>R</b> to reject, <b>Tab</b> for completions"
)


def can_prompt_for_tool_approval(non_interactive: bool) -> bool:
    """Return whether an interactive tool-approval prompt may be opened.

    Fail closed if stdin does not expose a usable TTY check.
    """
    if non_interactive:
        return False
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, OSError):
        return False


async def decide_tool_call(tool_call_message: dict, non_interactive: bool) -> dict:
    """Approve or reject an interrupted tool call without prompting when unsafe.

    Explicit non-interactive mode is the primary contract. The stdin TTY check
    is a safety fallback for CI, pipes, cron, and detached subprocesses.
    """
    if not can_prompt_for_tool_approval(non_interactive):
        return {"type": "reject", "message": NON_INTERACTIVE_REJECTION_MESSAGE}

    tool_name = str(tool_call_message.get("name", "unknown"))
    tool_args = format_tool_arguments(tool_call_message.get("args", ""))
    prompt_msg = (
        f"Tool call: {tool_name}\n"
        f"Arguments: {tool_args}\n\n"
        "Allow execution tool call?\n\n (allow/reject): "
    )

    try:
        approved = await get_allow_reject_input(prompt=prompt_msg)
    except EOFError:
        return {"type": "reject", "message": NON_INTERACTIVE_REJECTION_MESSAGE}

    if approved:
        return {"type": "approve"}

    try:
        reason = await get_reject_message()
    except EOFError:
        reason = ""
    return {"type": "reject", "message": reason}


def extract_hitl_actions(interrupts) -> list[tuple[dict, dict]]:
    """Return ordered (action_request, review_config) pairs for human review.

    Reads the human-in-the-loop interrupt payload produced by
    ``HumanInTheLoopMiddleware``. Each interrupt value holds ``action_requests``
    and ``review_configs`` (see the middleware's ``HITLRequest``). Only gated
    calls appear here: auto-approved tools are never added to the batch.

    Interrupts are de-duplicated by id so a repeat of the same pause is not
    reviewed twice, and interrupts whose value is not a HITL request are
    ignored.
    """
    pairs: list[tuple[dict, dict]] = []
    seen_ids: set[str] = set()
    for interrupt in interrupts:
        interrupt_id = getattr(interrupt, "id", None)
        if interrupt_id is not None and interrupt_id in seen_ids:
            continue
        value = getattr(interrupt, "value", interrupt)
        if not isinstance(value, dict):
            continue
        action_requests = value.get("action_requests") or []
        if not action_requests:
            continue
        if interrupt_id is not None:
            seen_ids.add(interrupt_id)
        review_configs = value.get("review_configs") or []
        for index, action_request in enumerate(action_requests):
            review_config = (
                review_configs[index] if index < len(review_configs) else {}
            )
            pairs.append((action_request, review_config))
    return pairs


async def build_resume_payload(interrupts, non_interactive: bool) -> Optional[dict]:
    """Prompt once per gated action and build a ``Command(resume=...)`` payload.

    Decisions are collected in the same order as the interrupt's
    ``action_requests``, as the middleware requires. Returns ``None`` when there
    is nothing pending human review.

    The CLI only offers approve/reject, which every configuration in this
    application allows. The ``allowed_decisions`` check is a defensive guard so
    a decision the tool's policy forbids is never submitted.
    """
    pairs = extract_hitl_actions(interrupts)
    if not pairs:
        return None

    decisions: list[dict] = []
    for action_request, review_config in pairs:
        decision = await decide_tool_call(action_request, non_interactive)
        allowed_decisions = review_config.get("allowed_decisions")
        if allowed_decisions and decision["type"] not in allowed_decisions:
            decision = {
                "type": "reject",
                "message": NON_INTERACTIVE_REJECTION_MESSAGE,
            }
        decisions.append(decision)

    return {"decisions": decisions}


async def get_reject_message() -> str:
    """
    Get a rejection reason/message from the user using prompt_toolkit.

    Returns:
        str: The rejection message provided by the user. Returns empty string if cancelled.
    """
    # Create prompt session with red border
    session = PromptSession(
        bottom_toolbar=lambda: HTML(
            "<b>Tip:</b> Press <b>Enter</b> to submit, <b>Ctrl+C</b> to cancel"
        ),
        style=Style.from_dict(
            {
                "frame.border": "#ff0000",  # Red border
                "toolbar": "#ffffff bg:#333333",
            }
        ),
        include_default_pygments_style=False,
        output=stderr_prompt_output(),
    )

    try:
        # Prompt for the reason
        message = await session.prompt_async(
            HTML("<b><ansired>Reason for rejection (optional):</ansired></b> "),
            multiline=False,
            wrap_lines=True,
            show_frame=True,
        )
        return message.strip() if message else ""
    except KeyboardInterrupt:
        return ""


class DocumentParseError(Exception):
    """Raised when a supported document cannot be converted to text."""


def detect_document_type(
    source_name: str,
    content_bytes: bytes,
    content_type: str = "",
) -> str:
    """Detect whether bytes contain PDF, HTML, or plain text.

    PDF's file signature takes precedence over names and response headers so a
    locally renamed PDF is still parsed correctly.  Extensions and content
    type are used for HTML/PDF fallback detection.
    """
    source_lower = source_name.lower()
    content_type_lower = content_type.lower()

    if content_bytes.startswith(b"%PDF-"):
        return "pdf"
    if "application/pdf" in content_type_lower or source_lower.endswith(".pdf"):
        return "pdf"
    if (
        "text/html" in content_type_lower
        or source_lower.endswith(".html")
        or source_lower.endswith(".htm")
    ):
        return "html"

    # Also recognize HTML saved without an HTML extension.  Keep this
    # deliberately conservative so ordinary source/text files are not routed
    # through html2text merely because they contain angle brackets.
    try:
        text_start = content_bytes.decode("utf-8").lstrip()[:512].lower()
        if (
            text_start.startswith("<!doctype html")
            or text_start.startswith("<html")
            or "<html" in text_start[:128]
        ):
            return "html"
    except UnicodeDecodeError:
        pass

    return "text"


def parse_document(
    content_bytes: bytes,
    source_name: str,
    content_type: str = "",
    *,
    text_errors: str = "strict",
) -> str:
    """Convert PDF or HTML bytes to text, or decode ordinary text files.

    ``text_errors`` allows URL handling to retain its historical forgiving
    behavior while local text files retain the previous strict behavior.
    """
    document_type = detect_document_type(source_name, content_bytes, content_type)

    if document_type == "pdf":
        try:
            pdf_reader = pypdf.PdfReader(stream=BytesIO(content_bytes))
            text_parts = []
            for page in pdf_reader.pages:
                # Some PDF pages have no extractable text and return None.
                text_parts.append(page.extract_text() or "")
            return "\n".join(text_parts).strip()
        except Exception as e:
            raise DocumentParseError(f"Failed to parse PDF: {e}") from e

    if document_type == "html":
        try:
            html_content = content_bytes.decode("utf-8")
            return _converter.handle(html_content)
        except Exception as e:
            raise DocumentParseError(f"Failed to convert HTML: {e}") from e

    return content_bytes.decode("utf-8", errors=text_errors)


def load_files_context(
    file_paths, max_input_lines: int = DEFAULT_MAX_INPUT_LINES
):
    """
    Loads content from multiple files and formats it as context for AI.

    PDF files are parsed with pypdf and HTML files are converted with the
    shared html2text converter. Other files are decoded as UTF-8 text.

    Args:
        file_paths (list): List of file paths as strings

    Returns:
        str: Formatted context with each file's content wrapped in markdown code blocks
    """
    formatted_context = []

    for file_path in file_paths:
        try:
            with open(file_path, "rb") as file:
                content_bytes = file.read()
            content = parse_document(content_bytes, str(file_path))
            content = truncate_first_lines(content, max_input_lines)
            formatted_context.append(f"```{file_path}\n{content}\n```")
        except DocumentParseError as e:
            fail(f"Error loading file '{file_path}': {e}")
        except Exception as e:
            fail(f"Error loading file: {e}")

    return "\n".join(formatted_context)


def load_clipboard_context(max_input_lines: int = DEFAULT_MAX_INPUT_LINES):
    """
    Loads content from clipboard and formats it as context for AI.

    Returns:
        str: Formatted context with clipboard content wrapped in markdown code blocks
    """

    try:
        clipboard_content = truncate_first_lines(
            pyperclip.paste(), max_input_lines
        )
        return f"```clipboard\n{clipboard_content}\n```"
    except Exception as e:
        fail(f"Error loading clipboard: {e}")


def load_image(image_path: str) -> tuple[str, str]:
    """Load image from file path and return (base64_data, mime_type)"""
    try:
        with open(image_path, "rb") as f:
            file_content = f.read()
        # Determine MIME type from file extension
        if image_path.lower().endswith(".png"):
            mime_type = "image/png"
        elif image_path.lower().endswith((".jpg", ".jpeg")):
            mime_type = "image/jpeg"
        elif image_path.lower().endswith(".gif"):
            mime_type = "image/gif"
        elif image_path.lower().endswith(".bmp"):
            mime_type = "image/bmp"
        elif image_path.lower().endswith(".tiff"):
            mime_type = "image/tiff"
        elif image_path.lower().endswith(".webp"):
            mime_type = "image/webp"
        elif image_path.lower().endswith(".svg"):
            mime_type = "image/svg+xml"
        else:
            mime_type = "image/jpeg"  # Default fallback

        # Encode to base64
        base64_data = base64.b64encode(file_content).decode("utf-8")

        return base64_data, mime_type
    except Exception as e:
        fail(f"Error loading image: {e}")


def load_mcp_config(config_path: str) -> dict:
    """Load MCP configuration from JSON file"""
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception as e:
        fail(f"Error loading MCP config: {e}")


def load_json_schema(schema_path: str) -> dict:
    """Load JSON schema from file"""
    try:
        with open(schema_path, "r") as f:
            schema = json.load(f)
        # LangChain's with_structured_output(method="json_schema") requires
        # a top-level 'title' key to use as the function name. Inject a
        # default title if the schema doesn't already have one.
        if isinstance(schema, dict) and "title" not in schema:
            schema["title"] = "structured_output"
        return schema
    except Exception as e:
        fail(f"Error loading JSON schema: {e}")


def load_file_content(file_path: str) -> str:
    """Load and parse a local text, HTML, or PDF file."""
    try:
        with open(file_path, "rb") as f:
            return parse_document(f.read(), str(file_path))
    except DocumentParseError as e:
        fail(f"Error loading file '{file_path}': {e}")
    except Exception as e:
        fail(f"Error loading file: {e}")


def get_system_shell_name(platform_name: Optional[str] = None) -> str:
    """Return a readable name for the shell used by ``shell=True``.

    On POSIX, Python invokes ``/bin/sh`` regardless of the user's login shell.
    Resolve that path so common links to shells such as ``dash`` are visible.
    On Windows, Python uses the command processor identified by ``COMSPEC``.
    Detection is informational and therefore falls back rather than failing.
    """
    try:
        effective_platform = platform_name or os.name
        if effective_platform == "nt":
            shell_path = os.environ.get("COMSPEC")
            shell_name = ntpath.basename(shell_path) if shell_path else ""
            return shell_name or "system shell"

        resolved_shell = os.path.realpath("/bin/sh")
        return os.path.basename(resolved_shell) or "sh"
    except (OSError, ValueError, TypeError):
        return "system shell"


def append_shell_context(
    system_prompt: str, shell_name: str, workspace: Path
) -> str:
    """Append informational shell-tool environment details to a system prompt."""
    context = (
        "Shell tool environment:\n"
        f"- Shell: {shell_name}\n"
        f"- Workspace directory: {workspace}"
    )
    if not system_prompt:
        return context
    return f"{system_prompt.rstrip()}\n\n{context}"


def validate_shell_command_timeout(timeout: float) -> None:
    """Reject non-positive or non-finite shell command timeout values."""
    if not math.isfinite(timeout) or timeout <= 0:
        fail("Error: shell-command-timeout must be a positive finite number")


def execute_shell_command(
    command: str,
    timeout: float = 30.0,
    max_input_lines: int = DEFAULT_MAX_INPUT_LINES,
) -> str:
    """Execute a command and retain the tail of oversized output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = ""
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr}\n"

        output = truncate_last_lines(output.rstrip("\n"), max_input_lines)
        if result.returncode != 0:
            return f"Command failed with exit code {result.returncode}.\n{output}"

        return f"Command executed successfully.\n{output}"

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout:g} seconds."
    except FileNotFoundError:
        return "Error: System shell not found."
    except Exception as e:
        return f"Error executing command: {str(e)}"


def create_shell_command_tool(
    timeout: float = 30.0,
    max_input_lines: int = DEFAULT_MAX_INPUT_LINES,
):
    """Create the shell tool with CLI-controlled timeout and output limit."""
    @tool("run_shell_command")
    def configured_shell_command(command: str) -> str:
        """Execute a command in the platform's system shell.

        Args:
            command: The shell command to execute (e.g., "ls -la", "echo Hello").

        Returns:
            A string containing stdout, stderr, and the command status.
        """
        return execute_shell_command(command, timeout, max_input_lines)

    return configured_shell_command


# Retain the importable default tool for library users and backwards compatibility.
run_shell_command = create_shell_command_tool()


def create_read_url_tool(
    max_input_lines: int = DEFAULT_MAX_INPUT_LINES,
    user_agent: Optional[str] = None,
):
    """Create the URL reader with a model-facing line limit.

    ``user_agent`` overrides the ``User-Agent`` header sent with requests. When
    it is not provided, the ``MINUTUS_READ_URL_USER_AGENT`` environment variable
    is used instead; when neither is set the tool keeps its default behavior and
    lets the client library send its built-in ``User-Agent``.
    """
    resolved_user_agent = (user_agent or "").strip() or os.getenv(
        "MINUTUS_READ_URL_USER_AGENT", ""
    ).strip()

    @tool("read_url")
    def configured_read_url(url: str) -> str:
        """Read content from a URL.

        Args:
            url: The full URL to read content from.
        """
        try:
            if resolved_user_agent:
                request = Request(url, headers={"User-Agent": resolved_user_agent})
                stream = urlopen(request, timeout=10)
            else:
                stream = urlopen(url, timeout=10)
            with stream as response:
                content_bytes = response.read()
                content_type = response.headers.get("Content-Type", "")

                try:
                    content = parse_document(
                        content_bytes,
                        url,
                        content_type,
                        text_errors="ignore",
                    )
                    return truncate_first_lines(content, max_input_lines)
                except DocumentParseError as e:
                    return str(e)

        except HTTPError as e:
            return f"HTTP Error: {e.code} - {e.reason}"
        except URLError as e:
            return f"URL Error: {e.reason}"
        except Exception as e:
            return f"Failed to process URL: {str(e)}"

    return configured_read_url


read_url = create_read_url_tool()


def get_safe_path(user_path: str) -> Path:
    """
    Validates and resolves a path to ensure it stays within the TRUSTED_ROOT.
    Raises ValueError if the path attempts to escape the workspace.
    """
    # 2. Sanitize Input (e.g., expand tildes so they resolve to an absolute path outside workspace)
    sanitized = Path(user_path).expanduser()

    # 3. Resolve the Candidate Path (expand .. and follow symlinks to their true location)
    candidate = sanitized.resolve()

    # 4. Validate Containment (Check if the resolved path is relative to the trusted root)
    try:
        # Path.relative_to() throws a ValueError if 'candidate' is not a subpath of TRUSTED_ROOT
        candidate.relative_to(TRUSTED_ROOT)
    except ValueError:
        raise ValueError(
            f"Security Error: Path '{user_path}' resolves outside the trusted workspace."
        )

    return candidate


def create_list_files_tool(
    max_input_lines: int = DEFAULT_MAX_INPUT_LINES,
    respect_ignore_files: bool = True,
):
    ignore = get_workspace_ignore(respect_ignore_files)

    @tool("list_files")
    def configured_list_files(path: str = ".", recursive: bool = False) -> str:
        """Lists files and directories in the specified path."""
        try:
            safe_path = get_safe_path(path)
        except ValueError as e:
            return str(e)
        if not safe_path.is_dir():
            return f"Error: '{path}' is not a directory."
        ignored_error = ensure_not_ignored(safe_path, path, ignore, is_dir=True)
        if ignored_error is not None:
            return ignored_error

        files = []
        if recursive:
            for root, dirs, filenames in os.walk(safe_path):
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in IGNORE_DIRS
                    and not ignore.check(Path(root) / d, is_dir=True)
                ]
                for filename in filenames:
                    full_path = Path(root) / filename
                    if ignore.check(full_path):
                        continue
                    files.append(os.path.relpath(full_path, safe_path))
        else:
            for name in os.listdir(safe_path):
                full_path = safe_path / name
                if ignore.check(full_path, is_dir=full_path.is_dir()):
                    continue
                files.append(name)

        if not files:
            return "Directory is empty."
        return truncate_first_lines("\n".join(files), max_input_lines)

    return configured_list_files


def strip_line_ending(line: str) -> str:
    """Remove only the trailing newline (and a preceding carriage return).

    Trailing spaces and tabs are preserved: they are real bytes on disk and
    affect byte-exact operations such as ``edit_file`` matching.
    """
    if line.endswith("\n"):
        line = line[:-1]
        if line.endswith("\r"):
            line = line[:-1]
    return line


def render_numbered_line(line: str, show_whitespace: bool) -> str:
    """Render one file line for ``read_file`` output.

    Line terminators are never shown. Trailing spaces and tabs are preserved
    by default, because they are significant. When ``show_whitespace`` is set,
    a trailing run of spaces and tabs is rendered with visible markers so it
    cannot be mistaken for absent whitespace.
    """
    line = strip_line_ending(line)
    if not show_whitespace:
        return line
    stripped = line.rstrip(" \t")
    trailing = line[len(stripped):]
    if not trailing:
        return line
    markers = "".join("\u2420" if ch == " " else "\u2409" for ch in trailing)
    return f"{stripped}{markers}"


def create_read_file_tool(
    max_input_lines: int = DEFAULT_MAX_INPUT_LINES,
    respect_ignore_files: bool = True,
):
    ignore = get_workspace_ignore(respect_ignore_files)

    @tool("read_file")
    def configured_read_file(
        path: str,
        start_line: int = None,
        end_line: int = None,
        show_whitespace: bool = False,
    ) -> str:
        """Reads a file and returns one numbered line per physical line.

        Output format is ``N: <content>``. Line terminators (\\n or \\r\\n) are
        not shown. Trailing spaces and tabs ARE preserved because they are real
        on-disk bytes; pass show_whitespace=True to render a trailing run of
        them with visible markers (\u2420 for space, \u2409 for tab). A single
        physical line may itself contain the literal two-character sequence
        backslash+n (as in JSON or Jupyter notebook source): that is one line,
        not a line break. Use start_line/end_line to read a range.

        Paths excluded by .gitignore or .aiignore are refused.
        """
        try:
            safe_path = get_safe_path(path)
        except ValueError as e:
            return str(e)
        if not safe_path.is_file():
            return f"Error: File '{path}' does not exist."
        ignored_error = ensure_not_ignored(safe_path, path, ignore)
        if ignored_error is not None:
            return ignored_error

        with open(safe_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        total_lines = len(lines)
        if start_line is None or start_line < 1:
            start_line = 1
        if end_line is None or end_line > total_lines:
            end_line = total_lines
        requested_end = end_line
        if end_line - start_line + 1 > max_input_lines:
            end_line = start_line + max_input_lines - 1
        output = "\n".join(
            f"{i + 1}: {render_numbered_line(lines[i], show_whitespace)}"
            for i in range(start_line - 1, min(end_line, total_lines))
        )
        if end_line < requested_end:
            output += (
                f"\n... Truncated: showing lines {start_line} to {end_line} "
                f"of requested range ending at {requested_end}."
            )
        return output

    return configured_read_file


def iter_searchable_files(root: Path, ignore: "WorkspaceIgnore | None" = None):
    """Yield files to search under ``root``.

    If ``root`` is a regular file, yield just that file. If it is a directory,
    walk it recursively while skipping ``IGNORE_DIRS`` and any path excluded by
    the supplied ignore matcher.
    """
    if root.is_file():
        yield root
        return
    for dirpath, dirs, filenames in os.walk(root):
        pruned = []
        for d in dirs:
            if d in IGNORE_DIRS:
                continue
            if ignore is not None and ignore.check(Path(dirpath) / d, is_dir=True):
                continue
            pruned.append(d)
        dirs[:] = pruned
        for name in filenames:
            full_path = Path(dirpath) / name
            if ignore is not None and ignore.check(full_path):
                continue
            yield full_path


def create_search_files_tool(
    max_input_lines: int = DEFAULT_MAX_INPUT_LINES,
    respect_ignore_files: bool = True,
):
    ignore = get_workspace_ignore(respect_ignore_files)

    @tool("search_files")
    def configured_search_files(
        query: str, path: str = ".", regex: bool = True
    ) -> str:
        """Searches file contents and returns "filepath:line: content" matches.

        path may be a file or a directory (directories are searched recursively;
        common build/vendor directories are skipped). By default query is a
        REGULAR EXPRESSION: characters such as ( ) [ ] . * + ? | ^ $ have special
        meaning, so a query like next(iter(x)) is treated as a pattern and will
        NOT match that literal text. To match a literal string that contains
        regex metacharacters, pass regex=False (or escape the metacharacters).
        If regex=True and query is not valid regex, it is matched as literal
        text and a note is appended to the result. Matching is case-sensitive by
        default; use (?i) for case-insensitive. Results are capped; a truncation
        marker is appended when the cap is hit.

        Paths excluded by .gitignore or .aiignore are skipped.
        """
        try:
            safe_path = get_safe_path(path)
        except ValueError as e:
            return str(e)
        if not safe_path.exists():
            return f"Error: Path '{path}' does not exist."
        if safe_path.is_file():
            ignored_error = ensure_not_ignored(safe_path, path, ignore)
            if ignored_error is not None:
                return ignored_error
        else:
            ignored_error = ensure_not_ignored(safe_path, path, ignore, is_dir=True)
            if ignored_error is not None:
                return ignored_error

        notice = None
        if regex:
            try:
                pattern = re.compile(query)
                matcher = lambda line: pattern.search(line) is not None
            except re.error:
                matcher = lambda line: query in line
                notice = (
                    "Note: query is not valid regex; matched as literal text."
                )
        else:
            matcher = lambda line: query in line

        results = []
        truncated = False
        for filepath in iter_searchable_files(safe_path, ignore):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if matcher(line):
                            if len(results) == max_input_lines:
                                truncated = True
                                break
                            clean_filepath = os.path.relpath(filepath, TRUSTED_ROOT)
                            results.append(f"{clean_filepath}:{i}: {line.rstrip()}")
            except (UnicodeDecodeError, PermissionError):
                continue
            if truncated:
                break
        if not results:
            if notice:
                return f"No matches found. {notice}"
            return "No matches found."
        output = "\n".join(results)
        if truncated:
            output += f"\n... Truncated: showing first {max_input_lines:,} matching lines."
        if notice:
            output += f"\n... {notice}"
        return output

    return configured_search_files


list_files = create_list_files_tool()
read_file = create_read_file_tool()
search_files = create_search_files_tool()


def create_write_file_tool(respect_ignore_files: bool = True):
    ignore = get_workspace_ignore(respect_ignore_files)

    @tool("write_file")
    def configured_write_file(path: str, content: str) -> str:
        """Creates a new file or overwrites an existing file with new content.

        content is written literally, byte for byte: this tool performs NO escape
        processing. This is the reliable choice for whole-file or format-critical
        rewrites (for example JSON or Jupyter notebooks) where escaping must be
        fully under the caller's control. Re-read the file afterward to verify.

        Paths excluded by .gitignore or .aiignore are refused, including paths
        whose parent directory would have to be created inside an ignored tree.
        """
        try:
            safe_path = get_safe_path(path)
        except ValueError as e:
            return str(e)

        ignored_error = ensure_not_ignored(safe_path, path, ignore)
        if ignored_error is not None:
            return ignored_error

        parent_dir = safe_path.parent
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, exist_ok=True)

        with open(safe_path, "w", encoding="utf-8") as f:
            f.write(content)

        return f"Successfully wrote {len(content)} characters to {path}."

    return configured_write_file


def create_edit_file_tool(respect_ignore_files: bool = True):
    ignore = get_workspace_ignore(respect_ignore_files)

    @tool("edit_file")
    def configured_edit_file(path: str, old_string: str, new_string: str) -> str:
        """Edits a file by replacing an exact, unique string with a new string.

        This is a literal, byte-for-byte replacement. The tool performs NO escape
        processing: the characters in old_string/new_string are written exactly as
        given. In particular, the two characters backslash+n are written as
        backslash+n, not as a newline; conversely a real newline in the argument is
        written as a real newline. When editing formats that store escapes literally
        (e.g. JSON or Jupyter notebooks, where a newline inside a string is the two
        characters \\n), escaping is entirely the caller's responsibility.

        old_string must be an exact, unique substring of the file. If it is absent
        you get "old_string not found"; if it occurs more than once you get a
        non-unique error. Copy old_string verbatim from read_file output (which
        preserves trailing whitespace) to avoid mismatches.

        This tool does not validate the file format. It reports success once the
        substitution is written, even if the result is no longer valid JSON/HTML/etc.
        For whole-file or format-critical rewrites, prefer write_file, then re-read
        to verify.

        Paths excluded by .gitignore or .aiignore are refused.
        """
        try:
            safe_path = get_safe_path(path)
        except ValueError as e:
            return str(e)

        if not safe_path.is_file():
            return f"Error: File '{path}' does not exist."

        ignored_error = ensure_not_ignored(safe_path, path, ignore)
        if ignored_error is not None:
            return ignored_error

        with open(safe_path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}."
        if count > 1:
            return f"Error: old_string is not unique (found {count} times). Provide more surrounding lines to make it unique."

        new_content = content.replace(old_string, new_string)
        with open(safe_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return f"Successfully edited {path}."

    return configured_edit_file


def create_append_file_tool(respect_ignore_files: bool = True):
    ignore = get_workspace_ignore(respect_ignore_files)

    @tool("append_file")
    def configured_append_file(path: str, content: str) -> str:
        """Appends text to the very end of an existing file.

        Paths excluded by .gitignore or .aiignore are refused.
        """
        try:
            safe_path = get_safe_path(path)
        except ValueError as e:
            return str(e)

        if not safe_path.is_file():
            return (
                f"Error: File '{path}' does not exist. Use write_file to create it first."
            )

        ignored_error = ensure_not_ignored(safe_path, path, ignore)
        if ignored_error is not None:
            return ignored_error

        with open(safe_path, "r+", encoding="utf-8") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()

            if file_size > 0:
                f.seek(file_size - 1)
                last_char = f.read(1)
                if last_char != "\n":
                    f.write("\n")

            f.write(content)

        return f"Successfully appended {len(content)} characters to {path}."

    return configured_append_file


def create_delete_path_tool(respect_ignore_files: bool = True):
    ignore = get_workspace_ignore(respect_ignore_files)

    @tool("delete_path")
    def configured_delete_path(path: str) -> str:
        """Deletes a single file or a single empty directory.

        Directories are removed only when they are empty; a directory that
        contains anything (including only ignored entries) is refused, and
        nothing is ever removed recursively. Paths excluded by .gitignore or
        .aiignore are refused, as are paths that resolve outside the workspace
        and the workspace root itself.

        Paths are resolved (expanding ``..``, ``~``, and symlinks) before the
        deletion target is chosen, so a symlink that points outside the
        workspace is refused and a symlink to an in-workspace target deletes
        the target rather than the link.
        """
        try:
            safe_path = get_safe_path(path)
        except ValueError as e:
            return str(e)

        if safe_path == TRUSTED_ROOT:
            return "Error: Refusing to delete the workspace root."

        if not safe_path.exists():
            return f"Error: Path '{path}' does not exist."

        is_dir = safe_path.is_dir()
        ignored_error = ensure_not_ignored(
            safe_path, path, ignore, is_dir=is_dir
        )
        if ignored_error is not None:
            return ignored_error

        if is_dir:
            try:
                if next(safe_path.iterdir(), None) is not None:
                    return (
                        f"Error: Directory '{path}' is not empty; only empty "
                        "directories can be deleted."
                    )
            except OSError as e:
                return f"Error: Failed to inspect directory '{path}': {e}"
            try:
                safe_path.rmdir()
            except OSError as e:
                return f"Error: Failed to delete directory '{path}': {e}"
            return f"Successfully deleted empty directory {path}."

        try:
            safe_path.unlink()
        except OSError as e:
            return f"Error: Failed to delete file '{path}': {e}"
        return f"Successfully deleted file {path}."

    return configured_delete_path


# Retain importable default tools for library users and backwards compatibility.
write_file = create_write_file_tool()
edit_file = create_edit_file_tool()
append_file = create_append_file_tool()
delete_path = create_delete_path_tool()


def prepare_message_content(
    prompt: str, file_context: str, image_path: str, clipboard_context: str = None
) -> list | str:
    """
    Prepare message content with file context and optional image.

    Args:
        prompt (str): The user prompt
        file_context (str): File context to prepend
        image_path (str): Path to image file (optional)
        clipboard_context (str): Clipboard context to append after file context (optional)

    Returns:
        list | str: Message content as list (with image) or string (without image)
    """
    extended_context = ""
    # Prepend file context to the message
    if file_context:
        extended_context = file_context

    # Append clipboard context if provided
    if clipboard_context:
        extended_context = f"{extended_context}\n\n{clipboard_context}"

    prompt = extended_context + "\n\n" + prompt

    # Prepare message content
    if image_path:
        base64_data, mime_type = load_image(image_path)
        message_content = [
            {"type": "text", "text": prompt},
            {
                "type": "image",
                "base64": base64_data,
                "mime_type": mime_type,
            },
        ]
    else:
        message_content = prompt

    return message_content


async def invoke_with_tool_approval(
    agent, agent_input, runnable_config, non_interactive: bool = False
):
    """Invoke an agent and resume any human-in-the-loop tool interruptions."""
    result = await agent.ainvoke(agent_input, config=runnable_config)

    while True:
        interrupts = (
            result.get("__interrupt__") if isinstance(result, dict) else None
        )
        if not interrupts:
            break
        payload = await build_resume_payload(interrupts, non_interactive)
        if payload is None:
            raise RuntimeError("Tool approval interrupt contained no tool calls")
        result = await agent.ainvoke(
            Command(resume=payload), config=runnable_config
        )

    return result


async def stream_response(
    agent,
    message_content,
    runnable_config,
    non_interactive: bool = False,
    atomic_output: bool = False,
):
    """Stream an agent response while keeping one-shot stdout machine-clean.

    Approval decisions are derived from the graph's human-in-the-loop interrupt
    payload, not from the streamed tool-call chunks. Only gated calls appear in
    that payload; auto-run tools execute inside the same run and never prompt.
    This keeps the number of decisions aligned with the interrupt batch, which
    the middleware requires.

    In one-shot mode (``atomic_output=True``), text from each attempt is buffered.
    Failed attempts are discarded, and text from cycles that pause for review is
    treated as progress rather than as the final result. Interactive chat keeps
    its live token streaming behavior.
    """
    cont = True
    agent_input = {"messages": [{"role": "user", "content": message_content}]}
    while cont:
        cont = False

        async def _do_stream():
            attempt_text: list[str] = []
            final_text: Optional[str] = None
            # v3 event streaming (astream_events/_apregel_stream_v3 and
            # AsyncGraphRunStream) is still marked beta by LangGraph, which
            # emits a LangChainBetaWarning for each. These are expected in
            # this usage, so suppress them rather than leaking noise to the
            # user.
            with suppress_langchain_beta_warning():
                stream = await agent.astream_events(
                    agent_input, version="v3", config=runnable_config
                )
            async for message in stream.messages:
                message_text: list[str] = []
                has_tool_calls = False
                async for delta in message.text:
                    message_text.append(delta)
                    if atomic_output:
                        attempt_text.append(delta)
                    else:
                        write_result(delta, end="", flush=True)

                async for _chunk in message.tool_calls:
                    has_tool_calls = True

                # A message without tool calls is a candidate final answer.
                # Messages that request tools are progress, not the result.
                if not has_tool_calls:
                    final_text = "".join(message_text)

            # Driving the stream to completion surfaces the pause, if any.
            interrupted = await stream.interrupted()
            interrupts = await stream.interrupts() if interrupted else []
            return final_text, "".join(attempt_text), interrupts

        final_text, streamed_text, interrupts = await retry_with_backoff(_do_stream)

        if interrupts:
            cont = True
            if atomic_output and streamed_text:
                write_diagnostic(streamed_text)
            for action_request, _review_config in extract_hitl_actions(interrupts):
                tool_name = str(action_request.get("name", "unknown"))
                tool_args = format_tool_arguments(action_request.get("args", ""))
                write_diagnostic(f"Tool call: {tool_name}\nArguments: {tool_args}")

            payload = await build_resume_payload(interrupts, non_interactive)
            if payload is None:
                raise RuntimeError("Tool approval interrupt contained no tool calls")
            agent_input = Command(resume=payload)
        elif atomic_output:
            write_result(final_text or "")

        if not atomic_output:
            write_result("")


@app.command()
@cli_error_boundary
async def chat(
    model_name: str = typer.Option("gpt-5.6-terra", help="Name of the LLM model to use"),
    api_key: str = typer.Option(None, help="API key for ChatOpenAI"),
    api_key_file: str = typer.Option(
        None,
        help="Read the API key from a UTF-8 file; cannot be combined with --api-key",
    ),
    base_url: str = typer.Option(
        None,
        help="Base URL for the LLM API (or set MINUTUS_OPENAI_BASE_URL)",
    ),
    files: list[str] = typer.Option([], help="Files to include as context"),
    image: str = typer.Option(
        None, help="Path to image file to include in the first message"
    ),
    system_prompt: str = typer.Option("", help="System prompt for the AI"),
    prompt: str = typer.Option(
        None,
        help="Prompt to use as user message (if set, runs in single query mode).",
    ),
    prompt_file: str = typer.Option(
        None,
        help="Path to file containing prompt to use as user message (if set, runs in single query mode)."
        "Use '-' to read from stdin",
    ),
    system_prompt_file: str = typer.Option(
        None,
        help="Path to file containing system prompt to use (if set, overrides system-prompt)",
    ),
    mcp_config_json: str = typer.Option(
        None,
        help=(
            "Load an MCP configuration. Stdio entries execute local commands "
            "during tool discovery with the current user's privileges; use only "
            "trusted configurations"
        ),
    ),
    print_tool_names: bool = typer.Option(
        False, help="Print tool names and exit; does not require an API key"
    ),
    auto_run_tools: list[str] = typer.Option(
        [],
        help=(
            "Enabled tool names allowed to run without confirmation; unknown "
            "names are errors. Use only with trusted inputs or in an isolated "
            "environment"
        ),
    ),
    auto_run_all_tools: bool = typer.Option(
        False,
        help=(
            "Allow every tool to run without confirmation; use only with "
            "trusted inputs or in an isolated environment"
        ),
    ),
    non_interactive: bool = typer.Option(
        False,
        help=(
            "Never prompt for tool approval. Tool calls not authorized by "
            "--auto-run-tools or --auto-run-all-tools are rejected."
        ),
    ),
    temperature: float = typer.Option(None, help="Temperature for model"),
    structured_output: str = typer.Option(
        None, help="Path to JSON schema file for structured output"
    ),
    structured_output_strategy: StructuredOutputStrategy = typer.Option(
        StructuredOutputStrategy.provider,
        help=(
            "Structured output strategy: 'provider' uses provider-native JSON "
            "Schema; 'tool' uses tool calling"
        ),
    ),
    clipboard: bool = typer.Option(False, help="Enable clipboard context loading"),
    max_input_lines: int = typer.Option(
        DEFAULT_MAX_INPUT_LINES,
        help=(
            "Maximum lines retained per input source or tool result; shell "
            "output retains the last lines"
        ),
    ),
    summarization_context_size: int = typer.Option(
        None,
        help="Context size for summarization. If not set, summarization is disabled.",
    ),
    use_shell_command_tool: bool = typer.Option(
        False,
        help=(
            "Enable unrestricted shell commands with the current user's "
            "privileges; commands are not workspace-sandboxed"
        ),
    ),
    shell_command_timeout: float = typer.Option(
        30.0,
        help="Maximum execution time in seconds for each shell command",
    ),
    use_read_url_tool: bool = typer.Option(
        False,
        help=(
            "Enable URL fetching; requests are not network-sandboxed and may "
            "reach local or internal resources"
        ),
    ),
    read_only_workspace_tools: bool = typer.Option(
        False,
        help="Enable read-only workspace tools (list_files, read_file, search_files)",
    ),
    read_write_workspace_tools: bool = typer.Option(
        False,
        help=(
            "Enable read-write workspace tools (list_files, read_file, search_files, "
            "write_file, edit_file, append_file, delete_path); includes read-only mode "
            "and takes precedence if both workspace flags are supplied"
        ),
    ),
    respect_ignore_files: bool = typer.Option(
        True,
        "--respect-ignore-files/--no-ignore-files",
        help=(
            "Honor .gitignore and .aiignore when listing, reading, searching, and "
            "editing workspace files"
        ),
    ),
    debug: bool = typer.Option(
        False, help="Show tracebacks for unexpected errors"
    ),
):
    """
    Start interactive chat with AI assistant
    """
    async with AsyncExitStack() as async_stack:
        # Direct Python callers written before this option was added may omit it.
        # Typer resolves OptionInfo defaults only when invoking through the CLI.
        if isinstance(max_input_lines, typer.models.OptionInfo):
            max_input_lines = DEFAULT_MAX_INPUT_LINES

        # Direct Python callers written before this option was added may omit it.
        if isinstance(respect_ignore_files, typer.models.OptionInfo):
            respect_ignore_files = True

        # Validate parameter combinations
        if prompt and prompt_file:
            fail("Error: prompt and prompt-file cannot be set at the same time")

        if system_prompt and system_prompt_file:
            fail(
                "Error: system-prompt and system-prompt-file cannot be set at the same time"
            )

        if api_key is not None and api_key_file is not None:
            fail("Error: api-key and api-key-file cannot be set at the same time")

        if non_interactive and prompt is None and prompt_file is None:
            fail("Error: non-interactive requires prompt or prompt-file")

        validate_shell_command_timeout(shell_command_timeout)
        validate_max_input_lines(max_input_lines)

        # Structured output is only available in single-query mode.
        if structured_output and prompt is None and prompt_file is None:
            fail(
                "Error: structured-output can only be used in query mode with "
                "a prompt or prompt-file."
            )

        # Load the system prompt before constructing the agent.
        if system_prompt_file:
            system_prompt = load_file_content(system_prompt_file)

        # Load MCP tools if config is provided
        tools = []
        if mcp_config_json:
            mcp_config = load_mcp_config(mcp_config_json)
            try:
                client = MultiServerMCPClient(mcp_config)

                # 1. Enter all contexts sequentially in the main task (Safe for AsyncExitStack)
                sessions = []
                for mcp_connection_name in client.connections.keys():
                    session = await async_stack.enter_async_context(
                        client.session(mcp_connection_name)
                    )
                    sessions.append(session)

                # 2. Load the tools from all sessions concurrently (Efficient)
                async def fetch_tools(session):
                    return await load_mcp_tools(session)

                tasks = [fetch_tools(s) for s in sessions]
                results = await asyncio.gather(*tasks)

                # Flatten the results
                for tool_list in results:
                    tools.extend(tool_list)

            except ImportError:
                fail(
                    "Error: langchain_mcp_adapters not installed. Please install it to use MCP tools."
                )
            except Exception as e:
                fail(f"Error creating MCP client: {e}")

        # Add the shell command tool with the timeout configured by the user.
        if use_shell_command_tool:
            tools.append(
                create_shell_command_tool(shell_command_timeout, max_input_lines)
            )
            system_prompt = append_shell_context(
                system_prompt, get_system_shell_name(), TRUSTED_ROOT
            )

        # Add read URL tool if enabled
        if use_read_url_tool:
            tools.append(create_read_url_tool(max_input_lines))

        configured_list_files = create_list_files_tool(
            max_input_lines, respect_ignore_files
        )
        configured_read_file = create_read_file_tool(
            max_input_lines, respect_ignore_files
        )
        configured_search_files = create_search_files_tool(
            max_input_lines, respect_ignore_files
        )

        # Read-write mode includes read-only tools and takes precedence when both
        # workspace flags are supplied.
        if read_write_workspace_tools:
            tools.extend(
                [
                    configured_list_files,
                    configured_read_file,
                    configured_search_files,
                    create_write_file_tool(respect_ignore_files),
                    create_edit_file_tool(respect_ignore_files),
                    create_append_file_tool(respect_ignore_files),
                    create_delete_path_tool(respect_ignore_files),
                ]
            )
        elif read_only_workspace_tools:
            tools.extend(
                [configured_list_files, configured_read_file, configured_search_files]
            )

        tools_names = [tool.name for tool in tools]

        # Tool discovery and printing do not require LLM credentials. Keep this
        # as a discovery-only operation, irrespective of auto-run options.
        if print_tool_names:
            write_result(f"Available tools: {', '.join(tools_names)}")
            return

        # MCP names are not available until discovery has completed. Validate
        # immediately afterwards, before credentials, model, or agent creation.
        # Explicit names are checked even when --auto-run-all-tools is also set,
        # so configuration mistakes never pass silently.
        validate_auto_run_tools(auto_run_tools, tools_names)

        api_key = resolve_api_key(api_key, api_key_file)
        base_url = base_url or os.getenv("MINUTUS_OPENAI_BASE_URL")
        model = ChatOpenAI(
            base_url=base_url,
            model=model_name,
            api_key=api_key,
            temperature=temperature,
        )

        # Configure structured output on the agent. Provider-native output is the
        # default; tool-based output deliberately does not ask the model to retry
        # schema errors.
        response_format = None
        if structured_output:
            json_schema = load_json_schema(structured_output)
            if structured_output_strategy == StructuredOutputStrategy.provider:
                response_format = ProviderStrategy(json_schema)
            else:
                response_format = ToolStrategy(json_schema, handle_errors=False)

        checkpointer = InMemorySaver()
        middleware = []
        if summarization_context_size is not None:
            middleware.append(
                SummarizationMiddleware(
                    model=model,
                    trigger=("tokens", round(summarization_context_size * 0.7)),
                    keep=("tokens", round(summarization_context_size * 0.3)),
                    trim_tokens_to_summarize=None,
                )
            )

        # Create interrupt_on dictionary with False for auto_run_tools.
        interrupt_on_dict = {
            name: (name not in auto_run_tools and not auto_run_all_tools)
            for name in tools_names
        }
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on_dict))
        middleware.append(ToolRunningMiddleware())
        middleware.append(ToolErrorHandlingMiddleware())

        agent = create_agent(
            model=model,
            system_prompt=system_prompt,
            middleware=middleware,
            tools=tools,
            checkpointer=checkpointer,
            response_format=response_format,
        )

        runnable_config = RunnableConfig({"configurable": {"thread_id": 1}})

        # Load file context if provided
        file_context = (
            load_files_context(files, max_input_lines) if files else ""
        )

        # Load clipboard context if enabled
        clipboard_context = (
            load_clipboard_context(max_input_lines) if clipboard else ""
        )

        # Single query mode if a prompt is provided.
        if prompt is not None or prompt_file is not None:
            # Handle prompt loading.
            if prompt_file == "-":
                prompt = truncate_first_lines(sys.stdin.read(), max_input_lines)
            elif prompt_file:
                prompt = truncate_first_lines(
                    load_file_content(prompt_file), max_input_lines
                )

            message_content = prepare_message_content(
                prompt, file_context, image, clipboard_context
            )

            if structured_output:
                # Do not use the broad retry wrapper here. In particular,
                # ToolStrategy(handle_errors=False) validation failures must
                # propagate instead of restarting the entire agent invocation.
                result = await invoke_with_tool_approval(
                    agent,
                    {"messages": [{"role": "user", "content": message_content}]},
                    runnable_config,
                    non_interactive=non_interactive,
                )
                write_result(json.dumps(result["structured_response"]))
            else:
                await stream_response(
                    agent,
                    message_content,
                    runnable_config,
                    non_interactive=non_interactive,
                    atomic_output=True,
                )
            return

        # Interactive chat loop with prompt_toolkit
        write_diagnostic("type quit to exit")
        first_message = True

        # Create session with multiline input and frame
        chat_session = PromptSession(
            bottom_toolbar=lambda: HTML(
                "<b>Tip:</b> Press <b>Alt+Enter</b> to send, <b>Enter</b> for new"
                "line, <b>exit</b> to quit"
            ),
            style=Style.from_dict(
                {
                    "frame.border": "#808080",
                    "toolbar": "#ffffff bg:#333333",
                }
            ),
            include_default_pygments_style=False,
            output=stderr_prompt_output(),
        )

        while True:
            try:
                user_input = await chat_session.prompt_async(
                    HTML("<b>You</b>: "),
                    multiline=True,
                    show_frame=True,
                    wrap_lines=True,
                    mouse_support=False,
                    # File context is injected by prepare_message_content() for
                    # the first message. Keep the editor empty so the context is
                    # not submitted twice.
                    default="",
                )

                # Handle empty input
                if not user_input.strip():
                    continue

                if user_input.lower().strip() in ["exit", "quit"]:
                    write_diagnostic("Goodbye!")
                    break

                # Prepare message content
                if first_message:
                    message_content = prepare_message_content(
                        user_input, file_context, image, clipboard_context
                    )
                else:
                    message_content = user_input

                first_message = False

                # Stream response
                await stream_response(
                    agent,
                    message_content,
                    runnable_config,
                )
            except KeyboardInterrupt:
                write_diagnostic("\nInput cancelled.")
                continue
            except EOFError:
                write_diagnostic("\nExiting...")
                break


if __name__ == "__main__":
    app()
