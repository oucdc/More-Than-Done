import json
import time
from types import SimpleNamespace

from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from agent.local_agent import AgentSession
from agent.workspace import WorkspaceManager
from config import Settings


def reply(content=None, tool=None, arguments=None):
    calls = [SimpleNamespace(id="call-1", function=SimpleNamespace(name=tool, arguments=json.dumps(arguments, ensure_ascii=False)))] if tool else None
    return SimpleNamespace(content=content, tool_calls=calls)


class TeachingClient:
    def complete_tool_call(self, messages, *, tools, max_tokens):
        context = json.loads(messages[1]["content"].split("\n", 1)[1])
        if not context["goal"]:
            return reply(tool="plan_learning", arguments={"goal": "学会读懂项目", "steps": [{"title": "理解 README", "task": "读取 README 后用自己的话解释项目用途", "success_criteria": "解释用途和一个启动步骤", "kind": "guided"}, {"title": "独立迁移", "task": "找另一个说明文档并独立解释", "success_criteria": "有自己的解释和依据", "kind": "independent"}]})
        if context["submission"]:
            return reply(tool="review_learning_attempt", arguments={"submission_id": context["submission"]["id"], "correctness": "correct", "reasoning_quality": "sound", "evidence": "用户解释了用途和启动方式", "feedback": "这次解释满足本步标准。"})
        return reply("先请你读 README，然后用自己的话解释项目用途。")


def manager(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentConfigStore(tmp_path / "agent_config.json", settings_loader=lambda: Settings(api_key="", model="test", base_url="https://example.invalid"))
    config.update({"workspace": str(workspace), "api_key": "test-only", "model": "test"})
    store = ConversationStore(tmp_path / "conversations.json")
    session = AgentSession(config, client_factory=lambda _: TeachingClient())
    manager = WorkspaceManager(config, store, session)
    manager.create_project({"workspace": str(workspace)})
    return manager, store


def finish(session):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        worker = session.worker
        if worker is not None: worker.join(timeout=.1)
        if session.status in {"idle", "error"} and (session.worker is None or not session.worker.is_alive()): return
    raise AssertionError("Teaching run did not finish")


def test_teaching_is_used_by_the_same_workspace_and_persisted_without_polling(tmp_path):
    workspace, store = manager(tmp_path)
    workspace.send("请带我读懂项目")
    finish(workspace.session)
    document = store.load()
    assert document["conversations"][0]["lesson"]["goal"] == "学会读懂项目"
    assert workspace.snapshot()["lesson"]["current"] == 0
    workspace.teaching_action("hint")
    finish(workspace.session)
    workspace.submit_attempt("这是一个学习项目，我会按 README 中的启动命令运行")
    finish(workspace.session)
    state = workspace.snapshot()
    assert state["lesson"]["attempts"][0]["practice"]["hint_level"] == 1
    assert state["lesson"]["can_continue"]
    workspace.teaching_action("next")
    finish(workspace.session)
    assert workspace.snapshot()["lesson"]["current"] == 1
    restored = WorkspaceManager(workspace.config_store, store, AgentSession(workspace.config_store, client_factory=lambda _: TeachingClient()))
    assert restored.snapshot()["lesson"]["current"] == 1
    assert restored.snapshot()["lesson"]["attempts"][0]["practice"]["hint_level"] == 1


def test_state_reads_do_not_write_or_reorder_conversations(tmp_path, monkeypatch):
    workspace, store = manager(tmp_path)
    first = workspace.snapshot()
    calls = []
    original = store.save
    monkeypatch.setattr(store, "save", lambda data: (calls.append(data), original(data))[-1])
    for _ in range(4): workspace.snapshot()
    assert not calls
    assert workspace.snapshot()["active_conversation"]["updated_at"] == first["active_conversation"]["updated_at"]


def test_switching_conversations_keeps_learning_evidence_separate(tmp_path):
    workspace, _ = manager(tmp_path)
    first = workspace.snapshot()["active_conversation_id"]
    workspace.send("开始学习")
    finish(workspace.session)
    workspace.teaching_action("demonstrate")
    finish(workspace.session)
    new = workspace.new_conversation()
    assert new["lesson"]["steps"] == []
    restored = workspace.select_conversation(first)
    assert restored["lesson"]["current_step"]["answer_viewed"]
