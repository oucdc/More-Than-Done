"""Per-conversation learning guidance and Python-owned progress rules."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import re
import threading
from typing import Any
import uuid

from learning.evidence import AnswerAssessment, AnswerCorrectness, PracticeAttempt, PracticeKind, ReasoningQuality
from learning.progress import mastery_pass_eligibility
from learning.portfolio import skill_identifier


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return wrapped


def text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label}需要是 1–{limit} 个字符。")
    return value.strip()


KINDS = {"explain": "理解概念", "guided": "引导尝试", "independent": "独立练习", "transfer": "迁移检验"}


def task_identity(task):
    return re.sub(r"\s+", "", task).casefold()


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def artifact_snapshots(artifacts):
    """Validate server-captured text snapshots before persisting a submission."""
    if artifacts is None:
        return []
    if not isinstance(artifacts, list) or len(artifacts) > 5:
        raise ValueError("每次最多提交 5 个作品文件。")
    snapshots = []
    for item in artifacts:
        if not isinstance(item, dict):
            raise ValueError("作品快照格式无效。")
        path = text(item.get("path"), "作品路径", 4096)
        digest = item.get("hash", item.get("sha256"))
        if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise ValueError("作品快照缺少有效的 SHA-256 指纹。")
        content = item.get("content")
        if not isinstance(content, str) or len(content) > 200_000:
            raise ValueError("作品快照需要是不超过 200000 字符的文本。")
        size = item.get("size")
        if type(size) is not int or not 0 <= size <= 2_000_000:
            raise ValueError("作品大小无效。")
        encoded = content.encode("utf-8")
        if size != len(encoded) or digest.lower() != hashlib.sha256(encoded).hexdigest():
            raise ValueError("作品内容与保存的大小或 SHA-256 指纹不一致。")
        checks = item.get("checks", [])
        if not isinstance(checks, list) or len(checks) > 20:
            raise ValueError("作品检查记录无效。")
        for check in checks:
            if not isinstance(check, dict) or type(check.get("passed")) is not bool:
                raise ValueError("作品检查结果无效。")
            text(check.get("name", check.get("type")), "检查名称", 200)
            if "detail" in check:
                text(check["detail"], "检查说明", 2000)
        snapshots.append({"path": path, "hash": digest.lower(), "content": content,
                          "size": size, "checks": deepcopy(checks)})
    return snapshots


def evidence_summary(record):
    """Keep readable evidence while full source snapshots stay in durable records."""
    summary = deepcopy(record)
    summary["artifacts"] = [
        {key: value for key, value in artifact.items() if key != "content"}
        for artifact in summary.get("artifacts", [])
    ]
    return summary


class GuidanceNotebook:
    """The model supplies lessons/reviews; learner actions own help and advancement."""

    def __init__(self, state=None):
        self.lock = threading.RLock()
        self.state = self.empty()
        if state is not None:
            self.restore(state)

    @staticmethod
    def empty():
        return {"schema_version": 1, "goal": "", "steps": [], "current": 0, "mode": "guided", "attempts": [], "submission": None, "observations": [], "last_feedback": None, "skill_id": "", "prior_tasks": [], "retry_requested": False, "pending_prediction": None}

    @synchronized
    def restore(self, state):
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("教学记录版本不受支持。")
        if not isinstance(state.get("goal"), str) or not isinstance(state.get("steps"), list) or len(state["steps"]) > 30:
            raise ValueError("教学计划格式无效。")
        if type(state.get("current")) is not int or not 0 <= state["current"] <= len(state["steps"]):
            raise ValueError("教学步骤位置无效。")
        if state.get("mode") not in {"guided", "independent"}:
            raise ValueError("教学模式无效。")
        for step in state["steps"]:
            if not isinstance(step, dict) or step.get("kind") not in KINDS or step.get("status") not in {"pending", "submitted", "reviewed", "needs_revision", "completed", "discussed", "skipped"}:
                raise ValueError("教学步骤字段无效。")
            for key in ("id", "title", "task", "success_criteria"):
                text(step.get(key), key)
            if type(step.get("hint_level")) is not int or step["hint_level"] not in range(5) or type(step.get("answer_viewed")) is not bool:
                raise ValueError("教学帮助记录无效。")
        if not isinstance(state.get("attempts"), list) or not isinstance(state.get("observations"), list):
            raise ValueError("教学证据格式无效。")
        for item in state["attempts"]:
            if not isinstance(item, dict): raise ValueError("练习记录无效。")
            PracticeAttempt.from_dict(item.get("practice"))
            for flag in ("fresh_task", "mastery_passed", "delayed_verified"):
                if flag in item and type(item[flag]) is not bool:
                    raise ValueError("能力证据标记无效。")
        submission = state.get("submission")
        if submission is not None and (not isinstance(submission, dict) or not all(isinstance(submission.get(k), str) for k in ("id", "step_id", "text"))):
            raise ValueError("待审阅作答格式无效。")
        if type(state.get("retry_requested", False)) is not bool:
            raise ValueError("重新练习标记无效。")
        skill_id = state.get("skill_id") or (skill_identifier(state["goal"]) if state["goal"] else "")
        if not isinstance(skill_id, str) or len(skill_id) > 100:
            raise ValueError("能力标记无效。")
        prior_tasks = state.get("prior_tasks", [])
        if not isinstance(prior_tasks, list) or any(not isinstance(task, str) for task in prior_tasks):
            raise ValueError("先前练习记录无效。")
        if state.get("challenge_due_at") is not None:
            try:
                due = datetime.fromisoformat(state["challenge_due_at"])
            except (TypeError, ValueError):
                raise ValueError("复验时间无效。") from None
            if due.tzinfo is None:
                raise ValueError("复验时间需要包含时区。")
        for record in [*state["attempts"], *([submission] if submission else [])]:
            if "artifacts" in record:
                artifact_snapshots(record["artifacts"])
            if record.get("confidence") is not None and (type(record["confidence"]) is not int or not 0 <= record["confidence"] <= 100):
                raise ValueError("信心程度需要是 0 到 100 的整数。")
        prediction = state.get("pending_prediction")
        if prediction is not None:
            if not isinstance(prediction, dict) or state["current"] == len(state["steps"]):
                raise ValueError("预先预测记录无效。")
            if prediction.get("step_id") != state["steps"][state["current"]]["id"]:
                raise ValueError("预先预测与当前步骤不匹配。")
            text(prediction.get("text"), "预先预测")
            confidence = prediction.get("confidence")
            if confidence is not None and (type(confidence) is not int or not 0 <= confidence <= 100):
                raise ValueError("信心程度需要是 0 到 100 的整数。")
            try:
                recorded = datetime.fromisoformat(prediction.get("recorded_at"))
            except (TypeError, ValueError):
                raise ValueError("预先预测时间无效。") from None
            if recorded.tzinfo is None:
                raise ValueError("预先预测时间需要包含时区。")
        self.state = deepcopy(state)
        self.state.setdefault("submission", None)
        self.state.setdefault("retry_requested", False)
        self.state["skill_id"] = skill_id
        self.state.setdefault("prior_tasks", [])
        self.state.setdefault("pending_prediction", None)

    @synchronized
    def prepare_challenge(self, goal, skill_id, prior_tasks, *, available_after):
        """Initialize a fresh conversation with the evidence identity of a due skill."""
        if self.state["steps"] or self.state["attempts"] or self.state["submission"]:
            raise ValueError("独立复验需要一段新的学习对话。")
        goal = text(goal, "学习目标", 500)
        skill_id = text(skill_id, "能力标记", 100)
        if not isinstance(prior_tasks, list):
            raise ValueError("先前练习记录无效。")
        tasks = [text(task, "先前任务") for task in prior_tasks]
        try:
            due = datetime.fromisoformat(available_after)
        except (TypeError, ValueError):
            raise ValueError("复验时间无效。") from None
        if due.tzinfo is None:
            raise ValueError("复验时间需要包含时区。")
        if datetime.fromisoformat(timestamp()) < due:
            raise ValueError("这项能力尚未到复验时间。")
        self.state.update(goal=goal, skill_id=skill_id, prior_tasks=tasks,
                          challenge_due_at=due.isoformat(timespec="seconds"))
        return self.public()

    @property
    def current(self):
        index = self.state["current"]
        return self.state["steps"][index] if index < len(self.state["steps"]) else None

    @synchronized
    def plan(self, arguments):
        append = arguments.get("append", False)
        if type(append) is not bool: raise ValueError("append 必须是布尔值。")
        if self.state["steps"] and not append:
            raise ValueError("已有教学计划不能覆盖。可追加新练习，或由用户开始新对话。")
        goal = text(arguments.get("goal") or self.state["goal"], "学习目标", 500)
        if self.state["goal"] and goal != self.state["goal"]:
            raise ValueError("新学习目标请开启新对话，避免混用已有学习证据。")
        raw_steps = arguments.get("steps")
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 12 or len(raw_steps) + len(self.state["steps"]) > 30:
            raise ValueError("每次安排 1–12 个小步骤，一个学习计划最多 30 步。")
        steps = []
        existing = len(self.state["steps"])
        for index, raw in enumerate(raw_steps):
            if not isinstance(raw, dict) or raw.get("kind") not in KINDS: raise ValueError("教学步骤类型无效。")
            steps.append({"id": f"step-{existing+index+1}", "title": text(raw.get("title"), "步骤标题", 100), "task": text(raw.get("task"), "本步任务"), "success_criteria": text(raw.get("success_criteria"), "完成标准"), "kind": raw["kind"], "status": "pending", "hint_level": 0, "answer_viewed": False})
        seen_tasks = {task_identity(step["task"]) for step in self.state["steps"]}
        seen_tasks.update(task_identity(task) for task in self.state.get("prior_tasks", []))
        for step in steps:
            identity = task_identity(step["task"])
            if step["kind"] in {"independent", "transfer"} and identity in seen_tasks:
                raise ValueError("独立练习和迁移检验需要新任务，不能复用已有题目。")
            seen_tasks.add(identity)
        recovering = self.state.get("retry_requested", False)
        if recovering:
            if not self.public()["can_retry"]:
                raise ValueError("当前检验状态已改变，请先审阅当前作答。")
            if [step["kind"] for step in steps] != ["independent", "transfer"]:
                raise ValueError("重新检验需要一道新的独立练习和一道新的迁移题。")
        self.state["goal"] = goal
        self.state["skill_id"] = self.state.get("skill_id") or skill_identifier(goal)
        if recovering:
            # Preserve the failed step and all later planned work; insert the new
            # practice pair immediately after it, rather than stranding the user.
            self.current["status"] = "skipped"
            self.state["current"] += 1
            self.state["steps"][self.state["current"]:self.state["current"]] = steps
            self.state["last_feedback"] = None
            self.state["retry_requested"] = False
            self.state["pending_prediction"] = None
        else:
            self.state["steps"].extend(steps)
        if self.current and self.current["kind"] in {"independent", "transfer"}:
            self.state["mode"] = "independent"
        return self.public()

    @synchronized
    def help(self, kind):
        if kind not in {"hint", "explain", "demonstrate"}: raise ValueError("教学帮助类型无效。")
        if self.current:
            if kind == "demonstrate": self.current["answer_viewed"] = True
            else: self.current["hint_level"] = min(4, self.current["hint_level"] + 1)
            self.state["mode"] = "guided"
        return self.public()

    @synchronized
    def record_prediction(self, prediction, confidence=None):
        """Fix a learner's prediction before their first submission for this step."""
        if self.current is None or self.current["status"] != "pending" or self.state["submission"] is not None:
            raise ValueError("请在当前步骤首次提交尝试前记录预测。")
        prediction = text(prediction, "你的预测")
        if confidence is not None and (type(confidence) is not int or not 0 <= confidence <= 100):
            raise ValueError("信心程度需要是 0 到 100 的整数。")
        existing = self.state.get("pending_prediction")
        if existing:
            if existing["text"] != prediction or existing.get("confidence") != confidence:
                raise ValueError("本步预测已记录，请在实际结果中解释差异。")
            return deepcopy(existing)
        recorded = {"step_id": self.current["id"], "text": prediction,
                    "confidence": confidence, "recorded_at": timestamp()}
        self.state["pending_prediction"] = recorded
        return deepcopy(recorded)

    @synchronized
    def submit(self, answer, *, artifacts=None, prediction=None, confidence=None, observed_result=None):
        if self.current is None: raise ValueError("请先让导师制定学习计划。")
        if self.state["submission"] is not None: raise ValueError("上一次尝试尚未审阅，可先请导师检查或明确跳过。")
        if confidence is not None and (type(confidence) is not int or not 0 <= confidence <= 100):
            raise ValueError("信心程度需要是 0 到 100 的整数。")
        snapshots = artifact_snapshots(artifacts)
        prediction = text(prediction, "你的预测") if prediction not in (None, "") else None
        observed_result = text(observed_result, "实际结果") if observed_result not in (None, "") else None
        recorded = self.state.get("pending_prediction")
        if recorded:
            if prediction is not None and prediction != recorded["text"]:
                raise ValueError("提交的预测与先前记录不一致。")
            if confidence is not None and confidence != recorded.get("confidence"):
                raise ValueError("提交的信心程度与先前记录不一致。")
            prediction = recorded["text"]
            confidence = recorded.get("confidence")
        related = [step for step in self.state["steps"][:self.state["current"]]
                   if task_identity(step["task"]) == task_identity(self.current["task"])]
        hint_level = max([self.current["hint_level"], *[step["hint_level"] for step in related]])
        answer_viewed = self.current["answer_viewed"] or any(step["answer_viewed"] for step in related)
        submission = {"id": uuid.uuid4().hex, "step_id": self.current["id"],
                      "text": text(answer, "你的尝试", 20000), "hint_level": hint_level,
                      "answer_viewed": answer_viewed, "artifacts": snapshots,
                      "prediction": prediction, "confidence": confidence,
                      "prediction_recorded_at": recorded["recorded_at"] if recorded else None,
                      "observed_result": observed_result, "submitted_at": timestamp()}
        self.state["submission"] = submission
        self.state["retry_requested"] = False
        self.current["status"] = "submitted"
        return deepcopy(submission)

    @synchronized
    def review(self, arguments):
        submission = self.state["submission"]
        step = self.current
        if submission is None or step is None or arguments.get("submission_id") != submission["id"] or submission["step_id"] != step["id"]:
            raise ValueError("没有匹配的真实用户作答。导师不能代替用户提交或重复评分。")
        assessment = AnswerAssessment(AnswerCorrectness(arguments.get("correctness")), ReasoningQuality(arguments.get("reasoning_quality")), feedback=text(arguments.get("feedback"), "反馈"), evidence=text(arguments.get("evidence"), "评价依据"))
        practice_kind = {"independent": PracticeKind.INDEPENDENT, "transfer": PracticeKind.MASTERY}.get(step["kind"], PracticeKind.GUIDED)
        practice = PracticeAttempt(practice_kind, assessment.correctness, assessment.reasoning_quality, submission.get("hint_level", step["hint_level"]), submission.get("answer_viewed", step["answer_viewed"]))
        reviewed_at = timestamp()
        repeated = task_identity(step["task"]) in {
            task_identity(old["task"]) for old in self.state["steps"][:self.state["current"]]
        } or task_identity(step["task"]) in {
            task_identity(task) for task in self.state.get("prior_tasks", [])
        }
        record = {"id": submission["id"], "step_id": step["id"], "practice": practice.to_dict(),
                  "feedback": assessment.feedback, "evidence": assessment.evidence,
                  "mastery_passed": False, "fresh_task": not repeated,
                  "skill_id": self.state.get("skill_id"), "task": step["task"],
                  "success_criteria": step["success_criteria"], "explanation": submission["text"],
                  "artifacts": deepcopy(submission.get("artifacts", [])),
                  "prediction": submission.get("prediction"), "confidence": submission.get("confidence"),
                  "prediction_recorded_at": submission.get("prediction_recorded_at"),
                  "observed_result": submission.get("observed_result"),
                  "submitted_at": submission.get("submitted_at"), "reviewed_at": reviewed_at}
        attempts = [PracticeAttempt.from_dict(item["practice"]) for item in self.state["attempts"]] + [practice]
        can_continue = practice.shows_sound_work
        if practice_kind is PracticeKind.MASTERY:
            preceding = [item for item in self.state["steps"][:self.state["current"]] if item["kind"] == "independent"]
            passed, reason = mastery_pass_eligibility(tuple(attempts), answer_viewed_for_current_exercise=practice.answer_viewed, independent_practice_skipped=bool(preceding and preceding[-1]["status"] == "skipped"), mastery_exercise_repeated=repeated)
            if passed and self.state["attempts"][-1].get("fresh_task") is False:
                passed, reason = False, "前一次独立练习重复了旧题，需要新的独立任务。"
            record["mastery_passed"] = passed
            record["mastery_reason"] = reason
            can_continue = passed
            if passed:
                delayed = bool(self.state.get("challenge_due_at"))
                record["delayed_verified"] = delayed
                interval = timedelta(days=7 if delayed else 1)
                record["due_at"] = (datetime.fromisoformat(reviewed_at) + interval).isoformat(timespec="seconds")
        self.state["attempts"].append(record)
        self.state["submission"] = None
        self.state["last_feedback"] = record
        step["status"] = "reviewed" if can_continue else "needs_revision"
        return {"ok": True, "feedback": record, "can_continue": can_continue, "note": "正确性与推理由导师模型审阅；帮助程度和掌握资格由 Python 规则记录。"}

    @synchronized
    def action(self, action):
        if self.current is None: raise ValueError("请先给导师一个学习目标并生成计划。")
        if action in {"hint", "explain", "demonstrate"}: return self.help(action)
        if action == "retry":
            if not self.public()["can_retry"]:
                raise ValueError("当前没有可重新安排的迁移检验。")
            if len(self.state["steps"]) + 2 > 30:
                raise ValueError("本轮计划已达到步骤上限，请开启新对话练习；原记录会保留。")
            self.state["retry_requested"] = True
        elif action == "independent": self.state["mode"] = "independent"
        elif action in {"next", "skip"}:
            step = self.current
            if action == "next" and self.state["submission"] is not None:
                raise ValueError("当前作答尚未审阅，请先完成审阅。")
            if action == "next" and step["kind"] != "explain" and step["status"] != "reviewed":
                raise ValueError("请先提交自己的尝试并完成审阅；也可明确跳过，但不会记为掌握。")
            step["status"] = "skipped" if action == "skip" else ("discussed" if step["kind"] == "explain" else "completed")
            self.state["current"] += 1
            self.state["submission"] = None
            self.state["last_feedback"] = None
            self.state["retry_requested"] = False
            self.state["pending_prediction"] = None
            self.state["mode"] = "independent" if self.current and self.current["kind"] in {"independent", "transfer"} else "guided"
        else: raise ValueError("未知教学操作。")
        return self.public()

    @synchronized
    def observe(self, observation):
        if self.current:
            self.state["observations"] = (self.state["observations"] + [{**observation, "step_id": self.current["id"], "submission_id": self.state["submission"]["id"] if self.state["submission"] else None}])[-20:]

    @synchronized
    def record_agent_execution(self):
        """Agent work during review must never be credited as learner-authored work."""
        self.help("demonstrate")
        if self.state["submission"] is not None:
            self.state["submission"]["answer_viewed"] = True

    @synchronized
    def public(self):
        data = deepcopy(self.state)
        submission = data.pop("submission")
        data["attempts"] = [evidence_summary(record) for record in data["attempts"]]
        if data.get("last_feedback"):
            data["last_feedback"] = evidence_summary(data["last_feedback"])
        data["evidence_cards"] = data["attempts"][-20:]
        data["pending_review"] = submission is not None
        data["current_step"] = deepcopy(self.current)
        data["completed"] = bool(data["steps"]) and self.current is None
        data["kind_labels"] = KINDS
        data["can_continue"] = bool(submission is None and self.current and (self.current["kind"] == "explain" or self.current["status"] == "reviewed"))
        data["retry_requested"] = self.state.get("retry_requested", False)
        data["can_retry"] = bool(submission is None and self.current and self.current["kind"] == "transfer" and self.current["status"] == "needs_revision")
        data["mastery_verified"] = any(item.get("mastery_passed") is True for item in data["attempts"])
        data["independent_successes"] = sum(PracticeAttempt.from_dict(item["practice"]).is_unassisted_independent_success and item.get("fresh_task", True) for item in data["attempts"])
        return data

    @synchronized
    def export(self):
        return deepcopy(self.state)

    @synchronized
    def context(self):
        submission = deepcopy(self.state["submission"])
        if submission:
            remaining = 24_000
            for artifact in submission.get("artifacts", []):
                content = artifact["content"]
                artifact["content"] = content[:remaining]
                artifact["content_truncated"] = len(content) > remaining
                remaining -= len(artifact["content"])
        return {"current": self.state["current"], "total_steps": len(self.state["steps"]),
                "can_continue": self.public()["can_continue"],
                "retry_requested": self.state.get("retry_requested", False),
                "goal": self.state["goal"], "skill_id": self.state.get("skill_id"),
                "prior_tasks": self.state.get("prior_tasks", [])[-30:],
                "mode": self.state["mode"], "current_step": deepcopy(self.current),
                "pending_prediction": deepcopy(self.state.get("pending_prediction")),
                "submission": submission,
                "recent_attempts": [evidence_summary(record) for record in self.state["attempts"][-5:]],
                "observations": deepcopy(self.state["observations"][-5:]),
                "completed": bool(self.state["steps"]) and self.current is None}
