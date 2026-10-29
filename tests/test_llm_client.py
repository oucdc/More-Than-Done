from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIError,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from agent.errors import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMInsufficientBalanceError,
    LLMInvalidResponseError,
    LLMProviderError,
    LLMRateLimitError,
    LLMRequestError,
    LLMTimeoutError,
)
from agent.llm_client import LLMClient
from config import Settings


class FakeCompletions:
    def __init__(self, content="  Test response  ", *, error=None, errors=None, finish_reason="stop"):
        self.content = content
        self.error = error
        self.errors = list(errors or [])
        self.finish_reason = finish_reason
        self.request = None
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        self.request = kwargs
        if self.errors:
            raise self.errors.pop(0)
        if self.error is not None:
            raise self.error
        message = SimpleNamespace(content=self.content)
        choice = SimpleNamespace(message=message, finish_reason=self.finish_reason)
        return SimpleNamespace(choices=[choice])


def make_client(content="  Test response  ", *, error=None, errors=None, finish_reason="stop"):
    completions = FakeCompletions(
        content,
        error=error,
        errors=errors,
        finish_reason=finish_reason,
    )
    fake_sdk_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    settings = Settings(api_key="test-key", model="test-model")
    return LLMClient(settings, client=fake_sdk_client), completions


def _status_error(error_type, status_code):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status_code, request=request)
    return error_type(
        "private error detail",
        response=response,
        body={"detail": "private error detail"},
    )


def test_complete_returns_trimmed_text_and_uses_configured_model():
    llm, completions = make_client()

    result = llm.complete([{"role": "user", "content": "Hello"}])

    assert result == "Test response"
    assert completions.request["model"] == "test-model"
    assert completions.request["stream"] is False
    assert completions.request["extra_body"] == {"thinking": {"type": "disabled"}}


def test_complete_json_requests_provider_json_mode():
    llm, completions = make_client('{"message":"Hello","follow_up_question":null}')

    result = llm.complete_json([{"role": "user", "content": "Return JSON."}])

    assert result.startswith("{")
    assert completions.request["response_format"] == {"type": "json_object"}
    assert completions.request["max_tokens"] == 512


def test_complete_rejects_empty_messages():
    llm, _ = make_client()

    with pytest.raises(ValueError, match="At least one"):
        llm.complete([])


def test_complete_rejects_empty_model_response():
    llm, completions = make_client("  ")

    with pytest.raises(LLMInvalidResponseError, match="空文本"):
        llm.complete([{"role": "user", "content": "Hello"}])
    assert completions.calls == 1


def test_complete_rejects_truncated_model_response():
    llm, completions = make_client('{"message":', finish_reason="length")

    with pytest.raises(LLMInvalidResponseError, match="输出长度限制"):
        llm.complete_json([{"role": "user", "content": "Return JSON."}])
    assert completions.calls == 1


@pytest.mark.parametrize(
    ("make_error", "expected_type", "expected_text", "expected_calls"),
    [
        (
            lambda: _status_error(AuthenticationError, 401),
            LLMAuthenticationError,
            "密钥无效",
            1,
        ),
        (
            lambda: _status_error(RateLimitError, 429),
            LLMRateLimitError,
            "过于频繁",
            2,
        ),
        (
            lambda: _status_error(APIStatusError, 402),
            LLMInsufficientBalanceError,
            "余额不足",
            1,
        ),
        (
            lambda: _status_error(APIStatusError, 422),
            LLMRequestError,
            "HTTP 422",
            1,
        ),
        (
            lambda: _status_error(APIStatusError, 400),
            LLMRequestError,
            "HTTP 400",
            1,
        ),
        (
            lambda: _status_error(APIStatusError, 503),
            LLMProviderError,
            "HTTP 503",
            2,
        ),
    ],
)
def test_complete_maps_provider_status_errors_safely(
    make_error,
    expected_type,
    expected_text,
    expected_calls,
    monkeypatch,
):
    monkeypatch.setattr("agent.llm_client.time.sleep", lambda _seconds: None)
    llm, completions = make_client(error=make_error())

    with pytest.raises(expected_type) as error:
        llm.complete([{"role": "user", "content": "Hello"}])

    assert expected_text in str(error.value)
    assert "private error detail" not in str(error.value)
    assert completions.calls == expected_calls


def test_rate_limit_retries_once_then_returns_success(monkeypatch):
    sleeps = []
    monkeypatch.setattr("agent.llm_client.time.sleep", sleeps.append)
    llm, completions = make_client(errors=[_status_error(RateLimitError, 429)])

    result = llm.complete([{"role": "user", "content": "Hello"}])

    assert result == "Test response"
    assert completions.calls == 2
    assert sleeps == [1.0]


def test_server_error_retries_once_then_returns_success(monkeypatch):
    monkeypatch.setattr("agent.llm_client.time.sleep", lambda _seconds: None)
    llm, completions = make_client(errors=[_status_error(APIStatusError, 503)])

    result = llm.complete([{"role": "user", "content": "Hello"}])

    assert result == "Test response"
    assert completions.calls == 2


def test_timeout_retries_once_then_returns_success(monkeypatch):
    sleeps = []
    monkeypatch.setattr("agent.llm_client.time.sleep", sleeps.append)
    timeout = APITimeoutError(
        httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    )
    llm, completions = make_client(errors=[timeout])

    result = llm.complete([{"role": "user", "content": "Hello"}])

    assert result == "Test response"
    assert completions.calls == 2
    assert sleeps == [1.0]


def test_provider_error_without_status_retries_once_then_returns_success(monkeypatch):
    monkeypatch.setattr("agent.llm_client.time.sleep", lambda _seconds: None)
    provider_error = APIError(
        "private provider detail",
        request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
        body=None,
    )
    llm, completions = make_client(errors=[provider_error])

    result = llm.complete([{"role": "user", "content": "Hello"}])

    assert result == "Test response"
    assert completions.calls == 2


@pytest.mark.parametrize(
    ("make_error", "expected_type"),
    [
        (
            lambda: APITimeoutError(
                httpx.Request("POST", "https://api.deepseek.com/chat/completions")
            ),
            LLMTimeoutError,
        ),
        (
            lambda: APIConnectionError(
                request=httpx.Request("POST", "https://api.deepseek.com/chat/completions")
            ),
            LLMConnectionError,
        ),
    ],
)
def test_complete_maps_network_errors(make_error, expected_type, monkeypatch):
    monkeypatch.setattr("agent.llm_client.time.sleep", lambda _seconds: None)
    llm, completions = make_client(error=make_error())

    with pytest.raises(expected_type):
        llm.complete([{"role": "user", "content": "Hello"}])
    assert completions.calls == 2


def test_complete_tool_call_sends_tools_and_returns_message():
    tool_calls = [SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="read_file", arguments='{"path":"a.txt"}'),
    )]
    message = SimpleNamespace(content=None, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    response = SimpleNamespace(choices=[choice])

    class ToolCompletions:
        def __init__(self):
            self.request = None

        def create(self, **kwargs):
            self.request = kwargs
            return response

    completions = ToolCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    llm = LLMClient(Settings(api_key="test-key", model="test-model"), client=client)
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]

    result = llm.complete_tool_call(
        [{"role": "user", "content": "read a.txt"}],
        tools=tools,
    )

    assert result is message
    assert completions.request["tool_choice"] == "auto"
    assert completions.request["tools"] == tools
    assert completions.request["max_tokens"] == 2048
    assert "response_format" not in completions.request


def test_complete_tool_call_rejects_empty_tools():
    llm = LLMClient(Settings(api_key="test-key"), client=SimpleNamespace())

    with pytest.raises(ValueError, match="tool definition"):
        llm.complete_tool_call([{"role": "user", "content": "x"}], tools=[])

