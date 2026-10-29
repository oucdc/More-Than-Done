import json
from pathlib import Path

import pytest

from agent.agent_config import AgentConfig
from agent.tools import ToolRuntime


def make_config(tmp_path, **overrides):
    values = {
        "workspace": tmp_path,
        "api_key": "",
        "allow_read": True,
        "allow_write": False,
        "allow_shell": False,
        "confirm_write": True,
        "confirm_shell": True,
    }
    values.update(overrides)
    return AgentConfig(**values)


def result(runtime, name, arguments):
    return json.loads(runtime.execute(name, arguments))


def test_list_dir_and_read_file_are_workspace_scoped(tmp_path):
    (tmp_path / "notes.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    runtime = ToolRuntime(make_config(tmp_path))

    listed = result(runtime, "list_dir", {})
    assert listed["ok"] is True
    names = {entry["name"] for entry in listed["entries"]}
    assert names == {"notes.txt", "sub"}

    read = result(runtime, "read_file", {"path": "notes.txt"})
    assert read["ok"] is True
    assert read["content"].replace("\r\n", "\n") == "hello\nworld\n"
    assert read["truncated"] is False


def test_read_file_blocks_paths_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    runtime = ToolRuntime(make_config(tmp_path))

    for path in ("../outside.txt", str(outside)):
        response = result(runtime, "read_file", {"path": path})
        assert response["ok"] is False
        assert "工作区" in response["error"]


def test_local_credentials_are_not_exposed_to_tools(tmp_path):
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=secret", encoding="utf-8")
    (tmp_path / "agent_config.json").write_text('{"api_key":"secret"}', encoding="utf-8")
    runtime = ToolRuntime(
        make_config(tmp_path, allow_write=True, confirm_write=False),
        approval=lambda tool, args: True,
    )

    for name in (".env", "agent_config.json"):
        read = result(runtime, "read_file", {"path": name})
        assert read["ok"] is False
        assert "凭据" in read["error"]
        write = result(runtime, "write_file", {"path": name, "content": "overwrite"})
        assert write["ok"] is False
        assert "凭据" in write["error"]

    grep = result(runtime, "grep", {"pattern": "secret"})
    assert grep["ok"] is True
    assert all(match["path"] not in {".env", "agent_config.json"} for match in grep["matches"])


def test_write_requires_permission_and_approval(tmp_path):
    approvals = []
    runtime = ToolRuntime(make_config(tmp_path), approval=lambda tool, args: approvals.append(tool) or True)

    denied = result(runtime, "write_file", {"path": "new.txt", "content": "x"})
    assert denied["ok"] is False
    assert "写入权限" in denied["error"]
    assert not (tmp_path / "new.txt").exists()

    allowed_runtime = ToolRuntime(
        make_config(tmp_path, allow_write=True),
        approval=lambda tool, args: approvals.append(tool) or True,
    )
    written = result(allowed_runtime, "write_file", {"path": "new.txt", "content": "hello"})
    assert written["ok"] is True
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello"
    assert approvals == ["write_file"]


def test_write_rejection_does_not_change_file(tmp_path):
    target = tmp_path / "new.txt"
    target.write_text("old", encoding="utf-8")
    runtime = ToolRuntime(
        make_config(tmp_path, allow_write=True),
        approval=lambda tool, args: False,
    )

    response = result(runtime, "write_file", {"path": "new.txt", "content": "new"})

    assert response["ok"] is False
    assert "拒绝" in response["error"]
    assert target.read_text(encoding="utf-8") == "old"


def test_grep_searches_only_workspace_text(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("alpha\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "secret.txt").write_text("alpha\n", encoding="utf-8")
    runtime = ToolRuntime(make_config(tmp_path))

    response = result(runtime, "grep", {"pattern": "alpha"})

    assert response["ok"] is True
    files = {match["path"] for match in response["matches"]}
    assert files == {"a.txt", "b.md"}


def test_shell_requires_permission_and_approval(tmp_path):
    runtime = ToolRuntime(make_config(tmp_path))
    denied = result(runtime, "run_command", {"command": "echo hi"})
    assert denied["ok"] is False
    assert "命令执行权限" in denied["error"]

    calls = []
    allowed = ToolRuntime(
        make_config(tmp_path, allow_shell=True),
        approval=lambda tool, args: calls.append((tool, args)) or True,
    )
    response = result(allowed, "run_command", {"command": "echo hello"})
    assert response["ok"] is True
    assert response["returncode"] == 0
    assert "hello" in response["stdout"]
    assert calls[0][0] == "run_command"


def test_shell_rejection_returns_denial(tmp_path):
    runtime = ToolRuntime(
        make_config(tmp_path, allow_shell=True),
        approval=lambda tool, args: False,
    )
    response = result(runtime, "run_command", {"command": "echo hi"})
    assert response["ok"] is False
    assert "拒绝" in response["error"]


def test_shell_does_not_pass_deepseek_or_api_key_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "super-secret-value")
    monkeypatch.setenv("MY_API_KEY", "another-secret")
    runtime = ToolRuntime(
        make_config(tmp_path, allow_shell=True),
        approval=lambda tool, args: True,
    )

    response = result(runtime, "run_command", {"command": "echo %DEEPSEEK_API_KEY% %MY_API_KEY%"})

    assert response["ok"] is True
    assert "super-secret-value" not in response["stdout"]
    assert "another-secret" not in response["stdout"]
