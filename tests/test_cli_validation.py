import pytest
import os
import pyperclip
import asyncio
import re
import pexpect
from pathlib import Path

# Path to the project root (parent of tests/ directory)
PROJECT_ROOT = Path(__file__).parent.parent

# Path to the minutus module
MINUTUS_MODULE = str(PROJECT_ROOT / "src" / "minutus" / "minutus.py")

pytestmark = pytest.mark.e2e


def test_basic_connectivity(run_minutus):
    """
    Verify that the application can connect to the OpenAI API and generate a response.
    Tests the 'single query mode' with a simple prompt.
    """
    # We use a very simple prompt to minimize token usage/cost during testing
    result = run_minutus([
        "--prompt", "Respond with exactly the word SUCCESS in all uppercase. No other words, no punctuation."
        ])

    assert result.returncode == 0
    assert "success" in result.stdout.lower()

def test_file_context_injection(run_minutus, temp_file_factory):
    """
    Verify that files passed via --files are correctly read and provided to the AI.
    """
    secret_content = "The hidden treasure is located at 45.0, -93.0"
    file_path = temp_file_factory(secret_content)

    result = run_minutus([
        "--prompt", "Where is the treasure?",
        "--files", file_path
    ])

    assert result.returncode == 0
    # The model should extract the coordinates from the provided file context
    assert "45.0" in result.stdout
    assert "93.0" in result.stdout

def test_clipboard_context(run_minutus):
    """
    Verify that text from the system clipboard is successfully injected into the prompt.
    """
    test_string = "CLIPBOARD_DATA_12345"
    pyperclip.copy(test_string)

    result = run_minutus([
        "--prompt", "What is the clipboard data?",
        "--clipboard"
    ])

    assert result.returncode == 0
    assert test_string in result.stdout

def test_invalid_argument_validation(run_minutus):
    """
    Verify that the application correctly identifies and rejects invalid parameter combinations.
    """
    # Testing the error: prompt and prompt-file cannot be set at the same time
    result = run_minutus([
        "--prompt", "Hello",
        "--prompt-file", "-" # Using stdin simulation via '-'
    ])

    assert result.returncode != 0
    assert "Error: prompt and prompt-file cannot be set at the same time" in result.stderr

def test_image_vision_capability(run_minutus):
    """
    Verify that the application can process an image and extract text from it.
    """
    image_path = str(PROJECT_ROOT / "assets" / "minutus-small.png")

    # Raise a standard exception to trigger an "Error" (E) status
    if not os.path.exists(image_path):
        raise ValueError(f"Test image '{image_path}' not found. Please provide it to run this test.")

    result = run_minutus([
        "--prompt", "Read the text in the image.",
        "--image", image_path
    ])

    assert result.returncode == 0, f"Command failed with stderr: {result.stderr}"
    assert "minutus" in result.stdout.lower()

def test_structured_output_parsing(run_minutus, temp_file_factory):
    """
    Verify that the application can handle structured output via a JSON schema.
    """
    import json
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"}
        },
        "required": ["answer"]
    }
    schema_path = temp_file_factory(json.dumps(schema), suffix=".json")

    result = run_minutus([
        "--prompt", "Answer the question: What is 2+2?",
        "--structured-output", schema_path
    ])

    assert result.returncode == 0
    # Check if the output is valid JSON and contains the expected answer
    output_json = json.loads(result.stdout)
    assert "4" in output_json["answer"]

def test_interactive_mode_single_query(openai_api_key, openai_base_url, model_name):
    """
    Verify that the application works in interactive mode for a single query using pexpect.
    This simulates:
    1. Starting the app
    2. Waiting for the prompt
    3. Sending a query with Alt+Enter
    4. Receiving the response
    5. Exiting the app
    """
    cmd = [
        "python", MINUTUS_MODULE,
        "--model-name", model_name
    ]

    cmd.extend(["--api-key", openai_api_key])
    if openai_base_url:
        cmd.extend(["--base-url", openai_base_url])

    # Spawn the process
    child = pexpect.spawn(cmd[0], cmd[1:])

    try:
        # 1. Wait for the interactive prompt
        child.expect(r"You", timeout=10.0)

        # 2. Send the query with Alt+Enter.
        # Alt+Enter is usually ESC (27) followed by Carriage Return (\r).
        # We send the text first, then the Alt+Enter keystroke.
        child.sendline("How much is 10 + 12.1?")
        child.send('\x1b\r')  # Alt+Enter

        # 3. Wait for the response containing the answer
        child.expect(r"22.1", timeout=30.0)

        # Capture the output until now
        output = child.before.decode('utf-8') + child.after.decode('utf-8')

        assert "22.1" in output, f"Expected '22.1' in output, got: {output}"

        # 4. Exit cleanly
        child.expect(r"You", timeout=10.0)
        child.sendline("quit")
        child.send('\x1b\r')  # Alt+Enter
        child.expect(pexpect.EOF, timeout=5.0)

    except pexpect.TIMEOUT as e:
        pytest.fail("Interactive test timed out waiting for response or exit.")
    except Exception as e:
        pytest.fail(f"Interactive test failed: {e}")
    finally:
        child.close()


def test_mcp_tool_call_approval_and_rejection(
    openai_api_key, openai_base_url, model_name
):
    """Verify MCP tool execution and approval/rejection in one interactive session."""
    tests_dir = PROJECT_ROOT / "tests"
    mcp_config = tests_dir / "people-database-mcp.json"
    cmd = [
        "python",
        MINUTUS_MODULE,
        "--model-name",
        model_name,
        "--mcp-config-json",
        str(mcp_config),
        "--api-key",
        openai_api_key,
    ]
    if openai_base_url:
        cmd.extend(["--base-url", openai_base_url])

    # The example MCP config refers to people-database-mcp.py by its relative
    # path, so run Minutus from the tests directory.
    child = pexpect.spawn(cmd[0], cmd[1:], cwd=str(tests_dir), encoding="utf-8")

    try:
        child.expect(r"You", timeout=15.0)

        # Approve an MCP tool call and verify that its result reaches the model.
        child.sendline(
            "You must call the get_age tool to look up Alice. "
            "Report the name and age returned by the tool."
        )
        child.send("\x1b\r")  # Alt+Enter
        child.expect(r"Tool call:\s*get_age", timeout=30.0)
        child.expect(r"Alice", timeout=10.0)
        child.send("a")
        child.expect(r"30", timeout=30.0)
        child.expect(r"You", timeout=10.0)

        # Reject a second MCP tool call and verify that the agent continues
        # without presenting a tool result as though execution succeeded.
        child.sendline(
            "You must call the get_age tool to look up Charlie. If the tool "
            "call is rejected, respond with exactly LOOKUP_REJECTED."
        )
        child.send("\x1b\r")  # Alt+Enter
        child.expect(r"Tool call:\s*get_age", timeout=30.0)
        child.expect(r"Charlie", timeout=10.0)
        child.send("r")
        child.expect(r"Reason for rejection", timeout=10.0)
        child.sendline("The lookup is not authorized; do not call another tool.")
        child.expect(r"LOOKUP_REJECTED", timeout=30.0)

        child.expect(r"You", timeout=10.0)
        child.sendline("quit")
        child.send("\x1b\r")  # Alt+Enter
        child.expect(pexpect.EOF, timeout=10.0)
        child.close()
        assert child.exitstatus == 0

    except pexpect.TIMEOUT:
        pytest.fail(
            "MCP E2E test timed out. Output before timeout: "
            f"{child.before}"
        )
    finally:
        if child.isalive():
            child.close(force=True)
