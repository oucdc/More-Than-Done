import json

import pytest

from agent.agent_config import AgentConfig, AgentConfigError, AgentConfigStore
from config import PROJECT_ROOT, Settings


def no_env_settings():
    return Settings(api_key="", model="test-model", base_url="https://example.invalid")


def test_missing_config_uses_environment_defaults_and_project_workspace(tmp_path):
    store = AgentConfigStore(tmp_path / "agent.json", settings_loader=no_env_settings)

    config = store.load()

    assert config.workspace == PROJECT_ROOT
    assert config.api_key == ""
    assert config.model == "test-model"
    assert config.base_url == "https://example.invalid"
    assert config.allow_read is True
    assert config.permission_mode == "request"
    assert config.allow_write is True
    assert config.allow_shell is True
    assert config.confirm_write and config.confirm_shell
    assert config.key_source == "none"
    assert config.public()["api_key_configured"] is False


def test_environment_key_is_used_when_config_has_no_key(tmp_path):
    store = AgentConfigStore(
        tmp_path / "agent.json",
        settings_loader=lambda: Settings(api_key="env-key"),
    )

    config = store.load()

    assert config.api_key == "env-key"
    assert config.key_source == "environment"
    assert config.key_configured is True


def test_update_persists_workspace_permissions_and_key_without_public_leak(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AgentConfigStore(tmp_path / "agent.json", settings_loader=no_env_settings)

    config = store.update({
        "workspace": str(workspace),
        "api_key": "secret-key",
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "allow_read": True,
        "allow_write": True,
        "allow_shell": True,
        "confirm_write": False,
        "confirm_shell": True,
    })

    assert config.api_key == "secret-key"
    assert config.workspace == workspace.resolve()
    assert config.allow_write is True
    assert config.allow_shell is True
    assert config.confirm_write is False
    public = store.load().public()
    assert "secret-key" not in json.dumps(public, ensure_ascii=False)
    assert public["api_key_configured"] is True
    assert public["api_key_source"] == "config"
    saved = json.loads((tmp_path / "agent.json").read_text(encoding="utf-8"))
    assert saved["api_key"] == "secret-key"


def test_blank_api_key_keeps_existing_key_and_clear_removes_it(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = AgentConfigStore(tmp_path / "agent.json", settings_loader=no_env_settings)
    store.update({"workspace": str(workspace), "api_key": "secret-key"})

    kept = store.update({"workspace": str(workspace), "api_key": ""})
    assert kept.api_key == "secret-key"

    cleared = store.update({"workspace": str(workspace), "clear_api_key": True})
    assert cleared.api_key == ""
    assert cleared.key_source == "none"


def test_update_rejects_missing_workspace_and_invalid_flags(tmp_path):
    store = AgentConfigStore(tmp_path / "agent.json", settings_loader=no_env_settings)

    with pytest.raises(AgentConfigError, match="工作区"):
        store.update({"workspace": str(tmp_path / "missing"), "api_key": "x"})

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(AgentConfigError, match="allow_write"):
        store.update({
            "workspace": str(workspace),
            "allow_write": "yes",
        })


def test_corrupt_config_is_reported_and_not_overwritten(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text("{bad", encoding="utf-8")
    store = AgentConfigStore(path, settings_loader=no_env_settings)

    with pytest.raises(AgentConfigError, match="JSON"):
        store.load()

    assert path.read_text(encoding="utf-8") == "{bad"


def test_config_dataclass_rejects_missing_workspace(tmp_path):
    with pytest.raises(AgentConfigError):
        AgentConfig(workspace=tmp_path / "missing")
