"""Local agent settings, including workspace and permission choices."""

from dataclasses import InitVar, dataclass, field
from pathlib import Path
import json
import os
import tempfile

from config import DEFAULT_BASE_URL, DEFAULT_MODEL, PROJECT_ROOT, Settings
from agent.permission_policy import PERMISSION_LABELS, PERMISSION_PRESETS


class AgentConfigError(RuntimeError):
    """The local agent configuration is missing, invalid, or cannot be saved."""


@dataclass(frozen=True)
class AgentConfig:
    workspace: Path
    api_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    allow_read: bool = True
    allow_write: bool = False
    allow_shell: bool = False
    confirm_write: bool = True
    confirm_shell: bool = True
    reasoning_level: str = "medium"
    key_source: str = "none"
    permission_mode: str = "custom"
    require_workspace: InitVar[bool] = True

    def __post_init__(self, require_workspace: bool) -> None:
        if not isinstance(self.workspace, Path):
            raise TypeError("workspace must be a Path.")
        if not isinstance(self.api_key, str):
            raise TypeError("api_key must be text.")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be non-empty text.")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url must be non-empty text.")
        if self.reasoning_level not in {"low", "medium", "high"}:
            raise ValueError("reasoning_level must be low, medium, or high.")
        if not isinstance(self.permission_mode, str) or self.permission_mode not in PERMISSION_LABELS:
            raise ValueError("Unknown permission mode.")
        if self.permission_mode in PERMISSION_PRESETS:
            for name, value in PERMISSION_PRESETS[self.permission_mode].items():
                object.__setattr__(self, name, value)
        for name in (
            "allow_read", "allow_write", "allow_shell", "confirm_write", "confirm_shell"
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool.")
        if require_workspace and not self.workspace.is_dir():
            raise AgentConfigError(f"工作区不存在或不是目录：{self.workspace}")

    @property
    def key_configured(self) -> bool:
        return bool(self.api_key)

    def to_settings(self) -> Settings:
        return Settings(api_key=self.api_key, model=self.model, base_url=self.base_url)

    def public(self) -> dict[str, object]:
        """Return a UI-safe view that never contains the API key."""
        return {
            "workspace": str(self.workspace),
            "model": self.model,
            "base_url": self.base_url,
            "allow_read": self.allow_read,
            "allow_write": self.allow_write,
            "allow_shell": self.allow_shell,
            "confirm_write": self.confirm_write,
            "confirm_shell": self.confirm_shell,
            "reasoning_level": self.reasoning_level,
            "permission_mode": self.permission_mode,
            "permission_label": PERMISSION_LABELS[self.permission_mode],
            "api_key_configured": self.key_configured,
            "api_key_source": self.key_source,
        }


class AgentConfigStore:
    """Persist one local agent configuration next to the application."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        settings_loader=None,
    ) -> None:
        self.path = Path(path) if path is not None else PROJECT_ROOT / "data" / "agent_config.json"
        self._settings_loader = settings_loader or Settings.from_environment

    def _environment_settings(self) -> Settings:
        try:
            return self._settings_loader()
        except (ValueError, OSError):
            return Settings(api_key="", model=DEFAULT_MODEL, base_url=DEFAULT_BASE_URL)

    def load(self, *, require_workspace: bool = True) -> AgentConfig:
        if not self.path.exists():
            settings = self._environment_settings()
            return AgentConfig(
                workspace=PROJECT_ROOT,
                api_key=settings.api_key,
                model=settings.model,
                base_url=settings.base_url,
                key_source="environment" if settings.api_key else "none",
                permission_mode="request",
            )

        try:
            document = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise AgentConfigError("Agent 配置不是 UTF-8 文本。原文件已保留。") from exc
        except json.JSONDecodeError as exc:
            raise AgentConfigError(
                f"Agent 配置不是有效 JSON（第 {exc.lineno} 行，第 {exc.colno} 列）。"
            ) from exc
        except OSError as exc:
            raise AgentConfigError("无法读取 Agent 配置。原文件已保留。") from exc

        if not isinstance(document, dict):
            raise AgentConfigError("Agent 配置顶层必须是 JSON 对象。")
        if document.get("schema_version") != 1:
            raise AgentConfigError("Agent 配置版本不受支持。")

        workspace_raw = document.get("workspace", str(PROJECT_ROOT))
        if not isinstance(workspace_raw, str) or not workspace_raw.strip():
            raise AgentConfigError("Agent 配置缺少有效的工作区路径。")
        try:
            workspace = Path(workspace_raw).expanduser().resolve()
        except OSError as exc:
            raise AgentConfigError(f"工作区路径无效：{workspace_raw}") from exc
        if require_workspace and not workspace.is_dir():
            raise AgentConfigError(f"工作区不存在或不是目录：{workspace}")

        key = document.get("api_key", "")
        if not isinstance(key, str):
            raise AgentConfigError("Agent 配置中的 API Key 类型无效。")
        settings = self._environment_settings()
        key_source = "config" if key else ("environment" if settings.api_key else "none")
        if not key:
            key = settings.api_key

        model = document.get("model", settings.model)
        base_url = document.get("base_url", settings.base_url)
        if not isinstance(model, str) or not model.strip():
            raise AgentConfigError("Agent 配置中的模型名无效。")
        if not isinstance(base_url, str) or not base_url.strip():
            raise AgentConfigError("Agent 配置中的 API 地址无效。")
        reasoning_level = document.get("reasoning_level", "medium")
        if reasoning_level not in {"low", "medium", "high"}:
            raise AgentConfigError("Agent 配置中的推理强度无效。")
        permission_mode = document.get("permission_mode", "custom")
        if not isinstance(permission_mode, str) or permission_mode not in PERMISSION_LABELS:
            raise AgentConfigError("Agent 配置中的权限模式无效。")

        def flag(name: str, default: bool) -> bool:
            value = document.get(name, default)
            if type(value) is not bool:
                raise AgentConfigError(f"Agent 配置中的 {name} 必须是布尔值。")
            return value

        return AgentConfig(
            workspace=workspace,
            api_key=key,
            model=model.strip(),
            base_url=base_url.strip(),
            allow_read=flag("allow_read", True),
            allow_write=flag("allow_write", False),
            allow_shell=flag("allow_shell", False),
            confirm_write=flag("confirm_write", True),
            confirm_shell=flag("confirm_shell", True),
            reasoning_level=reasoning_level,
            key_source=key_source,
            permission_mode=permission_mode,
            require_workspace=require_workspace,
        )

    def update(self, payload: object) -> AgentConfig:
        """Update settings from a UI payload; a blank api_key keeps the old key."""
        if not isinstance(payload, dict):
            raise AgentConfigError("配置内容必须是 JSON 对象。")
        current = self.load(require_workspace=False)

        workspace_raw = payload.get("workspace", str(current.workspace))
        if not isinstance(workspace_raw, str) or not workspace_raw.strip():
            raise AgentConfigError("工作区路径不能为空。")
        workspace = Path(workspace_raw).expanduser().resolve()
        if "workspace" in payload and not workspace.is_dir():
            raise AgentConfigError(f"工作区不存在或不是目录：{workspace}")

        if payload.get("clear_api_key") is True:
            api_key = ""
        else:
            new_key = payload.get("api_key")
            if new_key is None or new_key == "":
                api_key = current.api_key
            elif isinstance(new_key, str):
                api_key = new_key.strip()
            else:
                raise AgentConfigError("API Key 必须是文本。")

        def text_value(name: str, fallback: str) -> str:
            value = payload.get(name, fallback)
            if not isinstance(value, str) or not value.strip():
                raise AgentConfigError(f"{name} 不能为空。")
            return value.strip()

        def bool_value(name: str, fallback: bool) -> bool:
            value = payload.get(name, fallback)
            if type(value) is not bool:
                raise AgentConfigError(f"{name} 必须是布尔值。")
            return value

        reasoning_level = payload.get("reasoning_level", current.reasoning_level)
        if reasoning_level not in {"low", "medium", "high"}:
            raise AgentConfigError("reasoning_level 必须是 low、medium 或 high。")

        settings = self._environment_settings()
        flag_names = {"allow_read", "allow_write", "allow_shell", "confirm_write", "confirm_shell"}
        permission_mode = payload.get("permission_mode", "custom" if flag_names.intersection(payload) else current.permission_mode)
        if not isinstance(permission_mode, str) or permission_mode not in PERMISSION_LABELS:
            raise AgentConfigError("权限模式必须是 request、auto 或 full。")
        if "permission_mode" in payload and permission_mode != "custom" and flag_names.intersection(payload):
            raise AgentConfigError("权限模式和单项权限不能同时修改。")
        config = AgentConfig(
            workspace=workspace,
            api_key=api_key,
            model=text_value("model", current.model),
            base_url=text_value("base_url", current.base_url),
            allow_read=bool_value("allow_read", current.allow_read),
            allow_write=bool_value("allow_write", current.allow_write),
            allow_shell=bool_value("allow_shell", current.allow_shell),
            confirm_write=bool_value("confirm_write", current.confirm_write),
            confirm_shell=bool_value("confirm_shell", current.confirm_shell),
            reasoning_level=reasoning_level,
            key_source="config" if api_key else ("environment" if settings.api_key else "none"),
            permission_mode=permission_mode,
            require_workspace="workspace" in payload,
        )
        self.save(config)
        return config

    def save(self, config: AgentConfig) -> None:
        if not isinstance(config, AgentConfig):
            raise TypeError("config must be an AgentConfig value.")
        document = {
            "schema_version": 1,
            "workspace": str(config.workspace),
            "api_key": config.api_key,
            "model": config.model,
            "base_url": config.base_url,
            "allow_read": config.allow_read,
            "allow_write": config.allow_write,
            "allow_shell": config.allow_shell,
            "confirm_write": config.confirm_write,
            "confirm_shell": config.confirm_shell,
            "reasoning_level": config.reasoning_level,
            "permission_mode": config.permission_mode,
        }
        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(encoded)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise AgentConfigError("无法保存 Agent 配置。请检查 data 目录是否可写。") from exc
