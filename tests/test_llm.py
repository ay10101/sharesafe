"""
Unit tests for app.ai.llm.
Covers all required resilient fallback, caching, timeout, and schema validation behaviors.
All network interactions are strictly mocked; no external credentials or network used.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from app.ai.llm import LLMResult, clear_cache, complete


# ---------------------------------------------------------------------------
# Test Fixtures & Models
# ---------------------------------------------------------------------------
class MockSchema(BaseModel):
    summary: str = Field(description="Summary of findings")
    severity: str = Field(description="Severity rating")


@pytest.fixture(autouse=True)
def reset_state():
    """Ensure in-memory cache is cleared and environment is reset between tests."""
    clear_cache()
    yield
    clear_cache()


def _make_mock_response(content: str, prompt_tokens: int = 15, completion_tokens: int = 30) -> Dict[str, Any]:
    """Helper to generate a mock OpenAI-compatible response dictionary."""
    return {
        "id": "chatcmpl-mock123",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# 1. Missing API Key Returns None
# ---------------------------------------------------------------------------
def test_missing_api_key_returns_none(monkeypatch):
    """When neither OPENAI_API_KEY nor LLM_API_KEY is set, complete() returns None immediately."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    result = complete("Summarize the lab incident.")
    assert result is None


# ---------------------------------------------------------------------------
# 2. Successful Plain-Text Completion Returns LLMResult
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_successful_plain_text_completion(mock_post, monkeypatch):
    """A valid plain-text completion produces an LLMResult with matching text content."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    mock_post.return_value = _make_mock_response("The microscope was returned with a cracked stage.")

    result = complete("What is the status?", system="You are an equipment auditor.")
    assert result is not None
    assert isinstance(result, LLMResult)
    assert result.content == "The microscope was returned with a cracked stage."
    assert result.structured_data is None
    assert result.cached is False
    assert result.prompt_tokens == 15
    assert result.completion_tokens == 30
    assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# 3. Provider/Network Error Returns None
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_provider_network_error_returns_none(mock_post, monkeypatch):
    """When the provider network call fails (e.g. 500 error or connection failure), return None."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    mock_post.return_value = None  # Simulates network failure / 500 status caught in _post_chat_completion

    result = complete("Draft incident summary")
    assert result is None


# ---------------------------------------------------------------------------
# 4. Timeout Returns None
# ---------------------------------------------------------------------------
@patch("app.ai.llm.urllib.request.urlopen")
def test_timeout_returns_none(mock_urlopen, monkeypatch):
    """When the network socket times out, complete() catches the exception and returns None."""
    import socket
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    mock_urlopen.side_effect = socket.timeout("timed out")

    result = complete("Fast query", timeout=2.0)
    assert result is None


# ---------------------------------------------------------------------------
# 5. Rate-Limit Failure Returns None
# ---------------------------------------------------------------------------
@patch("app.ai.llm.urllib.request.urlopen")
def test_rate_limit_failure_returns_none(mock_urlopen, monkeypatch):
    """When the provider returns HTTP 429 Too Many Requests, return None without raising."""
    import urllib.error
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://api.openai.com/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=None,
    )

    result = complete("Analyze logs")
    assert result is None


# ---------------------------------------------------------------------------
# 6. Identical Successful Requests Use Cache
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_identical_requests_use_cache(mock_post, monkeypatch):
    """Subsequent identical requests are served from the SHA-256 in-memory cache without extra network calls."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    mock_post.return_value = _make_mock_response("Cached answer.")

    # Call 1: Populates cache
    res1 = complete("Same prompt", system="System instructions")
    assert res1 is not None
    assert res1.cached is False
    assert mock_post.call_count == 1

    # Call 2: Cache hit
    res2 = complete("Same prompt", system="System instructions")
    assert res2 is not None
    assert res2.cached is True
    assert res2.content == "Cached answer."
    assert mock_post.call_count == 1  # No second network call made


# ---------------------------------------------------------------------------
# 7. Different Effective Requests Do Not Collide in Cache
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_different_effective_requests_no_cache_collision(mock_post, monkeypatch):
    """Requests differing in prompt, system prompt, or schema generate unique cache keys."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    mock_post.side_effect = [
        _make_mock_response("Response A"),
        _make_mock_response("Response B"),
        _make_mock_response('{"summary": "S", "severity": "low"}'),
    ]

    res1 = complete("Prompt A", system="System 1")
    res2 = complete("Prompt B", system="System 1")
    res3 = complete("Prompt A", system="System 1", json_schema=MockSchema)

    assert res1.content == "Response A"
    assert res2.content == "Response B"
    assert res3.content == '{"summary": "S", "severity": "low"}'
    assert mock_post.call_count == 3


# ---------------------------------------------------------------------------
# 8. Invalid Structured Output Triggers At Most One Retry and Returns None
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_invalid_structured_output_retries_once_and_returns_none(mock_post, monkeypatch):
    """When output fails schema validation twice, retry once then safely return None."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    mock_post.side_effect = [
        _make_mock_response("This is not JSON at all."),           # Attempt 1 fails
        _make_mock_response('{"wrong_key": "still invalid"}'),      # Attempt 2 (retry) fails schema
    ]

    result = complete("Generate report", json_schema=MockSchema)
    assert result is None
    assert mock_post.call_count == 2  # Exactly 1 initial + 1 retry


# ---------------------------------------------------------------------------
# 9. Valid Structured Output Is Validated and Returned Correctly
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_valid_structured_output_validated_and_returned(mock_post, monkeypatch):
    """A valid JSON response matching the schema validates into structured_data."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    valid_json = json.dumps({"summary": "Optical sensor misaligned", "severity": "medium"})
    mock_post.return_value = _make_mock_response(f"```json\n{valid_json}\n```")

    result = complete("Inspect sensor", json_schema=MockSchema)
    assert result is not None
    assert isinstance(result.structured_data, MockSchema)
    assert result.structured_data.summary == "Optical sensor misaligned"
    assert result.structured_data.severity == "medium"
    assert mock_post.call_count == 1


@patch("app.ai.llm._post_chat_completion")
def test_structured_output_succeeds_on_retry(mock_post, monkeypatch):
    """If initial attempt has invalid JSON but retry returns valid JSON, completion succeeds."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    valid_json = json.dumps({"summary": "Recovered on retry", "severity": "low"})
    mock_post.side_effect = [
        _make_mock_response("Invalid initial string"),
        _make_mock_response(valid_json),
    ]

    result = complete("Inspect gear", json_schema=MockSchema)
    assert result is not None
    assert isinstance(result.structured_data, MockSchema)
    assert result.structured_data.summary == "Recovered on retry"
    assert mock_post.call_count == 2


@patch("app.ai.llm._post_chat_completion")
def test_dict_schema_validation(mock_post, monkeypatch):
    """Raw JSON schema dictionaries are validated against the returned JSON object."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    dict_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["status", "count"],
    }
    valid_payload = {"status": "ok", "count": 42}
    mock_post.return_value = _make_mock_response(json.dumps(valid_payload))

    result = complete("Check count", json_schema=dict_schema)
    assert result is not None
    assert result.structured_data == valid_payload
    assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# 10. Logs Do Not Expose API Key or Authorization Header
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_logs_do_not_expose_secrets(mock_post, monkeypatch, caplog):
    """Ensure secret keys and authorization headers are never emitted to logs."""
    secret_key = "sk-super-secret-production-token-998877"
    monkeypatch.setenv("OPENAI_API_KEY", secret_key)
    mock_post.return_value = _make_mock_response("Safe response.")

    with caplog.at_level(logging.DEBUG):
        res = complete("Hello auditor")
        assert res is not None

    log_output = caplog.text
    assert secret_key not in log_output
    assert "Authorization" not in log_output
    assert "Bearer" not in log_output


# ---------------------------------------------------------------------------
# 11. Malformed Provider Response Returns None
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_malformed_provider_response_returns_none(mock_post, monkeypatch):
    """When the provider returns a JSON dict missing choices or message keys, return None."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    # Response missing 'choices' list
    mock_post.return_value = {"error": {"message": "Internal engine fault"}}

    result = complete("Process telemetry")
    assert result is None


# ---------------------------------------------------------------------------
# 12. Module Can Be Imported When Credentials Are Absent
# ---------------------------------------------------------------------------
def test_module_imports_without_credentials(monkeypatch):
    """The module can be imported and utilized in non-LLM paths without setting any env vars."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    import app.ai.llm as llm_module
    assert hasattr(llm_module, "complete")
    assert hasattr(llm_module, "LLMResult")
    assert hasattr(llm_module, "clear_cache")

    # Calling with empty prompt or without key gracefully returns None
    assert llm_module.complete("") is None
    assert llm_module.complete("Test") is None


# ---------------------------------------------------------------------------
# Task 1C Regression: Fail-Closed When JSON Schema Validator Unavailable
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_jsonschema_unavailable_fails_safely(mock_post, monkeypatch):
    """When jsonschema is not installed, dict schema validation fails closed and returns None."""
    import sys
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    # Simulate jsonschema not being installed in the environment
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    dict_schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
    }
    # Provider returns arbitrary payload that would otherwise pass without validation
    mock_post.return_value = _make_mock_response(json.dumps({"status": "unverified", "extra": 123}))

    result = complete("Validate status", json_schema=dict_schema)
    assert result is None, "Must fail closed and return None when validator is unavailable"
    # Verify no second retry network call was made since validator is missing
    assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# Task 1C Regression: Monotonic Timeout Budgeting Across Attempts
# ---------------------------------------------------------------------------
@patch("app.ai.llm._post_chat_completion")
def test_timeout_initial_attempt_receives_configured_budget(mock_post, monkeypatch):
    """Initial network attempt receives the exact configured timeout budget."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    mock_post.return_value = _make_mock_response("Initial response")

    complete("Hello", timeout=6.5)
    assert mock_post.call_count == 1
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["timeout"] == pytest.approx(6.5, abs=0.01)


@patch("app.ai.llm.time.monotonic")
@patch("app.ai.llm._post_chat_completion")
def test_timeout_retry_receives_remaining_budget(mock_post, mock_time, monkeypatch):
    """Structured validation retry receives only the remaining monotonic timeout budget."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    # Time progression: start=100.0s, elapsed check before retry=103.5s (3.5s elapsed)
    mock_time.side_effect = [100.0, 103.5, 103.5]
    mock_post.side_effect = [
        _make_mock_response("Invalid initial string"),
        _make_mock_response(json.dumps({"summary": "Recovered", "severity": "low"})),
    ]

    result = complete("Generate report", json_schema=MockSchema, timeout=8.0)
    assert result is not None
    assert mock_post.call_count == 2
    # First call had full budget 8.0s
    assert mock_post.call_args_list[0].kwargs["timeout"] == pytest.approx(8.0, abs=0.01)
    # Second call had remaining budget: 8.0 - 3.5 = 4.5s
    assert mock_post.call_args_list[1].kwargs["timeout"] == pytest.approx(4.5, abs=0.01)


@patch("app.ai.llm.time.monotonic")
@patch("app.ai.llm._post_chat_completion")
def test_timeout_exhausted_budget_skips_retry(mock_post, mock_time, monkeypatch):
    """When the elapsed time exhausts the timeout budget, the retry network call is skipped."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")
    # Time progression: start=100.0s, after attempt 1=108.5s (elapsed 8.5s with timeout=8.0s)
    mock_time.side_effect = [100.0, 108.5, 108.5]
    mock_post.return_value = _make_mock_response("Invalid initial string")

    result = complete("Generate report", json_schema=MockSchema, timeout=8.0)
    assert result is None
    # Retry was skipped because remaining budget was <= 0
    assert mock_post.call_count == 1


@patch("app.ai.llm._post_chat_completion")
def test_invalid_timeout_values_fail_safely(mock_post, monkeypatch):
    """Zero, negative, or non-numeric timeout parameters fail safely without network calls."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-valid")

    for invalid_val in [0, -1, -0.5, "not-a-number", None]:
        res = complete("Hello", timeout=invalid_val)
        assert res is None, f"Expected None for timeout={invalid_val!r}"

    assert mock_post.call_count == 0, "No network calls should be made for invalid timeout values"

