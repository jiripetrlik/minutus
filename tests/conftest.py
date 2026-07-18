import pytest
import subprocess
import os
import sys
from pathlib import Path
from typing import List

# Path to the project root (parent of tests/ directory)
PROJECT_ROOT = Path(__file__).parent.parent

# Path to the minutus module
MINUTUS_MODULE = str(PROJECT_ROOT / "src" / "minutus" / "minutus.py")

# Ensure src/ is on sys.path so unit tests can import the minutus package
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import minutus.minutus as minutus_mod


@pytest.fixture
def trusted_root(tmp_path, monkeypatch):
    """
    Patch TRUSTED_ROOT to point at tmp_path AND change the working directory
    to tmp_path, so that relative paths passed to get_safe_path() resolve
    against the trusted root (just like in production where TRUSTED_ROOT
    is set to Path.cwd() at startup).
    Returns the tmp_path Path for convenience.
    """
    root = tmp_path.resolve()
    monkeypatch.setattr(minutus_mod, "TRUSTED_ROOT", root)
    monkeypatch.chdir(root)
    return root


@pytest.fixture(scope="session")
def openai_api_key():
    """Ensure API key is set."""
    key = os.getenv("MINUTUS_OPENAI_API_KEY")
    if not key:
        raise AssertionError("MINUTUS_OPENAI_API_KEY environment variable not set.")
    return key


@pytest.fixture(scope="session")
def openai_base_url():
    """Return Base URL from env var, or None to use the standard OpenAI endpoint."""
    return os.getenv("MINUTUS_OPENAI_BASE_URL")


@pytest.fixture(scope="session")
def model_name():
    """
    Provide the model name for testing.
    Falls back to 'gpt-5.6-luna' if MINUTUS_TESTS_MODEL_NAME is not set.
    """
    return os.getenv("MINUTUS_TESTS_MODEL_NAME", "gpt-5.6-luna")


@pytest.fixture
def run_minutus(openai_api_key, openai_base_url, model_name):
    """
    A fixture that provides a function to run the minutus CLI as a subprocess.
    """
    def _run(args: List[str]):
        cmd = ["python3", MINUTUS_MODULE, "--model-name", model_name] + args

        cmd.extend(["--api-key", openai_api_key])
        if openai_base_url:
            cmd.extend(["--base-url", openai_base_url])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        return result
    return _run


@pytest.fixture
def temp_file_factory(tmp_path):
    """Factory to create temporary files for testing context."""
    def _create(content: str, suffix=".txt"):
        f = tmp_path / f"test_context{suffix}"
        f.write_text(content)
        return str(f)
    return _create
