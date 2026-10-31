"""Per-conversation learning guidance and Python-owned progress rules."""

from __future__ import annotations

from copy import deepcopy
from functools import wraps
import re
import threading
from typing import Any
import uuid

from learning.evidence import AnswerAssessment, AnswerCorrectness, PracticeAttempt, PracticeKind, ReasoningQuality
from learning.progress import mastery_pass_eligibility


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


class GuidanceNotebook:
    """The model supplies lessons/reviews; learner actions own help and advancement."""

    def __init__(self, state=None):
        self.lock = threading.RLock()
        self.state = self.empty()
        if state is not None:
            self.restore(state)

    @staticmethod
    def empty():
        return {"schema_version": 1, "goal": "", "steps": [], "current": 0, "mode": "guided", "attempts": [], "submission": None, "observations": [], "last_feedback": None}

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
        if not isinstance(state.get("attempts"), list) or len(state["attempts"]) > 200 or not isinstance(state.get("observations"), list):
            raise ValueError("教学证据格式无效。")
        for item in state["attempts"]:
            if not isinstance(item, dict): raise ValueError("练习记录无效。")
            PracticeAttempt.from_dict(item.get("practice"))
        submission = state.get("submission")
        if submission is not None and (not isinstance(submission, dict) or not all(isinstance(submission.get(k), str) for k in ("id", "step_id", "text"))):
            raise ValueError("待审阅作答格式无效。")
        if type(state.get("retry_requested", False)) is not bool:
            raise ValueError("重新练习标记无效。")
        self.state = deepcopy(state)
        self.state.setdefault("submission", None)
        self.state.setdefault("retry_requested", False)

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
        recovering = self.state.get("retry_requested", False)
        if recovering:
            if not self.public()["can_retry"]:
                raise ValueError("当前检验状态已改变，请先审阅当前作答。")
            if [step["kind"] for step in steps] != ["independent", "transfer"]:
                raise ValueError("重新检验需要一道新的独立练习和一道新的迁移题。")
            normalize = lambda value: re.sub(r"\s+", "", value).casefold()
            tasks = [normalize(step["task"]) for step in self.state["steps"] + steps]
            old_tasks = set(tasks[:-2])
            if tasks[-2] in old_tasks or tasks[-1] in old_tasks or tasks[-1] == tasks[-2]:
                raise ValueError("重新练习需要新任务，不能复用原题。")
        self.state["goal"] = goal
        if recovering:
            # Preserve the failed step and all later planned work; insert the new
            # practice pair immediately after it, rather than stranding the user.
            self.current["status"] = "skipped"
            self.state["current"] += 1
            self.state["steps"][self.state["current"]:self.state["current"]] = steps
            self.state["last_feedback"] = None
            self.state["retry_requested"] = False
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
    def submit(self, answer):
        if self.current is None: raise ValueError("请先让导师制定学习计划。")
        if self.state["submission"] is not None: raise ValueError("上一次尝试尚未审阅，可先请导师检查或明确跳过。")
        submission = {"id": uuid.uuid4().hex, "step_id": self.current["id"], "text": text(answer, "你的尝试", 20000), "hint_level": self.current["hint_level"], "answer_viewed": self.current["answer_viewed"]}
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
        record = {"id": submission["id"], "step_id": step["id"], "practice": practice.to_dict(), "feedback": assessment.feedback, "evidence": assessment.evidence, "mastery_passed": False}
        attempts = [PracticeAttempt.from_dict(item["practice"]) for item in self.state["attempts"]] + [practice]
        can_continue = practice.shows_sound_work
        if practice_kind is PracticeKind.MASTERY:
            normalize = lambda value: re.sub(r"\s+", "", value).casefold()
            repeated = any(normalize(old["task"]) == normalize(step["task"]) for old in self.state["steps"][:self.state["current"]])
            preceding = [item for item in self.state["steps"][:self.state["current"]] if item["kind"] == "independent"]
            passed, reason = mastery_pass_eligibility(tuple(attempts), answer_viewed_for_current_exercise=practice.answer_viewed, independent_practice_skipped=bool(preceding and preceding[-1]["status"] == "skipped"), mastery_exercise_repeated=repeated)
            record["mastery_passed"] = passed
            record["mastery_reason"] = reason
            can_continue = passed
        self.state["attempts"] = (self.state["attempts"] + [record])[-200:]
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
        data["pending_review"] = submission is not None
        data["current_step"] = deepcopy(self.current)
        data["completed"] = bool(data["steps"]) and self.current is None
        data["kind_labels"] = KINDS
        data["can_continue"] = bool(submission is None and self.current and (self.current["kind"] == "explain" or self.current["status"] == "reviewed"))
        data["retry_requested"] = self.state.get("retry_requested", False)
        data["can_retry"] = bool(submission is None and self.current and self.current["kind"] == "transfer" and self.current["status"] == "needs_revision")
        data["mastery_verified"] = any(item.get("mastery_passed") is True for item in data["attempts"])
        data["independent_successes"] = sum(PracticeAttempt.from_dict(item["practice"]).is_unassisted_independent_success for item in data["attempts"])
        return data

    @synchronized
    def export(self):
        return deepcopy(self.state)

    @synchronized
    def context(self):
        return {"current": self.state["current"], "total_steps": len(self.state["steps"]), "can_continue": self.public()["can_continue"], "retry_requested": self.state.get("retry_requested", False), "goal": self.state["goal"], "mode": self.state["mode"], "current_step": deepcopy(self.current), "submission": deepcopy(self.state["submission"]), "recent_attempts": deepcopy(self.state["attempts"][-5:]), "observations": deepcopy(self.state["observations"][-5:]), "completed": bool(self.state["steps"]) and self.current is None}
