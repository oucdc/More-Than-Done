import json
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from config import Settings
import web_ui


def no_env_settings():
    return Settings(api_key="", model="test-model", base_url="https://example.invalid")


class FakeClient:
    def complete_tool_call(self, messages, *, tools, max_tokens):
        return SimpleNamespace(content="工作区完成。", tool_calls=None)


def request(base, path, *, method="GET", payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_workspace_page_and_api_flow(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    second = tmp_path / "second"
    second.mkdir()
    config_store = AgentConfigStore(tmp_path / "agent_config.json", settings_loader=no_env_settings)
    config_store.update({
        "workspace": str(workspace),
        "api_key": "workspace-test-key",
        "model": "deepseek-flash",
        "base_url": "https://example.invalid",
        "reasoning_level": "medium",
    })
    conversation_store = ConversationStore(tmp_path / "conversations.json")
    server = web_ui.MoreThanDoneHTTPServer(
        ("127.0.0.1", 0),
        object(),
        config_store=config_store,
        conversation_store=conversation_store,
    )
    server.agent.client_factory = lambda _settings: FakeClient()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, body = request(base, "/workspace")
        assert status == 200
        assert "LEARNING WORKSPACE" in body
        assert "学习导航" in body
        assert "提交我的尝试" in body
        assert "项目" in body
        status, body = request(base, "/workspace-assets/workspace.js")
        assert status == 200
        assert "/api/workspace/state" in body

        status, body = request(base, "/api/workspace/state")
        assert status == 200
        state = json.loads(body)
        assert state["projects"] == [] and state["active_project"] is None
        assert state["active_conversation_id"] is None and state["events"] == []
        assert state["config"].get("workspace") in (None, "")
        status, body = request(base, "/api/workspace/chat", method="POST", payload={"message": "不应访问旧目录"})
        assert status == 400 and "请先创建或选择项目" in body
        status, body = request(base, "/api/workspace/config", method="POST", payload={"model": "deepseek-chat"})
        assert status == 200 and json.loads(body)["projects"] == []
        status, body = request(base, "/api/workspace/project", method="POST", payload={"name": "第一个项目", "workspace": str(workspace)})
        assert status == 200
        state = json.loads(body)
        assert len(state["projects"]) == 1
        assert state["active_project"]["workspace"] == str(workspace.resolve())
        assert state["config"]["reasoning_level"] == "medium"
        assert "workspace-test-key" not in body
        assert state["environment"]["shell"]
        assert "python_source" in state["environment"]
        first_conversation = state["active_conversation_id"]

        status, body = request(
            base,
            "/api/workspace/config",
            method="POST",
            payload={"model": "deepseek-chat", "reasoning_level": "high"},
        )
        assert status == 200
        state = json.loads(body)
        assert state["config"]["model"] == "deepseek-chat"
        assert state["config"]["reasoning_level"] == "high"

        status, body = request(
            base,
            "/api/workspace/project",
            method="POST",
            payload={"name": "第二项目", "workspace": str(second)},
        )
        assert status == 200
        state = json.loads(body)
        assert len(state["projects"]) == 2
        assert state["active_project"]["workspace"] == str(second.resolve())

        status, body = request(
            base,
            "/api/workspace/chat",
            method="POST",
            payload={"message": "开始工作"},
        )
        assert status == 200
        deadline = time.time() + 5
        while time.time() < deadline:
            status, body = request(base, "/api/workspace/state")
            state = json.loads(body)
            if state["status"] == "idle":
                break
            time.sleep(0.03)
        assert state["status"] == "idle"
        assert any(event["kind"] == "assistant" and event["text"] == "工作区完成。" for event in state["events"])
        assert state["active_conversation"]["title"] == "开始工作"
        assert "workspace-test-key" not in body

        status, body = request(
            base,
            "/api/workspace/select-conversation",
            method="POST",
            payload={"id": first_conversation},
        )
        assert status == 200
        state = json.loads(body)
        assert state["active_conversation_id"] == first_conversation

        status, body = request(base, "/api/workspace/edit-conversation", method="POST", payload={"id": first_conversation, "action": "rename", "value": "新名称"})
        assert status == 200 and json.loads(body)["active_conversation"]["title"] == "新名称"
        status, body = request(base, "/api/workspace/edit-conversation", method="POST", payload={"id": first_conversation, "action": "pin", "value": True})
        assert status == 200 and json.loads(body)["active_conversation"]["pinned"]
        status, body = request(base, "/api/workspace/edit-conversation", method="POST", payload={"id": first_conversation, "action": "archive", "value": True})
        assert status == 200 and json.loads(body)["archived_conversations"][0]["id"] == first_conversation
        status, body = request(base, "/api/workspace/edit-conversation", method="POST", payload={"id": first_conversation, "action": "archive", "value": False})
        assert status == 200 and not json.loads(body)["archived_conversations"]
        status, body = request(base, "/api/workspace/select-conversation", method="POST", payload={"id": first_conversation})
        state = json.loads(body)
        assert status == 200 and state["active_conversation_id"] == first_conversation

        status, body = request(base, "/api/workspace/delete-conversation", method="POST", payload={"id": first_conversation})
        assert status == 200
        deleted = json.loads(body)
        assert all(chat["id"] != first_conversation for chat in deleted["conversations"])
        assert deleted["active_project_id"] == state["active_project_id"]
        assert deleted["active_conversation_id"] != first_conversation
        status, body = request(base, "/api/workspace/delete-conversation", method="POST", payload={"id": first_conversation})
        assert status == 400 and "已被删除" in body
        for project in deleted["projects"]:
            status, body = request(base, "/api/workspace/edit-project", method="POST", payload={"id": project["id"], "action": "delete"})
            assert status == 200
        status, body = request(base, "/api/workspace/state")
        empty = json.loads(body)
        assert status == 200 and empty["projects"] == []
        assert empty["active_project"] is None and empty["active_conversation_id"] is None
        assert empty["events"] == [] and empty["config"].get("workspace") in (None, "")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
