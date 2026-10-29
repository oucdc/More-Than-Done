from types import SimpleNamespace
import time

import pytest

from agent.agent_config import AgentConfigStore
from agent.local_agent import AgentSession
from config import Settings


def no_env_settings():
    return Settings(api_key="", model="test-model", base_url="https://example.invalid")


def tool_call(name, arguments="{}", call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def assistant(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def complete_tool_call(self, messages, *, tools, max_tokens):
        self.requests.append({"messages": list(messages), "tools": tools, "max_tokens": max_tokens})
        return self.responses.pop(0)


def make_session(tmp_path, responses, **config):
    store = AgentConfigStore(tmp_path / "agent.json", settings_loader=no_env_settings)
    base = {
        "workspace": str(tmp_path),
        "api_key": "test-key",
        "model": "test-model",
        "base_url": "https://example.invalid",
        "allow_read": True,
        "allow_write": False,
        "allow_shell": False,
        "confirm_write": True,
        "confirm_shell": True,
    }
    base.update(config)
    store.update(base)
    client = FakeClient(responses)
    session = AgentSession(store, client_factory=lambda _settings: client)
    return session, client, store


def wait_status(session, expected, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if session.status == expected:
            return session.snapshot()
        time.sleep(0.02)
    raise AssertionError(f"status {session.status!r} did not become {expected!r}")


def test_agent_reads_workspace_with_tool_and_finishes(tmp_path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    session, client, _ = make_session(
        tmp_path,
        [
            assistant(tool_calls=[tool_call("list_dir")]),
            assistant(content="目录读取完成。"),
        ],
    )

    session.send("看看工作区里有什么")

    snapshot = wait_status(session, "idle")
    texts = [(event["kind"], event["text"]) for event in snapshot["events"]]
    assert ("tool_start", "调用 list_dir") in texts
    assert ("assistant", "目录读取完成。") in texts
    assert len(client.requests) == 2
    assert client.requests[0]["tools"]
    assert client.requests[0]["messages"][-1]["role"] == "user"
    assert client.requests[0]["messages"][-1]["content"] == "看看工作区里有什么"
    assert "notes.txt" in str(snapshot["events"])


def test_agent_write_waits_for_user_approval(tmp_path):
    session, client, _ = make_session(
        tmp_path,
        [
            assistant(tool_calls=[tool_call("write_file", '{"path":"out.txt","content":"written"}')]),
            assistant(content="文件已写入。"),
        ],
        allow_write=True,
    )

    session.send("写一个文件")
    snapshot = wait_status(session, "waiting_approval")

    assert snapshot["pending"]["tool"] == "write_file"
    assert not (tmp_path / "out.txt").exists()
    session.approve(snapshot["pending"]["id"], True)
    wait_status(session, "idle")
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "written"
    assert any(event["kind"] == "tool_result" for event in session.snapshot()["events"])


def test_agent_rejects_write_and_keeps_model_loop_alive(tmp_path):
    session, client, _ = make_session(
        tmp_path,
        [
            assistant(tool_calls=[tool_call("write_file", '{"path":"out.txt","content":"no"}')]),
            assistant(content="写入被拒绝。"),
        ],
        allow_write=True,
    )

    session.send("写一个文件")
    snapshot = wait_status(session, "waiting_approval")
    session.approve(snapshot["pending"]["id"], False)
    snapshot = wait_status(session, "idle")

    assert not (tmp_path / "out.txt").exists()
    assert any(
        "拒绝" in event.get("result", "")
        for event in snapshot["events"]
        if event["kind"] == "tool_result"
    )
    assert any(event["text"] == "写入被拒绝。" for event in snapshot["events"])


def test_agent_read_denied_returns_tool_error(tmp_path):
    session, _, _ = make_session(
        tmp_path,
        [
            assistant(tool_calls=[tool_call("list_dir")]),
            assistant(content="读取被拒绝。"),
        ],
        allow_read=False,
    )

    session.send("看看目录")
    snapshot = wait_status(session, "idle")

    assert any(
        "读取权限" in event.get("result", "")
        for event in snapshot["events"]
        if event["kind"] == "tool_result"
    )


def test_agent_without_key_refuses_to_start(tmp_path):
    store = AgentConfigStore(tmp_path / "agent.json", settings_loader=no_env_settings)
    store.update({"workspace": str(tmp_path), "clear_api_key": True})
    session = AgentSession(store, client_factory=lambda _settings: object())

    with pytest.raises(ValueError, match="API Key"):
        session.send("hello")


def test_save_config_resets_conversation_and_keeps_key_secret(tmp_path):
    session, _, store = make_session(
        tmp_path,
        [assistant(content="done")],
    )
    session.send("hello")
    wait_status(session, "idle")
    assert session.snapshot()["events"]

    updated = session.save_config({
        "workspace": str(tmp_path),
        "api_key": "new-key",
        "allow_read": True,
        "allow_write": False,
        "allow_shell": False,
        "confirm_write": True,
        "confirm_shell": True,
    })

    assert updated["api_key_configured"] is True
    assert "new-key" not in str(session.snapshot())
    assert any("配置已保存" in event["text"] for event in session.snapshot()["events"])


def test_each_turn_refreshes_command_environment_without_exposing_key(tmp_path, monkeypatch):
    environment = {'shell': 'cmd.exe', 'python': 'first-python'}
    monkeypatch.setattr('agent.local_agent.describe_environment', lambda _: dict(environment))
    session, client, _ = make_session(tmp_path, [assistant(content='第一轮。'), assistant(content='第二轮。')])
    session.send('检查环境')
    wait_status(session, 'idle')
    environment['python'] = 'new-python'
    session.send('再检查环境')
    wait_status(session, 'idle')
    prompt = client.requests[-1]['messages'][0]['content']
    assert 'new-python' in prompt and 'first-python' not in prompt
    assert 'CMD' in prompt and 'test-key' not in prompt


def test_session_stop_interrupts_command_without_another_model_call(tmp_path):
    import json
    session, client, _ = make_session(tmp_path, [assistant(tool_calls=[tool_call('run_command', json.dumps({'command': 'python -u -c "import time; time.sleep(20)"'}))])], allow_shell=True, confirm_shell=False)
    session.send('运行可停止的检查')
    deadline = time.time() + 5
    while time.time() < deadline:
        if any(e['kind'] == 'tool_start' for e in session.snapshot()['events']):
            break
        time.sleep(.02)
    session.cancel()
    state = wait_status(session, 'idle')
    outcomes = [json.loads(e['result']) for e in state['events'] if e['kind'] == 'tool_result']
    assert outcomes and outcomes[-1]['cancelled'] is True
    assert len(client.requests) == 1
