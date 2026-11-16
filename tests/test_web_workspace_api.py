import json
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from agent.local_agent import AgentSession
from agent.workspace import WorkspaceManager
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
            payload={"message": "开始工作", "conversation_id": state["active_conversation_id"]},
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


def test_legacy_config_and_new_chat_cannot_overwrite_workspace_history(tmp_path):
    config = AgentConfigStore(tmp_path / "config.json", settings_loader=no_env_settings)
    config.update({"workspace": str(tmp_path), "api_key": "test-only"})
    conversations = ConversationStore(tmp_path / "chats.json")
    server = web_ui.MoreThanDoneHTTPServer(("127.0.0.1", 0), object(), config_store=config, conversation_store=conversations)
    server.workspace.create_project({"workspace": str(tmp_path)})
    server.agent.messages = [{"role": "user", "content": "keep"}, {"role": "assistant", "content": "kept"}]
    server.agent.lesson.plan({"goal": "keep goal", "steps": [{"title": "step", "kind": "guided", "task": "keep task", "success_criteria": "evidence"}]})
    server.workspace.snapshot()
    before = conversations.load()["conversations"][0]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for path, payload in [("/api/agent/config", {"model": "changed-model"}), ("/api/agent/new", {})]:
            assert request(base, path, method="POST", payload=payload)[0] == 200
            assert request(base, "/api/workspace/state")[0] == 200
            after = conversations.load()["conversations"][0]
            assert after["id"] == before["id"]
            assert after["messages"] == before["messages"]
            assert after["lesson"] == before["lesson"]
        chat_id = before["id"]
        for payload in [{"message": "wrong chat"}, {"message": "wrong chat", "conversation_id": None}, {"message": "wrong chat", "conversation_id": "stale-chat"}]:
            assert request(base, "/api/workspace/chat", method="POST", payload=payload)[0] == 409
        assert server.workspace.snapshot()["active_conversation_id"] == chat_id
        assert server.agent.worker is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class ChoiceClient:
    def __init__(self):
        self.model_inputs = []

    def complete_tool_call(self, messages, *, tools, max_tokens):
        self.model_inputs.append((messages, {tool["function"]["name"] for tool in tools}))
        last = messages[-1]
        if last["role"] == "user" and last["content"].startswith("请问我"):
            options = ["继续", "先谈思路"]
            if "重复" in last["content"]:
                options = ["继续", " 继续 "]
            return SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
                id=f"choice-{len(self.model_inputs)}",
                function=SimpleNamespace(name="ask_user_choice", arguments=json.dumps({
                    "question": "你想怎样进行？",
                    "options": options,
                }, ensure_ascii=False)),
            )])
        return SimpleNamespace(content="导师继续了普通对话。", tool_calls=None)


@contextmanager
def choice_workbench(tmp_path):
    config = AgentConfigStore(tmp_path / "config.json", settings_loader=no_env_settings)
    config.update({"workspace": str(tmp_path), "api_key": "test-only"})
    store = ConversationStore(tmp_path / "chats.json")
    server = web_ui.MoreThanDoneHTTPServer(("127.0.0.1", 0), object(), config_store=config, conversation_store=store)
    client = ChoiceClient()
    server.agent.client_factory = lambda _: client
    server.workspace.create_project({"workspace": str(tmp_path)})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, client, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def wait_for_choice_turn(server, base):
    with server.agent.changed:
        assert server.agent.changed.wait_for(lambda: server.agent.status in {"idle", "error"}, timeout=5)
    status, body = request(base, "/api/workspace/state")
    state = json.loads(body)
    assert status == 200 and state["status"] == "idle", state
    return state


def test_workspace_choice_round_trip_validates_the_active_question_and_conversation(tmp_path):
    with choice_workbench(tmp_path) as (server, client, base):
        lesson = server.agent.lesson
        lesson.plan({"goal": "梳理文章论点", "steps": [
            {"title": "找出中心论点", "kind": "guided", "task": "标记中心句", "success_criteria": "能解释为何选这句"},
            {"title": "独立检查", "kind": "independent", "task": "为另一篇文章标记中心句", "success_criteria": "用文意支持选择"},
        ]})
        attempt = lesson.submit("第二句说明作者的核心观点。")
        lesson.review({
            "submission_id": attempt["id"], "correctness": "correct", "reasoning_quality": "sound",
            "evidence": "作答引用了中心句并解释了原因。", "feedback": "通过。",
        })
        conversation_id = server.workspace.snapshot()["active_conversation_id"]

        def post(path, **payload):
            return request(base, "/api/workspace/" + path, method="POST", payload=payload)

        def chat(message):
            status, body = post("chat", conversation_id=conversation_id, message=message)
            assert status == 200, body
            return wait_for_choice_turn(server, base)

        state = chat("请问我接下来想做什么")
        event = state["events"][-1]
        assert event["kind"] == "choice_question"
        assert event["text"] == "你想怎样进行？"
        assert event["options"] == ["继续", "先谈思路"]
        question_id = event["id"]
        assert state["active_question_id"] == question_id
        assert len(client.model_inputs) == 1
        assert "ask_user_choice" in client.model_inputs[0][1]
        assert state["lesson"]["can_continue"]

        def answer(**selection):
            return post("chat/choice", conversation_id=conversation_id, question_id=question_id, **selection)

        bad_selections = ({}, {"option_index": True}, {"option_index": 2}, {"other": "  "},
                          {"option_index": 0, "other": "重复发送"}, {"option_index": 0, "other": None})
        for selection in bad_selections:
            assert answer(**selection)[0] == 400, selection
        assert post("chat/choice", question_id=question_id, option_index=0)[0] == 409
        assert post("chat/choice", conversation_id=conversation_id, question_id=str(question_id), option_index=0)[0] == 400
        assert post("chat/choice", conversation_id=conversation_id, question_id=question_id + 1, option_index=0)[0] == 409
        assert len(client.model_inputs) == 1

        restored = WorkspaceManager(server.agent_config, server.workspace.conversation_store, AgentSession(server.agent_config))
        assert restored.snapshot()["active_question_id"] == question_id
        assert post("conversation")[0] == 200
        assert answer(option_index=0)[0] == 409
        status, body = post("select-conversation", id=conversation_id)
        assert status == 200 and json.loads(body)["active_question_id"] == question_id

        assert answer(option_index=0)[0] == 200
        state = wait_for_choice_turn(server, base)
        assert state["active_question_id"] is None
        assert state["lesson"]["current"] == 0 and state["lesson"]["can_continue"]
        assert len(state["lesson"]["attempts"]) == 1
        context = client.model_inputs[-1][0]
        model_call = context[-3]["tool_calls"][0]
        assert model_call["function"]["name"] == "ask_user_choice"
        assert context[-2]["role"] == "tool" and context[-2]["tool_call_id"] == model_call["id"]
        assert context[-1]["role"] == "user"
        assert "你想怎样进行？" in context[-1]["content"] and context[-1]["content"].endswith("\n继续")
        assert any(item["kind"] == "user" and item["text"] == "继续" for item in state["events"])
        assert answer(option_index=1)[0] == 409

        question_id = chat("请问我想如何梳理文章")["active_question_id"]
        assert answer(other="  我想先试另一篇文章。\n ")[0] == 200
        state = wait_for_choice_turn(server, base)
        assert state["active_question_id"] is None
        message = client.model_inputs[-1][0][-1]
        assert message["role"] == "user" and message["content"].endswith("\n我想先试另一篇文章。")
        assert any(item["kind"] == "user" and item["text"] == "我想先试另一篇文章。" for item in state["events"])

        question_id = chat("请问我还有什么要补充")["active_question_id"]
        assert chat("我直接在聊天框回复。")["active_question_id"] is None
        assert answer(other="迟到的回复")[0] == 409


def test_choice_tool_recovers_from_invalid_options_and_cannot_replace_a_learning_review(tmp_path):
    with choice_workbench(tmp_path) as (server, client, base):
        conversation_id = server.workspace.snapshot()["active_conversation_id"]

        status, body = request(base, "/api/workspace/chat", method="POST", payload={
            "conversation_id": conversation_id, "message": "请问我，重复选项",
        })
        assert status == 200, body
        state = wait_for_choice_turn(server, base)
        assert state["active_question_id"] is None
        assert all(event["kind"] != "choice_question" for event in state["events"])
        assert state["events"][-1]["text"] == "导师继续了普通对话。"
        result = next(item for item in client.model_inputs[-1][0] if item["role"] == "tool")
        assert json.loads(result["content"])["ok"] is False

        server.agent.lesson.plan({"goal": "检查我的解释", "steps": [
            {"title": "解释用词", "kind": "guided", "task": "解释文中的用词", "success_criteria": "引用原句"},
        ]})
        status, body = request(base, "/api/workspace/learning/attempt", method="POST", payload={
            "conversation_id": conversation_id, "answer": "请问我怎样理解这个词",
        })
        assert status == 200, body
        state = wait_for_choice_turn(server, base)
        assert state["lesson"]["pending_review"] and not state["lesson"]["can_continue"]
        assert state["active_question_id"] is None
        assert all(event["kind"] != "choice_question" for event in state["events"])
        assert "ask_user_choice" not in client.model_inputs[-1][1]
