"""Environment-backed application configuration."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import sys

from dotenv import load_dotenv


PROJECT_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
APP_VERSION = "0.1.5"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


@dataclass(frozen=True)
class Settings:
    """Runtime settings required to connect to DeepSeek."""

    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_environment(cls, *, dotenv_path: Path | None = None) -> "Settings":
        """Load settings from process environment variables and an optional .env file."""
        load_dotenv(dotenv_path=dotenv_path or PROJECT_ROOT / ".env", override=False)

        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "DEEPSEEK_API_KEY is not set. Add it to the environment or project .env file."
            )

        model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        base_url = os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
        return cls(api_key=api_key, model=model, base_url=base_url)
