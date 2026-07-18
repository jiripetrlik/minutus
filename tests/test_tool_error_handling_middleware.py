"""Unit tests for invalid tool-call error handling middleware."""

import pytest
from langchain.messages import AIMessage, ToolMessage

from minutus.minutus import ToolErrorHandlingMiddleware


pytestmark = pytest.mark.unit


def test_after_model_uses_each_invalid_call_parsing_error():
    message = AIMessage(
        content="",
        invalid_tool_calls=[
            {
                "name": "first_tool",
                "args": "{invalid",
                "id": "call-1",
                "error": "Missing closing brace",
                "type": "invalid_tool_call",
            },
            {
                "name": "second_tool",
                "args": "not-json",
                "id": "call-2",
                "error": "Expected a JSON object",
                "type": "invalid_tool_call",
            },
        ],
    )

    result = ToolErrorHandlingMiddleware().after_model(
        {"messages": [message]}, runtime=None
    )

    assert result is not None
    error_messages = result["messages"]
    assert len(error_messages) == 2
    assert all(isinstance(item, ToolMessage) for item in error_messages)
    assert [item.tool_call_id for item in error_messages] == ["call-1", "call-2"]
    assert [item.status for item in error_messages] == ["error", "error"]
    assert [item.content for item in error_messages] == [
        "Tool error: Please check your input and try again. (Missing closing brace)",
        "Tool error: Please check your input and try again. (Expected a JSON object)",
    ]


def test_after_model_uses_fallback_when_parsing_error_is_missing():
    message = AIMessage(
        content="",
        invalid_tool_calls=[
            {
                "name": "broken_tool",
                "args": "{invalid",
                "id": "call-1",
                "type": "invalid_tool_call",
            }
        ],
    )

    result = ToolErrorHandlingMiddleware().after_model(
        {"messages": [message]}, runtime=None
    )

    assert result is not None
    assert result["messages"][0].content == (
        "Tool error: Please check your input and try again. (Unknown parsing error)"
    )
