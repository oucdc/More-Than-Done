"""Small OpenAI-compatible client wrapper for the DeepSeek Chat API."""

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from types import SimpleNamespace
import time
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
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
from config import Settings


class LLMClient:
    """Send chat messages to DeepSeek and return the assistant's text response."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._model = settings.model
        self._client = client if client is not None else OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=30.0,
            max_retries=0,
        )

    def complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = 128,
    ) -> str:
        """Return one non-streaming completion for the supplied chat messages."""
        return self._complete(messages, max_tokens=max_tokens, json_mode=False)

    def complete_json(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = 512,
    ) -> str:
        """Return one completion using the provider's JSON output mode."""
        return self._complete(messages, max_tokens=max_tokens, json_mode=True)

    def complete_tool_call(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]],
        max_tokens: int = 2048,
    ) -> Any:
        """Return one assistant message that may contain tool calls."""
        if not messages:
            raise ValueError("At least one chat message is required.")
        if not tools:
            raise ValueError("At least one tool definition is required.")
        if max_tokens < 1:
            raise ValueError("max_tokens must be greater than zero.")

        request: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "tools": list(tools),
            "tool_choice": "auto",
            "max_tokens": max_tokens,
            "stream": False,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        response = self._create_with_retry(request)
        try:
            choice = response.choices[0]
            message = choice.message
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError("模型回复结构异常，请重试。") from exc
        if choice.finish_reason == "length":
            raise LLMInvalidResponseError(
                "模型回复超过输出长度限制，请增加 max_tokens 后重试。"
            )
        content = getattr(message, "content", None)
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls and (not isinstance(content, str) or not content.strip()):
            raise LLMInvalidResponseError("模型返回了空回复，请重试。")
        return message

    def stream_tool_call(
        self, messages, *, tools, max_tokens=2048, on_delta: Callable[[str], None],
        cancel_event=None,
    ) -> Any:
        """Stream text immediately, then return fully assembled tool calls.

        A broken stream is never retried after text has been delivered, and its
        incomplete tool arguments must never reach the execution runtime.
        """
        if not messages or not tools or max_tokens < 1:
            raise ValueError("Messages, tools and a positive token budget are required.")
        request = {
            "model": self._model, "messages": list(messages), "tools": list(tools),
            "tool_choice": "auto", "max_tokens": max_tokens, "stream": True,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish = None
        stream = self._create_with_retry(request)
        try:
            with self._translate_sdk_errors():
                for chunk in stream:
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("已停止接收模型回复。")
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta
                    text = getattr(delta, "content", None)
                    if isinstance(text, str) and text:
                        parts.append(text)
                        on_delta(text)
                    for fragment in getattr(delta, "tool_calls", None) or []:
                        item = calls.setdefault(fragment.index, {
                            "id": "", "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                        if fragment.id:
                            item["id"] = fragment.id
                        function = fragment.function
                        if function is not None:
                            item["function"]["name"] += function.name or ""
                            item["function"]["arguments"] += function.arguments or ""
                    if choice.finish_reason is not None:
                        finish = choice.finish_reason
        finally:
            if hasattr(stream, "close"):
                stream.close()
        if finish == "length":
            raise LLMInvalidResponseError("模型回复超过输出长度限制，已保留收到的内容，请重试。")
        if finish not in {"stop", "tool_calls"}:
            raise LLMInvalidResponseError("模型流式回复未完整结束，已保留收到的内容，请重试。")
        tool_calls = [calls[index] for index in sorted(calls)]
        if any(not call["id"] or not call["function"]["name"] for call in tool_calls):
            raise LLMInvalidResponseError("模型工具调用不完整，未执行该操作。")
        content = "".join(parts)
        if not tool_calls and not content.strip():
            raise LLMInvalidResponseError("模型返回了空回复，请重试。")
        return SimpleNamespace(content=content, tool_calls=tool_calls)

    def _complete(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        if not messages:
            raise ValueError("At least one chat message is required.")
        if max_tokens < 1:
            raise ValueError("max_tokens must be greater than zero.")

        request: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "stream": False,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if json_mode:
            request["response_format"] = {"type": "json_object"}

        response = self._create_with_retry(request)

        try:
            choice = response.choices[0]
            content = choice.message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError("模型回复结构异常，请重试。") from exc

        if choice.finish_reason == "length":
            raise LLMInvalidResponseError(
                "模型回复超过输出长度限制，请增加 max_tokens 后重试。"
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMInvalidResponseError("模型返回了空文本，请重试。")
        return content.strip()

    def _create_with_retry(self, request: dict[str, Any]) -> Any:
        for attempt in range(2):
            try:
                return self._create_completion(request)
            except (LLMRateLimitError, LLMTimeoutError, LLMConnectionError, LLMProviderError) as exc:
                if attempt == 0 and self._is_retryable(exc):
                    time.sleep(1.0)
                    continue
                raise
        raise LLMProviderError("DeepSeek API 调用失败，请稍后再试。")

    def _create_completion(self, request: dict[str, Any]) -> Any:
        """Make one provider request and translate SDK errors to safe app errors."""
        with self._translate_sdk_errors():
            return self._client.chat.completions.create(**request)

    @contextmanager
    def _translate_sdk_errors(self):
        try:
            yield
        except AuthenticationError as exc:
            raise LLMAuthenticationError(
                "DeepSeek API 密钥无效或未授权，请检查本地 API Key 配置。"
            ) from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(
                "DeepSeek API 请求过于频繁，请稍后再试。"
            ) from exc
        except APITimeoutError as exc:
            raise LLMTimeoutError(
                "DeepSeek API 请求超时，请检查网络后再试。"
            ) from exc
        except APIConnectionError as exc:
            raise LLMConnectionError(
                "无法连接 DeepSeek API，请检查网络和服务地址。"
            ) from exc
        except APIStatusError as exc:
            if exc.status_code == 402:
                raise LLMInsufficientBalanceError(
                    "DeepSeek API 账户余额不足，请检查账户状态。"
                ) from exc
            if exc.status_code in (400, 422):
                raise LLMRequestError(exc.status_code) from exc
            raise LLMProviderError(
                f"DeepSeek API 暂时不可用（HTTP {exc.status_code}），请稍后再试。",
                status_code=exc.status_code,
            ) from exc
        except APIError as exc:
            raise LLMProviderError("DeepSeek API 调用失败，请稍后再试。") from exc

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        if isinstance(error, (LLMRateLimitError, LLMTimeoutError, LLMConnectionError)):
            return True
        return isinstance(error, LLMProviderError) and (
            error.status_code is None or 500 <= error.status_code < 600
        )
