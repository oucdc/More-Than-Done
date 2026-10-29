"""Safe, user-facing errors raised by the model integration layer."""


class LLMClientError(RuntimeError):
    """Base class for failures that the CLI can explain without SDK internals."""


class LLMAuthenticationError(LLMClientError):
    """The provider rejected the configured API key."""


class LLMInsufficientBalanceError(LLMClientError):
    """The provider account cannot make requests with its current balance."""


class LLMRateLimitError(LLMClientError):
    """The provider rejected a request because of a rate or concurrency limit."""


class LLMConnectionError(LLMClientError):
    """The client could not connect to the provider."""


class LLMTimeoutError(LLMConnectionError):
    """The provider did not respond before the request timeout."""


class LLMRequestError(LLMClientError):
    """The provider rejected request parameters or request formatting."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(
            f"DeepSeek API 拒绝了请求（HTTP {status_code}），请检查请求参数。"
        )


class LLMProviderError(LLMClientError):
    """The provider returned an unexpected error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class LLMInvalidResponseError(LLMClientError):
    """The provider response did not contain usable text."""
