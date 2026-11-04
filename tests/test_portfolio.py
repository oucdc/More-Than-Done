from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from learning.guidance import GuidanceNotebook
from learning.portfolio import build_portfolio, skill_identifier


def steps(label="首次"):
    return [{"title": kind, "task": f"{label}{kind}任务", "success_criteria": "结果和解释满足任务要求", "kind": kind}
            for kind in ("independent", "transfer")]


def review(notebook, **changes):
    payload = {"submission_id": notebook.context()["submission"]["id"], "correctness": "correct",
               "reasoning_quality": "sound", "evidence": "根据用户提交的作品与解释",
               "feedback": "结果符合要求，解释清楚。"}
    payload.update(changes)
    return notebook.review(payload)


def snapshot(content="CSV 名称和内容均已检查"):
    return {"path": "result.txt", "hash": hashlib.sha256(content.encode()).hexdigest(),
            "content": content, "size": len(content.encode()),
            "checks": [{"name": "UTF-8文本", "passed": True, "detail": "已读取提交时内容"}]}


def mastered(monkeypatch, time="2026-11-04T10:00:00+00:00"):
    monkeypatch.setattr("learning.guidance.timestamp", lambda: time)
    notebook = GuidanceNotebook()
    notebook.plan({"goal": "学会检查CSV文件", "steps": steps()})
    notebook.submit("第一题解释", artifacts=[snapshot()], prediction="应显示两列", confidence=70,
                    observed_result="实际显示两列")
    review(notebook)
    notebook.action("next")
    notebook.submit("迁移题解释")
    review(notebook)
    return notebook


def test_submission_snapshot_is_immutable_and_visible_as_an_evidence_card():
    notebook = GuidanceNotebook()
    notebook.plan({"goal": "学习文件检查", "steps": steps()})
    artifact = snapshot()
    submitted = notebook.submit("我先核对列名", artifacts=[artifact], prediction="结果符合预期", confidence=75)
    artifact["content"] = "后来修改的内容"
    submitted["artifacts"][0]["content"] = "返回值也被修改"
    original = notebook.context()["submission"]["artifacts"][0]["content"]
    assert original == "CSV 名称和内容均已检查"
    record = review(notebook)["feedback"]
    assert record["explanation"] == "我先核对列名"
    assert record["prediction"] == "结果符合预期" and record["confidence"] == 75
    assert record["artifacts"][0]["content"] == original
    public = notebook.public()["evidence_cards"][-1]
    assert "content" not in public["artifacts"][0]
    assert public["artifacts"][0]["checks"][0]["passed"]
    assert public["artifacts"][0]["hash"] == snapshot()["hash"]
    assert GuidanceNotebook(notebook.export()).export() == notebook.export()


@pytest.mark.parametrize("extra", [{"confidence": True}, {"confidence": 101}, {"prediction": ["invalid"]},
                                   {"observed_result": False}, {"artifacts": [{"path": "file"}]}])
def test_invalid_submission_metadata_does_not_partly_submit(extra):
    notebook = GuidanceNotebook()
    notebook.plan({"goal": "学习文件检查", "steps": steps()})
    before = notebook.export()
    with pytest.raises(ValueError):
        notebook.submit("我的尝试", **extra)
    assert notebook.export() == before


def test_model_context_has_a_shared_snapshot_budget_but_export_keeps_all_text():
    notebook = GuidanceNotebook()
    notebook.plan({"goal": "学习文件检查", "steps": steps()})
    notebook.submit("请审阅", artifacts=[snapshot("a" * 20000), snapshot("b" * 20000)])
    context = notebook.context()["submission"]["artifacts"]
    assert sum(len(item["content"]) for item in context) == 24000
    assert not context[0]["content_truncated"] and context[1]["content_truncated"]
    assert len(notebook.export()["submission"]["artifacts"][1]["content"]) == 20000


def test_portfolio_aggregates_legacy_and_new_records_without_raw_snapshots(monkeypatch):
    notebook = mastered(monkeypatch)
    current = {"id": "new", "lesson": notebook.export()}
    old = deepcopy(current)
    old["id"] = "old"
    old["lesson"].pop("skill_id")
    old["lesson"]["attempts"] = []
    document = {"conversations": [old, current]}
    before = deepcopy(document)
    portfolio = build_portfolio(document, now=datetime(2026, 11, 5, 10, tzinfo=timezone.utc))
    assert document == before
    assert len(portfolio["skills"]) == 1
    skill = portfolio["skills"][0]
    assert skill["skill_id"] == skill_identifier("学会检查CSV文件")
    assert skill["conversation_ids"] == ["old", "new"]
    assert skill["attempt_count"] == 2 and skill["independent_successes"] == 1
    assert skill["mastery_verified"] and skill["challenge_due"]
    assert skill["source_conversation_id"] == "new"
    assert portfolio["artifact_count"] == 1 and len(portfolio["due_challenges"]) == 1
    assert "content" not in skill["artifacts"][0]


def test_due_challenge_uses_new_tasks_and_schedules_the_next_review(monkeypatch):
    first = mastered(monkeypatch)
    document = {"conversations": [{"id": "first", "lesson": first.export()}]}
    due = datetime(2026, 11, 5, 10, tzinfo=timezone.utc)
    skill = build_portfolio(document, now=due)["due_challenges"][0]
    monkeypatch.setattr("learning.guidance.timestamp", lambda: due.isoformat())
    challenge = GuidanceNotebook()
    challenge.prepare_challenge(skill["goal"], skill["skill_id"], skill["prior_tasks"], available_after=skill["due_at"])
    with pytest.raises(ValueError, match="新任务"):
        challenge.plan({"steps": steps()})
    challenge.plan({"steps": steps("复验")})
    challenge.submit("复验独立作答")
    review(challenge)
    challenge.action("next")
    challenge.submit("复验迁移作答")
    outcome = review(challenge)["feedback"]
    assert outcome["mastery_passed"] and outcome["delayed_verified"]
    assert datetime.fromisoformat(outcome["due_at"]) == due + timedelta(days=7)
    document["conversations"].append({"id": "challenge", "lesson": challenge.export()})
    portfolio = build_portfolio(document, now=due)
    assert len(portfolio["skills"]) == 1 and portfolio["skills"][0]["delayed_verified"]
    assert portfolio["due_challenges"] == []


def test_challenge_cannot_be_initialized_before_its_due_time(monkeypatch):
    monkeypatch.setattr("learning.guidance.timestamp", lambda: "2026-11-04T10:00:00+00:00")
    notebook = GuidanceNotebook()
    before = notebook.export()
    with pytest.raises(ValueError, match="尚未到"):
        notebook.prepare_challenge("练习", skill_identifier("练习"), [], available_after="2026-11-05T10:00:00+00:00")
    assert notebook.export() == before


def test_legacy_mastery_uses_chat_time_for_scheduling_without_rewriting_attempts(monkeypatch):
    notebook = mastered(monkeypatch)
    lesson = notebook.export()
    for attempt in lesson["attempts"]:
        attempt.pop("reviewed_at", None)
        attempt.pop("submitted_at", None)
        attempt.pop("due_at", None)
    original = deepcopy(lesson)
    document = {"conversations": [{"id": "legacy", "updated_at": "2026-11-01T09:00:00+08:00", "lesson": lesson}]}
    skill = build_portfolio(document, now=datetime(2026, 11, 4, tzinfo=timezone.utc))["due_challenges"][0]
    assert skill["last_verified_at"] == "2026-11-01T09:00:00+08:00"
    assert skill["due_at"] == "2026-11-02T09:00:00+08:00"
    assert skill["verification_time_inferred"]
    assert lesson == original


def test_damaged_snapshot_is_rejected_on_submission_and_restore():
    notebook = GuidanceNotebook()
    notebook.plan({"goal": "学习文件检查", "steps": steps()})
    artifact = snapshot()
    artifact["content"] = "损坏的内容"
    with pytest.raises(ValueError, match="指纹"):
        notebook.submit("检查作品", artifacts=[artifact])
    notebook.submit("检查作品", artifacts=[snapshot()])
    state = notebook.export()
    state["submission"]["artifacts"][0]["hash"] = "0" * 64
    with pytest.raises(ValueError, match="指纹"):
        GuidanceNotebook(state)


def test_prediction_is_fixed_before_submission_and_survives_review(monkeypatch):
    notebook = GuidanceNotebook()
    notebook.plan({"goal": "学习文件检查", "steps": steps()})
    monkeypatch.setattr("learning.guidance.timestamp", lambda: "2026-11-04T10:00:00+00:00")
    prediction = notebook.record_prediction("我预计有两列", 75)
    assert notebook.record_prediction("我预计有两列", 75) == prediction
    notebook = GuidanceNotebook(notebook.export())
    before = notebook.export()
    with pytest.raises(ValueError, match="已记录"):
        notebook.record_prediction("我现在预计有三列", 75)
    with pytest.raises(ValueError, match="不一致"):
        notebook.submit("实际有三列", prediction="我现在预计有三列")
    assert notebook.export() == before
    monkeypatch.setattr("learning.guidance.timestamp", lambda: "2026-11-04T10:05:00+00:00")
    notebook.submit("因为表头增加了一列", observed_result="实际有三列")
    card = review(notebook)["feedback"]
    assert card["prediction"] == "我预计有两列" and card["confidence"] == 75
    assert card["prediction_recorded_at"] == "2026-11-04T10:00:00+00:00"
    assert card["submitted_at"] == "2026-11-04T10:05:00+00:00"
    assert card["observed_result"] == "实际有三列"
    notebook.action("next")
    assert notebook.public()["pending_prediction"] is None


def test_submission_only_prediction_is_not_labeled_as_previously_recorded():
    notebook = GuidanceNotebook()
    notebook.plan({"goal": "学习文件检查", "steps": steps()})
    notebook.submit("事后补充的解释", prediction="我当时觉得有两列", confidence=50)
    assert review(notebook)["feedback"]["prediction_recorded_at"] is None


def test_skip_clears_prediction_and_answering_prevents_backdating_one():
    notebook = GuidanceNotebook()
    notebook.plan({"goal": "学习文件检查", "steps": steps()})
    notebook.record_prediction("我预计成功")
    notebook.action("skip")
    assert notebook.public()["pending_prediction"] is None
    notebook.submit("已经知道实际结果")
    with pytest.raises(ValueError, match="首次提交"):
        notebook.record_prediction("补记一个预测")
