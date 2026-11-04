"""Aggregate durable learning evidence across conversations for a local learner."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import re

from learning.evidence import PracticeAttempt


def skill_identifier(goal: str) -> str:
    """Identical normalized goals retain one identity across new conversations."""
    normalized = re.sub(r"\s+", "", goal).casefold()
    return "skill-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def build_portfolio(document: dict, *, now: datetime | None = None) -> dict:
    """Return UI-safe ability cards and due skills, without raw file snapshots."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("能力复验需要包含时区的当前时间。")
    skills = {}
    artifact_count = 0
    for conversation in document.get("conversations", []):
        lesson = conversation.get("lesson") or {}
        goal = lesson.get("goal", "")
        if not goal:
            continue
        identity = lesson.get("skill_id") or skill_identifier(goal)
        skill = skills.setdefault(identity, {
            "skill_id": identity, "goal": goal, "conversation_ids": [],
            "source_conversation_id": conversation["id"], "prior_tasks": [],
            "attempt_count": 0, "independent_successes": 0, "mastery_verified": False,
            "delayed_verified": False, "last_verified_at": None, "due_at": None,
            "last_feedback": None, "artifacts": [], "hinted_attempts": 0,
        })
        skill["conversation_ids"].append(conversation["id"])
        for step in lesson.get("steps", []):
            task = step.get("task")
            if task and task not in skill["prior_tasks"]:
                skill["prior_tasks"].append(task)
        for task in lesson.get("prior_tasks", []):
            if task not in skill["prior_tasks"]:
                skill["prior_tasks"].append(task)
        for attempt in lesson.get("attempts", []):
            practice = PracticeAttempt.from_dict(attempt["practice"])
            skill["attempt_count"] += 1
            skill["independent_successes"] += int(practice.is_unassisted_independent_success and attempt.get("fresh_task", True))
            skill["hinted_attempts"] += int(practice.hint_level > 0 or practice.answer_viewed)
            reviewed = _time(attempt.get("reviewed_at"))
            previous_feedback = skill["last_feedback"] or {}
            previous_time = _time(previous_feedback.get("reviewed_at"))
            if not previous_feedback or (reviewed and (previous_time is None or reviewed >= previous_time)):
                skill["last_feedback"] = {
                    "id": attempt["id"], "feedback": attempt.get("feedback", ""),
                    "reviewed_at": attempt.get("reviewed_at"), "practice": practice.to_dict(),
                }
            if attempt.get("mastery_passed") is True:
                skill["mastery_verified"] = True
                skill["delayed_verified"] |= attempt.get("delayed_verified") is True
                # Old versions had no attempt timestamp. Use the owning chat's
                # recorded update time for scheduling without altering evidence.
                verified = reviewed or _time(conversation.get("updated_at"))
                previous_verified = _time(skill["last_verified_at"])
                if verified and (previous_verified is None or verified >= previous_verified):
                    skill["last_verified_at"] = verified.isoformat(timespec="seconds")
                    due = _time(attempt.get("due_at")) or (verified + timedelta(days=1))
                    skill["due_at"] = due.isoformat(timespec="seconds")
                    skill["verification_time_inferred"] = reviewed is None
                    skill["source_conversation_id"] = conversation["id"]
            for artifact in attempt.get("artifacts", []):
                artifact_count += 1
                summary = {key: deepcopy(value) for key, value in artifact.items() if key != "content"}
                summary.update(attempt_id=attempt["id"], conversation_id=conversation["id"],
                               reviewed_at=attempt.get("reviewed_at"))
                skill["artifacts"].append(summary)
    result = sorted(skills.values(), key=lambda item: (item["last_verified_at"] or "", item["goal"]), reverse=True)
    for skill in result:
        skill["artifact_count"] = len(skill["artifacts"])
        skill["artifacts"] = skill["artifacts"][-20:]
        due = _time(skill["due_at"])
        skill["challenge_due"] = bool(due and due <= now)
    return {"skills": result, "due_challenges": [deepcopy(skill) for skill in result if skill["challenge_due"]],
            "artifact_count": artifact_count}
