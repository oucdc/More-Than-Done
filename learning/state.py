"""Domain types for a More Than Done learning session."""

from dataclasses import dataclass
from enum import Enum

from learning.evidence import PracticeAttempt


class LearningStage(str, Enum):
    GOAL_CLARIFICATION = "goal_clarification"
    BASELINE_ASSESSMENT = "baseline_assessment"
    CONCEPT_EXPLANATION = "concept_explanation"
    GUIDED_PRACTICE = "guided_practice"
    FEEDBACK = "feedback"
    INDEPENDENT_PRACTICE = "independent_practice"
    MASTERY_CHECK = "mastery_check"
    SUMMARY = "summary"
    COMPLETED = "completed"
    EXITED = "exited"


class LearningEvent(str, Enum):
    GOAL_CONFIRMED = "goal_confirmed"
    BASELINE_COMPLETED = "baseline_completed"
    EXPLANATION_COMPLETED = "explanation_completed"
    PRACTICE_ANSWER_SUBMITTED = "practice_answer_submitted"
    MASTERY_ANSWER_SUBMITTED = "mastery_answer_submitted"
    FEEDBACK_CONTINUE_GUIDED = "feedback_continue_guided"
    FEEDBACK_READY_FOR_INDEPENDENT = "feedback_ready_for_independent"
    FEEDBACK_READY_FOR_MASTERY = "feedback_ready_for_mastery"
    MASTERY_PASSED = "mastery_passed"
    MASTERY_NOT_PASSED = "mastery_not_passed"
    SUMMARY_COMPLETED = "summary_completed"
    EXPLANATION_REQUESTED = "explanation_requested"
    PRACTICE_REQUESTED = "practice_requested"
    SUMMARY_REQUESTED = "summary_requested"
    HINT_REQUESTED = "hint_requested"
    ANSWER_REQUESTED = "answer_requested"
    SKIP_STAGE = "skip_stage"
    EXIT_SESSION = "exit_session"


STAGE_LABELS: dict[LearningStage, str] = {
    LearningStage.GOAL_CLARIFICATION: "目标确认",
    LearningStage.BASELINE_ASSESSMENT: "基础诊断",
    LearningStage.CONCEPT_EXPLANATION: "概念讲解",
    LearningStage.GUIDED_PRACTICE: "引导练习",
    LearningStage.FEEDBACK: "练习反馈",
    LearningStage.INDEPENDENT_PRACTICE: "独立练习",
    LearningStage.MASTERY_CHECK: "掌握度检验",
    LearningStage.SUMMARY: "学习总结",
    LearningStage.COMPLETED: "已完成",
    LearningStage.EXITED: "已退出",
}

EVENT_LABELS: dict[LearningEvent, str] = {
    LearningEvent.GOAL_CONFIRMED: "确认学习目标",
    LearningEvent.BASELINE_COMPLETED: "完成基础诊断",
    LearningEvent.EXPLANATION_COMPLETED: "完成概念讲解",
    LearningEvent.PRACTICE_ANSWER_SUBMITTED: "提交练习答案",
    LearningEvent.MASTERY_ANSWER_SUBMITTED: "提交掌握度检验答案",
    LearningEvent.FEEDBACK_CONTINUE_GUIDED: "继续引导练习",
    LearningEvent.FEEDBACK_READY_FOR_INDEPENDENT: "开始独立练习",
    LearningEvent.FEEDBACK_READY_FOR_MASTERY: "开始掌握度检验",
    LearningEvent.MASTERY_PASSED: "通过掌握度检验",
    LearningEvent.MASTERY_NOT_PASSED: "未通过掌握度检验",
    LearningEvent.SUMMARY_COMPLETED: "完成学习总结",
    LearningEvent.EXPLANATION_REQUESTED: "请求讲解",
    LearningEvent.PRACTICE_REQUESTED: "请求练习",
    LearningEvent.SUMMARY_REQUESTED: "请求总结",
    LearningEvent.HINT_REQUESTED: "请求提示",
    LearningEvent.ANSWER_REQUESTED: "请求答案",
    LearningEvent.SKIP_STAGE: "跳过当前阶段",
    LearningEvent.EXIT_SESSION: "退出学习",
}


@dataclass(frozen=True)
class LearningState:
    """Current workflow position plus the interaction evidence needed by it."""

    stage: LearningStage = LearningStage.GOAL_CLARIFICATION
    goal: str | None = None
    hint_level: int = 0
    events: tuple[LearningEvent, ...] = ()
    skipped_stages: tuple[LearningStage, ...] = ()
    answer_viewed_for_current_exercise: bool = False
    mastery_answer_submitted: bool = False
    mastery_exercise_repeated: bool = False
    independent_practice_skipped: bool = False
    active_exercise: str | None = None
    active_success_criteria: str | None = None
    active_reference_answer: str | None = None
    recent_exercises: tuple[str, ...] = ()
    baseline_result: str | None = None
    learner_level: str | None = None
    practice_attempts: tuple[PracticeAttempt, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.stage, LearningStage):
            raise TypeError("stage must be a LearningStage value.")
        if self.goal is not None and not isinstance(self.goal, str):
            raise TypeError("goal must be text or None.")
        if isinstance(self.goal, str):
            object.__setattr__(self, "goal", self.goal.strip() or None)
        if self.active_exercise is not None and not isinstance(self.active_exercise, str):
            raise TypeError("active_exercise must be text or None.")
        if isinstance(self.active_exercise, str):
            object.__setattr__(self, "active_exercise", self.active_exercise.strip() or None)
        for field_name in (
            "active_success_criteria", "active_reference_answer", "baseline_result", "learner_level"
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be text or None.")
            if isinstance(value, str):
                object.__setattr__(self, field_name, value.strip() or None)
        if type(self.hint_level) is not int or self.hint_level not in range(0, 5):
            raise ValueError("hint_level must be an integer from 0 to 4.")

        events = tuple(self.events)
        if any(not isinstance(event, LearningEvent) for event in events):
            raise TypeError("events must contain LearningEvent values.")
        object.__setattr__(self, "events", events)

        skipped = tuple(self.skipped_stages)
        if any(not isinstance(stage, LearningStage) for stage in skipped):
            raise TypeError("skipped_stages must contain LearningStage values.")
        object.__setattr__(self, "skipped_stages", skipped)

        for field_name in (
            "answer_viewed_for_current_exercise", "mastery_answer_submitted",
            "mastery_exercise_repeated", "independent_practice_skipped",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be a bool.")

        recent_exercises = tuple(self.recent_exercises)
        if len(recent_exercises) > 10:
            raise ValueError("recent_exercises must contain at most 10 items.")
        if any(not isinstance(exercise, str) for exercise in recent_exercises):
            raise TypeError("recent_exercises must contain text values.")
        object.__setattr__(self, "recent_exercises", recent_exercises)

        attempts = tuple(self.practice_attempts)
        if any(not isinstance(attempt, PracticeAttempt) for attempt in attempts):
            raise TypeError("practice_attempts must contain PracticeAttempt values.")
        object.__setattr__(self, "practice_attempts", attempts)

    @property
    def is_terminal(self) -> bool:
        return self.stage in {LearningStage.COMPLETED, LearningStage.EXITED}

    @property
    def answer_was_requested(self) -> bool:
        return LearningEvent.ANSWER_REQUESTED in self.events

    @property
    def mastery_was_passed(self) -> bool:
        """Report a recorded evaluator event; this property never evaluates answers."""
        return LearningEvent.MASTERY_PASSED in self.events

    def to_dict(self) -> dict[str, object]:
        """Serialize this state into JSON-compatible primitive values."""
        return {
            "stage": self.stage.value,
            "goal": self.goal,
            "hint_level": self.hint_level,
            "events": [event.value for event in self.events],
            "skipped_stages": [stage.value for stage in self.skipped_stages],
            "answer_viewed_for_current_exercise": self.answer_viewed_for_current_exercise,
            "mastery_answer_submitted": self.mastery_answer_submitted,
            "mastery_exercise_repeated": self.mastery_exercise_repeated,
            "independent_practice_skipped": self.independent_practice_skipped,
            "active_exercise": self.active_exercise,
            "active_success_criteria": self.active_success_criteria,
            "active_reference_answer": self.active_reference_answer,
            "recent_exercises": list(self.recent_exercises),
            "baseline_result": self.baseline_result,
            "learner_level": self.learner_level,
            "practice_attempts": [attempt.to_dict() for attempt in self.practice_attempts],
        }

    @classmethod
    def from_dict(cls, payload: object) -> "LearningState":
        """Restore a state from a complete, recognized JSON object."""
        if not isinstance(payload, dict):
            raise ValueError("Learning state must be a JSON object.")

        required_fields = {
            "stage",
            "goal",
            "hint_level",
            "events",
            "skipped_stages",
            "answer_viewed_for_current_exercise",
            "mastery_answer_submitted",
            "mastery_exercise_repeated",
            "independent_practice_skipped",
            "active_exercise",
            "active_success_criteria",
            "active_reference_answer",
            "recent_exercises",
            "baseline_result",
            "learner_level",
            "practice_attempts",
        }
        missing_fields = required_fields - payload.keys()
        unknown_fields = payload.keys() - required_fields
        if missing_fields:
            raise ValueError("Learning state is missing required fields.")
        if unknown_fields:
            raise ValueError("Learning state contains unsupported fields.")
        if not isinstance(payload["events"], list):
            raise ValueError("Learning state events must be a JSON array.")
        if not isinstance(payload["skipped_stages"], list):
            raise ValueError("Learning state skipped_stages must be a JSON array.")
        if not isinstance(payload["practice_attempts"], list):
            raise ValueError("Learning state practice_attempts must be a JSON array.")
        if not isinstance(payload["recent_exercises"], list):
            raise ValueError("Learning state recent_exercises must be a JSON array.")

        try:
            stage = LearningStage(payload["stage"])
            events = tuple(LearningEvent(value) for value in payload["events"])
            skipped_stages = tuple(LearningStage(value) for value in payload["skipped_stages"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Learning state contains an unknown stage or event.") from exc

        return cls(
            stage=stage,
            goal=payload["goal"],
            hint_level=payload["hint_level"],
            events=events,
            skipped_stages=skipped_stages,
            answer_viewed_for_current_exercise=payload["answer_viewed_for_current_exercise"],
            mastery_answer_submitted=payload["mastery_answer_submitted"],
            mastery_exercise_repeated=payload["mastery_exercise_repeated"],
            independent_practice_skipped=payload["independent_practice_skipped"],
            active_exercise=payload["active_exercise"],
            active_success_criteria=payload["active_success_criteria"],
            active_reference_answer=payload["active_reference_answer"],
            recent_exercises=tuple(payload["recent_exercises"]),
            baseline_result=payload["baseline_result"],
            learner_level=payload["learner_level"],
            practice_attempts=tuple(
                PracticeAttempt.from_dict(value) for value in payload["practice_attempts"]
            ),
        )
