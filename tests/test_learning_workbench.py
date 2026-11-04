"""Exercise artifact, ability, challenge and practice features through the manager."""

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from agent.local_agent import AgentSession
from agent.workspace import WorkspaceManager
from config import Settings


class LearningClient:
    def __init__(self):
        self.requests = 0
        self.review_started = threading.Event()
        self.review_release = threading.Event()
        self.review_release.set()

    def complete_tool_call(self, messages, **kwargs):
        self.requests += 1
        context = json.loads(messages[1]["content"].split("\n", 1)[1])
        submission = context["submission"]
        if submission:
            self.review_started.set()
            assert self.review_release.wait(4), "Test did not release the fake review"
            arguments = {"submission_id": submission["id"], "correctness": "correct",
                         "reasoning_quality": "sound", "evidence": "根据提交快照和用户说明",
                         "feedback": "步骤和解释符合本题标准。"}
            name = "review_learning_attempt"
        elif context["current_step"] is None and not context["completed"]:
            prefix = "隔日复验" if context["prior_tasks"] else "首次学习"
            arguments = {"goal": context["goal"] or "学会检查文本文件", "steps": [
                {"title": f"{prefix}{kind}", "task": f"{prefix}{kind}任务",
                 "success_criteria": "能说明实际结果与理由", "kind": kind}
                for kind in ("independent", "transfer")
            ]}
            name = "plan_learning"
        else:
            return SimpleNamespace(content="请按当前任务亲手尝试。", tool_calls=None)
        return SimpleNamespace(content=None, tool_calls=[{
            "id": f"call-{self.requests}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }])


def wait_idle(session):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if session.worker is None:
            assert session.status == "idle", session.events
            return
        time.sleep(.01)
    raise AssertionError("The fake-model task did not finish")


def make_manager(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentConfigStore(tmp_path / "data" / "config.json", settings_loader=lambda: Settings(api_key="", model="fake"))
    config.update({"workspace": str(workspace), "api_key": "isolated-test-key", "permission_mode": "request"})
    store = ConversationStore(tmp_path / "data" / "chats.json")
    client = LearningClient()
    session = AgentSession(config, client_factory=lambda _: client)
    manager = WorkspaceManager(config, store, session)
    manager.create_project({"workspace": str(workspace)})
    manager.send("我要学会检查文本文件")
    wait_idle(session)
    return manager, store, client, workspace


def submit(manager, answer, **metadata):
    chat = manager.snapshot()["active_conversation_id"]
    manager.submit_attempt(answer, conversation_id=chat, **metadata)
    wait_idle(manager.session)


def next_step(manager):
    chat = manager.snapshot()["active_conversation_id"]
    manager.teaching_action("next", conversation_id=chat)
    wait_idle(manager.session)


def test_work_submission_freezes_content_and_exposes_evidence_summaries(tmp_path):
    manager, store, client, workspace = make_manager(tmp_path)
    work = workspace / "work.txt"
    work.write_text("提交时的第一版作品\n", encoding="utf-8")
    original = work.read_bytes().decode("utf-8")
    client.review_release.clear()
    chat = manager.snapshot()["active_conversation_id"]
    manager.submit_attempt("我先预测再检查文件内容", paths=["work.txt"], prediction="将看到第一版",
                           confidence=80, observed_result="第一版内容完整", conversation_id=chat)
    try:
        assert client.review_started.wait(2)
        work.write_text("提交后修改的第二版作品", encoding="utf-8")
    finally:
        client.review_release.set()
    wait_idle(manager.session)
    saved = store.load()["conversations"][0]["lesson"]["attempts"][0]
    assert saved["artifacts"][0]["content"] == original
    assert saved["artifacts"][0]["hash"] == hashlib.sha256(original.encode()).hexdigest()
    assert saved["explanation"] == "我先预测再检查文件内容"
    assert saved["prediction"] == "将看到第一版" and saved["confidence"] == 80
    assert saved["observed_result"] == "第一版内容完整"
    card = manager.snapshot()["lesson"]["evidence_cards"][0]
    assert "content" not in card["artifacts"][0]
    assert card["artifacts"][0]["checks"][0]["passed"]
    portfolio = manager.portfolio()
    assert portfolio["artifact_count"] == 1
    assert "content" not in portfolio["skills"][0]["artifacts"][0]


@pytest.mark.parametrize("path", [".env.local", "../outside.txt", "large.txt"])
def test_invalid_work_is_rejected_before_starting_a_model_request(tmp_path, path):
    manager, store, client, workspace = make_manager(tmp_path)
    (workspace / ".env.local").write_text("DEMO_API_KEY=FAKE_ONLY", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    (workspace / "large.txt").write_text("x" * 20001, encoding="utf-8")
    before = store.path.read_bytes()
    requests = client.requests
    chat = manager.snapshot()["active_conversation_id"]
    with pytest.raises(ValueError):
        manager.submit_attempt("检查作品", paths=[path], conversation_id=chat)
    assert manager.session.worker is None and client.requests == requests
    assert store.path.read_bytes() == before
    assert not manager.session.lesson.public()["pending_review"]


def test_due_challenge_opens_new_chat_and_records_delayed_verification(tmp_path, monkeypatch):
    manager, store, client, workspace = make_manager(tmp_path)
    original_chat = manager.snapshot()["active_conversation_id"]
    with monkeypatch.context() as past:
        past.setattr("learning.guidance.timestamp", lambda: "2000-01-01T10:00:00+00:00")
        submit(manager, "第一次独立作答")
        next_step(manager)
        submit(manager, "第一次迁移作答")
    skill = manager.portfolio()["due_challenges"][0]
    original_tasks = set(skill["prior_tasks"])
    state = manager.start_challenge(skill["skill_id"])
    wait_idle(manager.session)
    current = manager.snapshot()
    assert state["active_conversation_id"] != original_chat
    assert current["lesson"]["skill_id"] == skill["skill_id"]
    assert not original_tasks.intersection(step["task"] for step in current["lesson"]["steps"])
    submit(manager, "隔日新题独立作答")
    next_step(manager)
    submit(manager, "隔日新情境迁移作答")
    portfolio = manager.portfolio()
    assert len(portfolio["skills"]) == 1 and not portfolio["due_challenges"]
    skill = portfolio["skills"][0]
    assert skill["delayed_verified"] and skill["attempt_count"] == 4
    assert datetime.fromisoformat(skill["due_at"]) == datetime.fromisoformat(skill["last_verified_at"]) + timedelta(days=7)
    assert len(store.load()["conversations"]) == 2


def test_practice_copy_diff_and_reset_preserve_the_source_workspace(tmp_path):
    manager, store, client, workspace = make_manager(tmp_path)
    source = workspace / "notes.txt"
    source.write_text("original\n", encoding="utf-8")
    original_chat = manager.snapshot()["active_conversation_id"]
    state = manager.make_practice_copy(["notes.txt"], conversation_id=original_chat)
    practice_root = Path(state["active_project"]["workspace"])
    copied = practice_root / "notes.txt"
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert copied.read_bytes() == source.read_bytes()
    assert manager.practice(conversation_id=state["active_conversation_id"])["diffs"][0]["diff"] == ""
    copied.write_text("changed in practice\n", encoding="utf-8")
    chat = state["active_conversation_id"]
    diff = manager.practice(conversation_id=chat)
    assert diff["available"] and "+changed in practice" in diff["diffs"][0]["diff"]
    manager.practice(reset=True, conversation_id=chat)
    assert copied.read_text(encoding="utf-8") == "original\n"
    assert source.read_text(encoding="utf-8") == "original\n"


def test_history_paginates_before_the_latest_five_hundred_events(tmp_path):
    manager, store, client, workspace = make_manager(tmp_path)
    chat = manager.snapshot()["active_conversation_id"]
    for index in range(620):
        manager.session.emit("system", f"event-{index}")
    state = manager.snapshot()
    assert len(state["events"]) == 500 and state["history_before"] is not None
    before = state["history_before"]
    older = []
    while True:
        page = manager.history(chat, before, limit=100)
        older = page["events"] + older
        if not page["has_more"]:
            break
        before = page["before"]
    combined = older + state["events"]
    ids = [event["id"] for event in combined]
    assert ids == sorted(set(ids))
    assert len(combined) == len(manager.session.events)
    restarted = WorkspaceManager(manager.config_store, store, AgentSession(manager.config_store))
    restored = restarted.snapshot()
    assert restored["history_before"] == state["history_before"]
    assert len(restarted.history(chat, restored["history_before"])["events"]) == 100


def test_prediction_is_saved_through_normal_entry_before_a_model_request(tmp_path):
    manager, store, client, workspace = make_manager(tmp_path)
    chat = manager.snapshot()["active_conversation_id"]
    requests = client.requests
    state = manager.record_prediction("我预计会读到两列", 60, conversation_id=chat)
    assert client.requests == requests
    saved = store.load()["conversations"][0]["lesson"]["pending_prediction"]
    assert saved == state["lesson"]["pending_prediction"]
    assert saved["text"] == "我预计会读到两列"
    with pytest.raises(ValueError, match="不一致"):
        manager.submit_attempt("实际有三列", prediction="改成三列", conversation_id=chat)
    submit(manager, "因为表头多了一项", observed_result="实际有三列")
    card = manager.snapshot()["lesson"]["evidence_cards"][0]
    assert card["prediction_recorded_at"] == saved["recorded_at"]
    assert card["prediction"] == saved["text"] and card["confidence"] == 60


def test_failed_prediction_save_restores_the_previous_prediction_state(tmp_path, monkeypatch):
    manager, store, client, workspace = make_manager(tmp_path)
    before = manager.snapshot()
    requests = client.requests

    def fail(_):
        raise OSError("Simulated disk failure")

    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(OSError):
        manager.record_prediction("预测内容", 50, conversation_id=before["active_conversation_id"])
    assert manager.session.lesson.public()["pending_prediction"] is None
    assert client.requests == requests
