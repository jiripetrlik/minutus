# Minutus

![Minutus Logo](assets/minutus.png)

**Minutus** is a lightweight, minimalistic AI assistant designed as a simpler alternative to feature-heavy AI and coding tools. It focuses on essential capabilities—including interactive conversations, tool use, MCP integration, and human approval—while remaining equally convenient for terminal use and automation in shell or other scripts. Security is a core design priority, with explicit tool authorization, workspace path containment, and clear boundaries around potentially privileged operations.

## Quick Start

Install Minutus directly from GitHub, set your API key, and start an
interactive session:

```bash
pipx install git+https://github.com/jiripetrlik/minutus.git
export MINUTUS_OPENAI_API_KEY="your-api-key"
minutus
```

On Windows PowerShell, set the API key with:

```powershell
$env:MINUTUS_OPENAI_API_KEY = "your-api-key"
minutus
```

To run without installing, use
`uvx --from git+https://github.com/jiripetrlik/minutus.git minutus`. See
[Installation](#installation) for other installation methods, [Usage](#usage)
for common workflows, and [Configuration](#configuration) for model and
endpoint options.

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
  - [Interactive Mode](#interactive-mode)
  - [Single Query Mode](#single-query-mode)
  - [With File Context](#with-file-context)
  - [With Image (Vision)](#with-image-vision)
  - [With Clipboard Context](#with-clipboard-context)
  - [With Workspace Tools](#with-workspace-tools)
  - [With Shell Command Tool](#with-shell-command-tool)
  - [With URL Reading Tool](#with-url-reading-tool)
  - [With MCP Tools](#with-mcp-tools)
  - [With Auto-Run Tools](#with-auto-run-tools)
  - [Tool Approval](#tool-approval)
  - [Non-Interactive Execution](#non-interactive-execution)
  - [Output Streams](#output-streams)
  - [With Structured Output](#with-structured-output)
- [Configuration](#configuration)
  - [Exit Status](#exit-status)
  - [Environment Variables](#environment-variables)
- [Docker](#docker)
  - [Build](#build)
  - [Docker Single-Query Mode](#docker-single-query-mode)
  - [Docker Interactive Mode](#docker-interactive-mode)
  - [Secrets](#secrets)
  - [Container Limitations and Security](#container-limitations-and-security)
  - [Smoke Tests](#smoke-tests)
- [Platform Notes](#platform-notes)
- [Development](#development)
  - [Setup](#setup)
  - [Test Environment Variables](#test-environment-variables)
  - [Project Structure](#project-structure)
- [License](#license)

## Features

- **Interactive & Single-Query Modes** — Rich terminal chat UI or one-shot queries via command-line flags
- **Context Injection** — Pass files, clipboard contents, or images as context for the AI; local PDF and HTML files are converted to text automatically
- **Built-in Tools** — File system operations (read, write, edit, search), shell command execution, and URL fetching
  - `search_files` treats the query as a regular expression by default; pass `regex=false` (or escape metacharacters) to match a literal string
  - `edit_file` and `write_file` write content literally and perform no escape processing — escaping is the caller's responsibility
  - `read_file` returns one numbered line per physical line and preserves trailing whitespace (pass `show_whitespace=true` to make it visible)
- **MCP Integration** — Connect to external MCP servers for extended tool capabilities
- **Human-in-the-Loop Approval** — Approve or reject tool calls before execution; only tools that require review are surfaced, and auto-run tools execute in the same turn
- **Workspace Path Containment** — Built-in workspace file tools restrict paths to the working directory captured when Minutus starts; explicitly supplied CLI paths and other tools are not sandboxed
- **Conversation Summarization** — Automatically summarizes long conversations to manage context limits
- **Structured Output** — Supports JSON schema-based structured output for programmatic use
- **Streaming Responses** — Interactive responses stream in real time; one-shot responses are buffered for atomic stdout output

## Installation

Install Minutus from the GitHub repository at
[jiripetrlik/minutus](https://github.com/jiripetrlik/minutus).

### Using `pipx`

```bash
pipx install git+https://github.com/jiripetrlik/minutus.git
```

### Using `uvx` (no install needed)

```bash
uvx --from git+https://github.com/jiripetrlik/minutus.git minutus --help
```

### From source

```bash
git clone https://github.com/jiripetrlik/minutus.git
cd minutus
uv tool install .
```

## Usage

Set the API key in the environment before running Minutus:

```bash
export MINUTUS_OPENAI_API_KEY="your-api-key"
```

Alternatively, use `--api-key-file` to read it from a UTF-8 file:

```bash
minutus --api-key-file /run/secrets/openai-api-key --prompt "What is 2+2?"
```

The `--api-key` option is also supported, but may expose the key in shell
history or process listings.

### Interactive Mode

```bash
minutus --model-name gpt-5.6-terra
```

### Single Query Mode

```bash
minutus --model-name gpt-5.6-terra --prompt "What is 2+2?"
```

### With File Context

```bash
minutus --model-name gpt-5.6-terra \
  --files ./README.md \
  --prompt "Summarize this file"
```

### With Image (Vision)

```bash
minutus --model-name gpt-5.6-terra \
  --image ./screenshot.png \
  --prompt "Describe what's in this image"
```

### With Clipboard Context

```bash
minutus --model-name gpt-5.6-terra \
  --clipboard \
  --prompt "Explain the code in my clipboard"
```

### With Workspace Tools

The built-in workspace file tools restrict requested paths to the process's
initial working directory. This applies only to `list_files`, `read_file`,
`search_files`, `write_file`, `edit_file`, and `append_file`. Paths explicitly
provided through CLI options—such as `--files`, `--image`, `--prompt-file`,
`--system-prompt-file`, `--structured-output`, `--api-key-file`, and
`--mcp-config-json`—are user-authorized paths and may point outside the
workspace.

This path validation is a defense-in-depth guard against ordinary path
traversal, not a high-assurance filesystem sandbox. Shell commands, URL
fetching, and MCP servers are not restricted to the workspace. For untrusted
workloads, run Minutus in a container, virtual machine, sandboxed subprocess,
or under a restricted OS user.

Prompts and enabled context sources may be transmitted to the configured model
provider. This can include system prompts, explicitly selected files and
images, clipboard contents, fetched URL content, tool results, MCP results, and
conversation history. When `--base-url` is used, that configured endpoint is
the model provider receiving this data.

```bash
# Read-only tools (list_files, read_file, search_files)
minutus --model-name gpt-5.6-terra \
  --read-only-workspace-tools \
  --prompt "List all Python files in this directory"

# Read-write tools (adds write_file, edit_file, append_file)
minutus --model-name gpt-5.6-terra \
  --read-write-workspace-tools \
  --prompt "Create a new file called hello.py"
```

Read-write mode includes all read-only workspace tools. If both workspace flags
are supplied, read-write mode takes precedence and each tool is enabled only
once.

### With Shell Command Tool

> **Warning:** Shell commands run with the current OS user's privileges. They
> are not restricted to the workspace and may read or modify other files, use
> the network, or alter the system. Use this tool only with trusted inputs or
> inside an isolated environment.

```bash
minutus --model-name gpt-5.6-terra \
  --use-shell-command-tool \
  --shell-command-timeout 30 \
  --prompt "Check the disk usage of this system"
```

Commands use the platform's system shell and have a 30-second timeout by
default. Configure the per-command limit with `--shell-command-timeout`.
When the tool is enabled, Minutus provides the model with the detected shell
name and initial workspace directory. The workspace value is informational;
shell commands are not confined to that directory.

### With URL Reading Tool

> **Warning:** URL fetching is not network-sandboxed and may access local or
> internal services. Fetched content is untrusted and may contain prompt
> injection; it may also be transmitted to the configured model provider.

```bash
minutus --model-name gpt-5.6-terra \
  --use-read-url-tool \
  --prompt "Summarize https://example.com"
```

### With MCP Tools

> **Warning:** MCP configurations are executable configuration, not harmless or
> passive JSON. Loading a configuration starts its `stdio` commands during tool
> discovery, with the current OS user's privileges, before any model tool call or
> tool-approval prompt. Those commands and MCP tools are not restricted to the
> workspace and can access files, the network, or the rest of the system. Inspect
> every command and argument and load only trusted configurations and servers;
> otherwise run Minutus in an isolated environment.

```bash
minutus --model-name gpt-5.6-terra \
  --mcp-config-json ./mcp-config.json \
  --prompt "What time is it?"
```

Example MCP config file (`mcp-config.json`) using the [time server](https://pypi.org/project/mcp-server-time/):

```json
{
    "time": {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_server_time"]
    }
}
```

### With Auto-Run Tools

> **Warning:** Auto-run allows selected tools to execute without interactive
> approval. Model-generated actions can be influenced by prompt injection in
> prompts, files, web content, or MCP results. Enable auto-run only for trusted
> inputs or in an isolated environment.

```bash
# Auto-run specific tools (no confirmation needed)
minutus --model-name gpt-5.6-terra \
  --read-only-workspace-tools \
  --auto-run-tools list_files read_file \
  --prompt "Find all TODO comments in this project"

# Auto-run all tools
minutus --model-name gpt-5.6-terra \
  --read-only-workspace-tools \
  --auto-run-all-tools \
  --prompt "Find all TODO comments in this project"
```

### Tool Approval

Every enabled tool is gated by default: when the model proposes a call, Minutus
pauses before execution and asks you to allow or reject it. Approval decisions
come from the run's human-in-the-loop interrupt, so only tools that actually
require review are shown. A single model turn may call several tools, and each
gated call is reviewed in the order it was requested.

Tools authorized with `--auto-run-tools` or `--auto-run-all-tools` are not part
of that review. They execute within the same model turn without prompting, so a
turn that mixes an auto-run tool with a gated one prompts only for the gated
tool.

Rejecting a call does not stop the run: the rejection is returned to the model
so it can continue, for example by taking a safer alternative or explaining
what it could not do.

### Non-Interactive Execution

```bash
minutus --model-name gpt-5.6-terra \
  --read-only-workspace-tools \
  --auto-run-tools list_files \
  --non-interactive \
  --prompt "Summarize this workspace"
```

`--non-interactive` is available in single-query mode with `--prompt` or
`--prompt-file`. It never prompts for tool approval. Tools authorized with
`--auto-run-tools` or `--auto-run-all-tools` may execute; all other tool calls
are rejected and the rejection is returned to the model so it can continue.
Names passed to `--auto-run-tools` must match tools enabled for that invocation;
unknown names are reported before the model and agent are created, including
when `--auto-run-all-tools` is also supplied. Use `--print-tool-names` with the
same tool-enabling options to discover valid names.
An automatic rejection does not by itself cause a nonzero exit status. Minutus
uses the same behavior as a safety fallback whenever approval is required but
standard input is not a TTY.

### Output Streams

Single-query output follows a script-friendly stream contract:

- **stdout** contains only the final assistant answer, requested JSON, or other
  explicitly requested data such as `--print-tool-names`.
- **stderr** contains retries, tool progress, approval UI, warnings, and other
  diagnostics. When a tool begins execution, Minutus writes
  `Running tool: [tool-name]` to stderr.

One-shot responses are buffered until an attempt succeeds, so a failed stream
cannot leave a partial answer on stdout. Text produced before a tool call is
reported as progress on stderr; only the terminal assistant response is written
to stdout. When a run pauses for approval, the pending `Tool call: ...` details
are written to stderr as diagnostics, never to stdout.

```bash
# Capture the result while retaining diagnostics separately.
minutus --prompt "Summarize this workspace" \
  >answer.txt 2>diagnostics.log

# stdout from structured output can be parsed directly.
minutus --prompt "Extract the data" --structured-output schema.json | jq .
```

Interactive prompts and status messages use stderr, while assistant responses
continue to use stdout.

### Input line limits

`--max-input-lines` limits each loaded file, prompt file or stdin prompt,
clipboard value, URL result, and read-only workspace-tool result independently.
The default is 5,000 lines. These inputs retain their first lines; shell-command
output retains its last lines so errors and summaries at the end remain visible.
Existing fixed limits in `list_files`, `read_file`, and `search_files` are
replaced by this option.

```bash
minutus --max-input-lines 5000 --files large.log \
  --use-shell-command-tool --prompt "Investigate this log"
```

The complete file, download, or subprocess output may still be read into memory
before its model-facing text is truncated.

### With Structured Output

Provider-native structured output is used by default:

```bash
minutus --model-name gpt-5.6-terra \
  --prompt "What is 2+2?" \
  --structured-output ./schema.json
```

Use the tool-calling strategy for models that support tool calling but not
provider-native JSON Schema output:

```bash
minutus --model-name gpt-5.6-terra \
  --prompt "What is 2+2?" \
  --structured-output ./schema.json \
  --structured-output-strategy tool
```

The `provider` strategy requires provider-native structured-output support.
The `tool` strategy requires tool-calling support and uses
`handle_errors=False`: schema-validation failures terminate the command rather
than asking the model to correct its response. Minutus does not automatically
fall back between strategies.

## Configuration

Minutus uses the OpenAI-compatible API format. The following options are available:

| Option | Description | Default |
|---|---|---|
| `--model-name` | Name of the LLM model to use | `gpt-5.6-terra` |
| `--api-key` | API key (or set `MINUTUS_OPENAI_API_KEY`) | — |
| `--api-key-file` | Read the API key from a UTF-8 file | — |
| `--base-url` | Base URL for the LLM API (or set `MINUTUS_OPENAI_BASE_URL`) | — |
| `--temperature` | Temperature for model | — |
| `--system-prompt` | System prompt for the AI | — |
| `--system-prompt-file` | Path to file containing system prompt | — |
| `--prompt` | Prompt for single-query mode | — |
| `--prompt-file` | Path to file containing prompt (use `-` for stdin) | — |
| `--files` | Files to include as context (can be repeated) | — |
| `--image` | Path to image file for vision | — |
| `--clipboard` | Enable clipboard context loading | `False` |
| `--max-input-lines` | Maximum lines retained per input source or tool result; regular inputs keep the first lines and shell output keeps the last lines | `5000` |
| `--mcp-config-json` | Load an MCP configuration; `stdio` entries execute local commands during discovery, so use only trusted configurations | — |
| `--use-shell-command-tool` | Enable shell command tool | `False` |
| `--use-read-url-tool` | Enable URL reading tool | `False` |
| `--read-only-workspace-tools` | Enable read-only file tools | `False` |
| `--read-write-workspace-tools` | Enable read-write file tools | `False` |
| `--auto-run-tools` | Enabled tool names that run without confirmation; unknown names are errors (can be repeated) | — |
| `--auto-run-all-tools` | All tools run without confirmation | `False` |
| `--non-interactive` | Never prompt; reject tool calls not explicitly authorized | `False` |
| `--structured-output` | Path to JSON schema for structured output | — |
| `--structured-output-strategy` | Structured output strategy: `provider` or `tool` | `provider` |
| `--summarization-context-size` | Context size for auto-summarization | — |
| `--print-tool-names` | Print available tool names and exit; no API key required | `False` |
| `--debug` | Show tracebacks for unexpected errors | `False` |

### Exit Status

Minutus returns `0` when the command completes successfully and a non-zero
status when it fails. Typer may use its own non-zero status for invalid command
line syntax. Errors are written to stderr and unexpected errors do not include
a traceback by default; pass `--debug` to show one.

A rejected or failed tool call is recoverable: if the model still produces a
final response, the command is considered successful.

### Environment Variables

| Variable | Description |
|---|---|
| `MINUTUS_OPENAI_API_KEY` | API key for the LLM (used if neither key option is provided) |
| `MINUTUS_OPENAI_BASE_URL` | Base URL for the LLM API (used if `--base-url` is not provided) |

## Docker

The Docker image runs Minutus as a non-root CLI application. It uses
`/workspace` as its working directory, which means that a project mounted there
becomes the trusted root for the built-in workspace tools. The image does not
expose any ports; outbound network access is required when calling a model API,
fetching URLs, or using network-backed MCP servers.

### Build

```bash
docker build -t minutus:local .
```

The image uses Python 3.14 slim and installs production dependencies exactly
from `uv.lock`.

### Docker Single-Query Mode

Forward an API key from the host environment and mount the current project:

```bash
docker run --rm \
  -e MINUTUS_OPENAI_API_KEY \
  -v "$PWD:/workspace:ro" \
  minutus:local \
  --non-interactive \
  --read-only-workspace-tools \
  --prompt "Summarize this workspace"
```

Remove `:ro` and enable `--read-write-workspace-tools` only when Minutus should
be able to modify the mounted project. Files created in a writable bind mount
can be made to use the host user's ownership by adding this on Linux:

```bash
--user "$(id -u):$(id -g)"
```

The virtual environment is world-readable and executable, so the image also
supports this host-user mode.

### Docker Interactive Mode

Allocate a TTY for the terminal interface and approval prompts:

```bash
docker run --rm -it \
  -e MINUTUS_OPENAI_API_KEY \
  -v "$PWD:/workspace" \
  minutus:local
```

### Secrets

Never place credentials in the Dockerfile, build arguments, or image. In
addition to forwarding `MINUTUS_OPENAI_API_KEY`, a key can be mounted read-only
and passed through the existing file option:

```bash
docker run --rm \
  --mount type=bind,src=/secure/openai-key,dst=/run/secrets/openai-key,readonly \
  -v "$PWD:/workspace:ro" \
  minutus:local \
  --api-key-file /run/secrets/openai-key \
  --non-interactive \
  --prompt "What is 2+2?"
```

`MINUTUS_OPENAI_BASE_URL` can be forwarded in the same way when using an
OpenAI-compatible endpoint.

### Container Limitations and Security

- `--clipboard` is generally unavailable in a headless container. Prefer a
  mounted file or `--prompt-file -` with input on stdin.
- Stdio MCP servers must have all of their executables and dependencies inside
  the image. Build a derived image when a server needs additional Python
  packages, Node.js, or other programs.
- The shell tool executes inside the container, but it can still change writable
  mounts and use the network. Do not mount the Docker socket, host root, SSH
  credentials, or other sensitive paths.
- Workspace containment applies only to the built-in workspace file tools.
  Explicit CLI paths, shell commands, URL fetching, and MCP tools retain the
  broader access described elsewhere in this README.

For a read-only, non-interactive workload, additional Docker restrictions can
be applied:

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  -e MINUTUS_OPENAI_API_KEY \
  -v "$PWD:/workspace:ro" \
  minutus:local \
  --non-interactive \
  --read-only-workspace-tools \
  --prompt "Summarize this workspace"
```

Do not use `--network none` when the configured model or enabled tools require
network access.

### Smoke Tests

These commands validate the image without credentials or network API calls:

```bash
docker run --rm minutus:local --help
docker run --rm minutus:local \
  --print-tool-names --read-only-workspace-tools
```

## Platform Notes

- **Linux**: `pyperclip` requires `xclip` or `xsel` to be installed for clipboard support
- **macOS**: Works out of the box
- **Windows**: Works with `pywin32` (installed automatically)

## Development

### Setup

```bash
# Install with test dependencies
uv sync --extra test

# Run tests
uv run pytest tests/
```

### Test Environment Variables

| Variable | Description |
|---|---|
| `MINUTUS_OPENAI_API_KEY` | API key for the LLM (required) |
| `MINUTUS_OPENAI_BASE_URL` | Base URL for the LLM API (optional, for non-default endpoints) |
| `MINUTUS_TESTS_MODEL_NAME` | Model name override (optional, defaults to `gpt-5.6-luna`) |

### Project Structure

The tree below intentionally shows only stable, high-level paths so it does not
become outdated whenever tests or assets are added:

```
minutus/
├── src/minutus/       # Application package and CLI implementation
├── tests/             # Unit and end-to-end tests plus test fixtures
├── assets/            # Project logos and other image assets
├── Dockerfile         # Container image definition
├── pyproject.toml     # Package metadata, dependencies, and tool configuration
├── uv.lock            # Locked dependency versions
├── README.md          # Project documentation
└── LICENSE            # MIT license text
```

## License

MIT
