from copy import deepcopy

import pytest

from learning.guidance import GuidanceNotebook


def step(kind="guided", task="在工作区创建一个问候文件，并解释内容"):
    return {"title": "动手试一次", "task": task, "success_criteria": "文件存在，内容正确，并能解释自己的做法", "kind": kind}


def plan(*steps):
    notebook = GuidanceNotebook()
    notebook.plan({"goal": "学会操作本地文件", "steps": list(steps) or [step()]})
    return notebook


def review(notebook, **overrides):
    payload = {"submission_id": notebook.context()["submission"]["id"], "correctness": "correct", "reasoning_quality": "sound", "evidence": "用户解释了文件用途，读取的文件内容与标准一致", "feedback": "你的做法满足本步骤标准。"}
    payload.update(overrides)
    return notebook.review(payload)


def test_progress_requires_a_real_submission_and_review():
    notebook = plan()
    with pytest.raises(ValueError, match="提交"):
        notebook.action("next")
    with pytest.raises(ValueError, match="真实用户"):
        notebook.review({"submission_id": "invented"})
    notebook.submit("我写了问候内容，并用文件表达一个完整句子")
    assert notebook.public()["pending_review"]
    review(notebook)
    assert notebook.public()["can_continue"]
    assert not notebook.public()["mastery_verified"]
    notebook.action("next")
    assert notebook.public()["completed"]


def test_review_cannot_be_replayed_and_does_not_advance_a_step():
    notebook = plan(step(), step("independent", "换一个新文件独立操作"))
    submission = notebook.submit("我的实际尝试")
    review(notebook)
    assert notebook.public()["current"] == 0
    with pytest.raises(ValueError, match="真实用户"):
        notebook.review({"submission_id": submission["id"]})


def test_help_is_monotonic_and_mode_switch_does_not_erase_it():
    notebook = plan(step("independent"))
    for _ in range(6): notebook.action("hint")
    notebook.action("demonstrate")
    notebook.action("independent")
    current = notebook.public()["current_step"]
    assert current["hint_level"] == 4
    assert current["answer_viewed"] is True
    notebook.submit("看过演示之后的回答")
    review(notebook)
    assert notebook.public()["independent_successes"] == 0


def test_later_feedback_does_not_retroactively_change_submitted_help_evidence():
    notebook = plan(step("independent"))
    notebook.submit("帮助发生前已独立提交的答案")
    notebook.help("hint")
    review(notebook)
    assert notebook.public()["attempts"][0]["practice"]["hint_level"] == 0


def test_agent_writing_during_review_cannot_be_counted_as_learner_independent_work():
    notebook = plan(step("independent"))
    notebook.submit("请检查我写的文件")
    notebook.record_agent_execution()
    review(notebook)
    assert notebook.public()["attempts"][0]["practice"]["answer_viewed"]
    assert notebook.public()["independent_successes"] == 0


def test_mastery_requires_unassisted_independent_then_new_transfer():
    notebook = plan(step("independent", "独立创建文本文件"), step("transfer", "把同一方法迁移到配置文件"))
    notebook.submit("第一题答案和理由")
    review(notebook)
    notebook.action("next")
    notebook.submit("新情境下的答案和理由")
    review(notebook)
    assert notebook.public()["mastery_verified"]


@pytest.mark.parametrize("mode", ["request", "auto", "full"])
@pytest.mark.parametrize("command", ["pwd", "dir"])
def test_read_only_command_during_review_preserves_independent_evidence(tmp_path, mode, command):
    import json
    from agent.agent_config import AgentConfig
    from agent.tools import ToolRuntime
    notebook = plan(step("independent"))
    notebook.submit("我独立完成了文件内容和用途解释")
    approvals = []
    runtime = ToolRuntime(AgentConfig(tmp_path, permission_mode=mode), notebook=notebook,
                          approval=lambda tool, args: approvals.append(tool) or True)
    outcome = json.loads(runtime.execute("run_command", {"command": command}))
    assert approvals == (["run_command"] if mode == "request" else [])
    assert outcome["execution"] == "read_only_check"
    review(notebook)
    assert notebook.public()["independent_successes"] == 1
    assert not notebook.public()["current_step"]["answer_viewed"]


@pytest.mark.parametrize("help_kind", [None, "hint", "demonstrate"])
def test_failed_transfer_recovers_via_new_tasks_without_erasing_evidence(help_kind):
    notebook = plan(step("independent", "旧独立题"), step("transfer", "旧迁移题"), step("explain", "后续概念"))
    notebook.submit("第一题答案")
    review(notebook)
    notebook.action("next")
    if help_kind:
        notebook.help(help_kind)
    notebook.submit("迁移题第一次尝试")
    review(notebook, correctness="incorrect")
    notebook.action("retry")
    notebook = GuidanceNotebook(notebook.export())
    before = notebook.export()
    with pytest.raises(ValueError, match="新任务"):
        notebook.plan({"append": True, "steps": [step("independent", "旧独立题"), step("transfer", "新迁移题")]})
    assert notebook.export() == before
    notebook.plan({"append": True, "steps": [step("independent", "新的独立任务"), step("transfer", "新的迁移场景")]})
    state = notebook.public()
    assert state["current"] == 2 and state["current_step"]["kind"] == "independent"
    assert state["steps"][1]["status"] == "skipped"
    assert state["steps"][-1]["task"] == "后续概念"
    assert state["attempts"] == before["attempts"]
    assert state["steps"][1]["hint_level"] == before["steps"][1]["hint_level"]
    assert len({s["id"] for s in state["steps"]}) == 5
    notebook.submit("新独立答案")
    review(notebook)
    notebook.action("next")
    notebook.submit("新迁移答案")
    review(notebook)
    assert notebook.public()["mastery_verified"]
    assert notebook.public()["can_continue"]


@pytest.mark.parametrize("reason", ["hint", "demonstrate", "incorrect", "uncertain", "partial", "repeated"])
def test_transfer_does_not_pass_without_sufficient_unassisted_evidence(reason):
    second = "独立创建文本文件" if reason == "repeated" else "迁移到新场景"
    notebook = plan(step("independent", "独立创建文本文件"), step("transfer", second))
    notebook.submit("第一题解释")
    review(notebook)
    notebook.action("next")
    if reason in {"hint", "demonstrate"}: notebook.action(reason)
    notebook.submit("第二题解释")
    overrides = {"correctness": reason} if reason in {"incorrect", "uncertain"} else {"reasoning_quality": "partial"} if reason == "partial" else {}
    review(notebook, **overrides)
    assert not notebook.public()["mastery_verified"]
    assert not notebook.public()["can_continue"]


def test_skipping_is_preserved_and_a_new_independent_practice_can_recover():
    notebook = plan(step("independent", "旧题"), step("independent", "新的独立题"), step("transfer", "新情境"))
    notebook.action("skip")
    notebook.submit("新题解释")
    review(notebook)
    notebook.action("next")
    notebook.submit("迁移解释")
    review(notebook)
    assert notebook.public()["steps"][0]["status"] == "skipped"
    assert notebook.public()["mastery_verified"]


def test_explanation_can_be_acknowledged_but_is_not_practice_evidence():
    notebook = plan(step("explain"))
    notebook.action("next")
    assert notebook.public()["attempts"] == []
    assert notebook.public()["steps"][0]["status"] == "discussed"
    assert not notebook.public()["mastery_verified"]


def test_plan_append_preserves_evidence_and_cannot_replace_it():
    notebook = plan()
    notebook.action("hint")
    with pytest.raises(ValueError, match="覆盖"):
        notebook.plan({"goal": "new", "steps": [step()]})
    notebook.plan({"append": True, "steps": [step("independent", "新任务")]})
    assert len(notebook.public()["steps"]) == 2
    assert notebook.public()["steps"][0]["hint_level"] == 1


def test_pending_submission_roundtrip_is_private_in_public_state():
    notebook = plan()
    notebook.submit("这是一段只应随作答上下文传给导师的原文")
    restored = GuidanceNotebook(notebook.export())
    assert restored.context()["submission"]["text"].startswith("这是一段")
    assert "这是一段" not in str(restored.public())
    review(restored)


@pytest.mark.parametrize("field,value", [("current", -1), ("current", True), ("mode", "fake"), ("steps", "bad"), ("attempts", [None])])
def test_corrupt_records_are_rejected(field, value):
    state = plan().export()
    state[field] = value
    with pytest.raises((ValueError, TypeError)):
        GuidanceNotebook(state)


def test_concept_step_with_pending_answer_cannot_skip_review_using_next():
    notebook = plan(step("explain"))
    notebook.submit("我的理解还在等待审阅。")
    assert not notebook.public()["can_continue"]
    with pytest.raises(ValueError, match="审阅"):
        notebook.action("next")
    assert notebook.public()["current"] == 0
    assert notebook.public()["pending_review"]
