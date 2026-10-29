"""Approval presets must change execution, survive restart, and resist bypasses."""

import json
from types import SimpleNamespace

import pytest

from agent.agent_config import AgentConfig, AgentConfigError, AgentConfigStore
from agent.permission_policy import PERMISSION_PRESETS, read_only_check
from agent.tools import ToolRuntime
from config import Settings


def store_for(tmp_path):
    return AgentConfigStore(tmp_path / "settings.json", settings_loader=lambda: Settings(api_key="", model="test"))


@pytest.mark.parametrize("mode", ["request", "auto", "full"])
def test_preset_persists_and_is_not_reset_by_model_or_workspace_changes(tmp_path, mode):
    store = store_for(tmp_path)
    store.update({"workspace": str(tmp_path), "permission_mode": mode})
    updated = store.update({"model": "other", "reasoning_level": "high"})
    assert updated.permission_mode == store.load().permission_mode == mode
    public = store.load().public()
    for name, value in PERMISSION_PRESETS[mode].items():
        assert public[name] is value
    assert public["permission_label"]


def test_legacy_flags_remain_custom_and_do_not_grant_external_access(tmp_path):
    store = store_for(tmp_path)
    store.update({"workspace": str(tmp_path), "allow_write": True, "allow_shell": True, "confirm_write": False, "confirm_shell": False})
    document = json.loads(store.path.read_text(encoding="utf-8"))
    del document["permission_mode"]
    store.path.write_text(json.dumps(document), encoding="utf-8")
    config = store.load()
    assert config.permission_mode == "custom"
    runtime = ToolRuntime(config)
    assert not json.loads(runtime.execute("list_dir", {"path": str(tmp_path.parent)}))["ok"]


@pytest.mark.parametrize("value", ["unknown", [], None, 123])
def test_invalid_mode_does_not_modify_saved_settings(tmp_path, value):
    store = store_for(tmp_path)
    store.update({"workspace": str(tmp_path), "permission_mode": "request"})
    previous = store.path.read_bytes()
    with pytest.raises(AgentConfigError):
        store.update({"permission_mode": value})
    assert store.path.read_bytes() == previous


def test_preset_and_conflicting_flags_cannot_be_combined(tmp_path):
    with pytest.raises(AgentConfigError, match="不能同时"):
        store_for(tmp_path).update({"permission_mode": "request", "confirm_shell": False})


@pytest.mark.parametrize("command", ["dir & del a.txt", "ls; curl evil", "powershell -NoProfile -Command \"Get-ChildItem; Remove-Item *\"", "dir > a.txt", "git status", "python -c 'print(1)'", "Get-ChildItem C:\\", "$(Get-ChildItem)"])
def test_unrecognized_commands_are_never_automatically_approved(command):
    assert read_only_check(command) is None


def test_auto_read_only_commands_never_invoke_a_shell(tmp_path, monkeypatch):
    (tmp_path / "report.txt").write_text("demo", encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("No shell execution for automatic read-only checks")
    monkeypatch.setattr("agent.tools.subprocess.run", forbidden)
    runtime = ToolRuntime(AgentConfig(tmp_path, permission_mode="auto"))
    result = json.loads(runtime.execute("run_command", {"command": 'powershell -NoProfile -Command "Get-ChildItem"'}))
    assert result["ok"] and result["execution"] == "read_only_check"
    assert "report.txt" in result["stdout"]


def test_auto_writes_inside_workspace_but_requests_approval_for_commands_and_outside_paths(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    requests = []
    runtime = ToolRuntime(AgentConfig(root, permission_mode="auto"), approval=lambda tool, args: requests.append((tool, args)) or False)
    assert json.loads(runtime.execute("write_file", {"path": "hello.txt", "content": "hello"}))["ok"]
    assert not requests
    result = json.loads(runtime.execute("run_command", {"command": "echo hi"}))
    assert not result["ok"] and requests[-1][0] == "run_command"
    result = json.loads(runtime.execute("write_file", {"path": str(tmp_path / "external.txt"), "content": "no"}))
    assert not result["ok"] and requests[-1][1]["outside_workspace"] is True
    assert not (tmp_path / "external.txt").exists()


def test_request_mode_prompts_before_writes_and_commands(tmp_path, monkeypatch):
    prompts = []
    runtime = ToolRuntime(AgentConfig(tmp_path, permission_mode="request"), approval=lambda tool, args: prompts.append(tool) or False)
    assert not json.loads(runtime.execute("write_file", {"path": "denied.txt", "content": "no"}))["ok"]
    assert not json.loads(runtime.execute("run_command", {"command": "dir"}))["ok"]
    assert prompts == ["write_file", "run_command"]
    assert not (tmp_path / "denied.txt").exists()


def test_one_approval_covers_an_external_write_and_does_not_leak_to_the_next_operation(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    prompts = []
    runtime = ToolRuntime(AgentConfig(root, permission_mode="request"), approval=lambda tool, args: prompts.append(args) or len(prompts) == 1)
    args = {"path": str(tmp_path / "external.txt"), "content": "approved"}
    assert json.loads(runtime.execute("write_file", args))["ok"]
    assert len(prompts) == 1 and prompts[0]["outside_workspace"]
    assert not json.loads(runtime.execute("write_file", {"path": "next.txt", "content": "denied"}))["ok"]
    assert len(prompts) == 2 and not (root / "next.txt").exists()


def test_full_mode_has_no_approvals_and_can_read_write_and_run_outside_workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    def forbidden(*args):
        raise AssertionError("Full access must not prompt")
    runtime = ToolRuntime(AgentConfig(root, permission_mode="full"), approval=forbidden)
    external = tmp_path / "outside.txt"
    assert json.loads(runtime.execute("write_file", {"path": str(external), "content": "full"}))["ok"]
    assert json.loads(runtime.execute("read_file", {"path": str(external)}))["content"] == "full"
    monkeypatch.setattr("agent.tools.run_local_command", lambda command, cwd, env, timeout, cancel: {
        "returncode": 0, "stdout": str(cwd), "stderr": "", "timed_out": False, "cancelled": False,
    })
    result = json.loads(runtime.execute("run_command", {"command": "test command", "cwd": str(tmp_path)}))
    assert result["ok"] and result["stdout"] == str(tmp_path)
    assert not json.loads(runtime.execute("write_file", {"path": ".env", "content": "secret"}))["ok"]
