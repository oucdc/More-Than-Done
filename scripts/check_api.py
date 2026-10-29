"""Make a small real DeepSeek API request to verify local configuration."""

import sys

from agent.errors import LLMClientError
from agent.llm_client import LLMClient
from config import Settings


def main() -> int:
    try:
        settings = Settings.from_environment()
    except ValueError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    try:
        answer = LLMClient(settings).complete(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=32,
        )
    except LLMClientError as exc:
        print(
            f"DeepSeek API 调用失败：{exc}",
            file=sys.stderr,
        )
        return 1

    print(f"DeepSeek API 调用成功：{answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
