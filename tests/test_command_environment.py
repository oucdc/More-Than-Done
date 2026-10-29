import json
import os
from pathlib import Path
import sys
import threading

import pytest

from agent.agent_config import AgentConfig
from agent.command_environment import discover_python, command_environment
from agent.tools import ToolRuntime, run_local_command


@pytest.mark.skipif(os.name != "nt", reason="Windows installation discovery")
def test_store_alias_does_not_hide_conda_installation(tmp_path, monkeypatch):
    alias = tmp_path / "Microsoft" / "WindowsApps" / "python.exe"
    alias.parent.mkdir(parents=True)
    alias.touch()
    python = tmp_path / "programdata" / "miniconda3" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr("agent.command_environment._registered_pythons", lambda: [])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    path, source = discover_python(tmp_path, {"PATH": str(alias.parent), "PROGRAMDATA": str(tmp_path / "programdata")})
    assert path == str(python)
    assert source == "known_installation"


@pytest.mark.skipif(os.name != "nt", reason="Windows installation discovery")
def test_workspace_environment_precedes_active_and_path(tmp_path):
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    path, source = discover_python(tmp_path, {"CONDA_PREFIX": r"C:\other", "PATH": ""})
    assert path == str(python)
    assert source == "workspace"


def test_child_environment_does_not_modify_parent_or_expose_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-propagate")
    monkeypatch.setenv("CUSTOM_TOKEN", "also-private")
    original_path = os.environ.get("PATH")
    env, info = command_environment(tmp_path)
    assert "DEEPSEEK_API_KEY" not in env and "CUSTOM_TOKEN" not in env
    assert os.environ.get("PATH") == original_path
    assert os.environ["DEEPSEEK_API_KEY"] == "must-not-propagate"
    if info["python"]:
        assert env["PATH"].split(os.pathsep)[0] == str(Path(info["python"]).parent)


def test_nonzero_command_is_explicit_failure_with_diagnostics(tmp_path):
    runtime = ToolRuntime(AgentConfig(tmp_path, permission_mode="full"))
    result = json.loads(runtime.execute("run_command", {"command": "exit 7"}))
    assert result["ok"] is False
    assert result["returncode"] == 7
    assert "7" in result["hint"]
    assert result["duration_ms"] >= 0


@pytest.mark.skipif(os.name != "nt", reason="CMD quoting and code page")
def test_selected_python_runs_unicode_command_using_cmd(tmp_path):
    env, info = command_environment(tmp_path)
    result = run_local_command('python -c "print(\'中文输出\')"', tmp_path, env, 10)
    assert result["returncode"] == 0, result
    assert "中文输出" in result["stdout"]


@pytest.mark.skipif(os.name != "nt", reason="CMD diagnostics")
def test_mixed_powershell_syntax_gets_actionable_hint(tmp_path):
    runtime = ToolRuntime(AgentConfig(tmp_path, permission_mode="full"))
    result = json.loads(runtime.execute("run_command", {"command": "dir missing-place 2>$null"}))
    assert not result["ok"]
    assert "CMD" in result["hint"] and "2>nul" in result["hint"]


def test_timeout_retains_partial_unicode_output(tmp_path):
    env, info = command_environment(tmp_path)
    python = str(info["python"] or sys.executable)
    command = f'"{python}" -u -c "import time; print(\'partial output\', flush=True); time.sleep(20)"'
    result = run_local_command(command, tmp_path, env, 1)
    assert result["timed_out"] and result["returncode"] is None
    assert "partial output" in result["stdout"]
    assert result["duration_ms"] < 7000


def test_cancel_stops_running_command_and_retains_output(tmp_path):
    env, info = command_environment(tmp_path)
    python = str(info["python"] or sys.executable)
    command = f'"{python}" -u -c "import time; print(\'started\', flush=True); time.sleep(20)"'
    cancel = threading.Event()
    timer = threading.Timer(.6, cancel.set)
    timer.start()
    try:
        result = run_local_command(command, tmp_path, env, 10, cancel)
    finally:
        timer.cancel()
    assert result["cancelled"] and not result["timed_out"]
    assert "started" in result["stdout"]
    assert result["duration_ms"] < 7000


def test_rejected_command_does_not_prepare_or_execute_environment(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Denied commands must not prepare or execute an environment")
    monkeypatch.setattr("agent.tools.command_environment", forbidden)
    runtime = ToolRuntime(AgentConfig(tmp_path, permission_mode="request"), approval=lambda *_: False)
    result = json.loads(runtime.execute("run_command", {"command": "python --version"}))
    assert result["ok"] is False
