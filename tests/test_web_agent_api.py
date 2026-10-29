import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent.agent_config import AgentConfigStore
from agent.local_agent import AgentSession
from config import Settings
import web_ui


def no_env_settings():
    return Settings(api_key="", model="test-model", base_url="https://example.invalid")


def tool_call(name, arguments="{}", call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete_tool_call(self, messages, *, tools, max_tokens):
        return self.responses.pop(0)


class ServerHarness:
    def __init__(self, workspace: Path, responses, *, allow_write=False):
        store = AgentConfigStore(workspace.parent / "agent_config.json", settings_loader=no_env_settings)
        self.session = AgentSession(
            store,
            client_factory=lambda _settings: FakeClient(responses),
        )
        self.server = web_ui.MoreThanDoneHTTPServer(("127.0.0.1", 0), object())
        self.server.agent = self.session
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.workspace = workspace
        self.allow_write = allow_write

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, *, method="GET", payload=None):
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8")
                return response.status, body
        except HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def configure(self):
        return self.request(
            "/api/agent/config",
            method="POST",
            payload={
                "workspace": str(self.workspace),
                "api_key": "web-test-key",
                "model": "test-model",
                "base_url": "https://example.invalid",
                "allow_read": True,
                "allow_write": self.allow_write,
                "allow_shell": False,
                "confirm_write": True,
                "confirm_shell": True,
            },
        )

    def state(self):
        status, body = self.request("/api/agent/state")
        return status, json.loads(body)

    def wait(self, status, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            code, snapshot = self.state()
            if code == 200 and snapshot["status"] == status:
                return snapshot
            time.sleep(0.03)
        raise AssertionError(f"did not reach {status}: {self.state()}")


def test_agent_page_assets_and_config_do_not_leak_key(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = ServerHarness(workspace, [message(content="ok")])
    try:
        status, body = harness.request("/agent")
        assert status == 200
        assert "Agent 工作台" in body
        status, body = harness.request("/agent-assets/agent.js")
        assert status == 200
        assert "/api/agent/chat" in body
        status, body = harness.request("/agent-assets/agent.css")
        assert status == 200

        status, body = harness.configure()
        assert status == 200
        snapshot = json.loads(body)
        assert snapshot["config"]["workspace"] == str(workspace.resolve())
        assert "web-test-key" not in body
        assert snapshot["config"]["api_key_configured"] is True

        status, body = harness.request("/api/agent/config")
        assert status == 200
        assert "web-test-key" not in body
        assert json.loads(body)["config"]["api_key_source"] == "config"
    finally:
        harness.close()


def test_agent_chat_runs_tool_loop_over_http(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    harness = ServerHarness(
        workspace,
        [
            message(tool_calls=[tool_call("list_dir")]),
            message(content="读取完成。"),
        ],
    )
    try:
        assert harness.configure()[0] == 200
        status, body = harness.request(
            "/api/agent/chat",
            method="POST",
            payload={"message": "看看目录"},
        )
        assert status == 200
        snapshot = harness.wait("idle")
        events = snapshot["events"]
        assert any(event["kind"] == "tool_start" and event["tool"] == "list_dir" for event in events)
        assert any(event["kind"] == "tool_result" and "notes.txt" in event.get("result", "") for event in events)
        assert any(event["kind"] == "assistant" and event["text"] == "读取完成。" for event in events)
        assert "web-test-key" not in json.dumps(snapshot, ensure_ascii=False)
    finally:
        harness.close()


def test_agent_write_approval_over_http(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    harness = ServerHarness(
        workspace,
        [
            message(tool_calls=[tool_call(
                "write_file",
                '{"path":"out.txt","content":"from web"}',
            )]),
            message(content="写入完成。"),
        ],
        allow_write=True,
    )
    try:
        assert harness.configure()[0] == 200
        status, body = harness.request(
            "/api/agent/chat",
            method="POST",
            payload={"message": "写文件"},
        )
        assert status == 200
        snapshot = harness.wait("waiting_approval")
        pending = snapshot["pending"]
        assert pending["tool"] == "write_file"
        assert not (workspace / "out.txt").exists()

        status, body = harness.request(
            "/api/agent/approve",
            method="POST",
            payload={"id": pending["id"], "allow": True},
        )
        assert status == 200
        harness.wait("idle")
        assert (workspace / "out.txt").read_text(encoding="utf-8") == "from web"
    finally:
        harness.close()
