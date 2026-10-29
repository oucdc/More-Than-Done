"""Validated input and output shapes at the tutoring-model boundary."""

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any

from learning.evidence import AnswerAssessment, AnswerCorrectness, ReasoningQuality


class TutorIntent(str, Enum):
    """What Python is asking the model to do for one turn."""

    GOAL_CLARIFICATION = "goal_clarification"
    BASELINE_ASSESSMENT = "baseline_assessment"
    CONCEPT_EXPLANATION = "concept_explanation"
    GUIDED_PRACTICE = "guided_practice"
    HINT = "hint"
    FEEDBACK = "feedback"
    INDEPENDENT_PRACTICE = "independent_practice"
    MASTERY_CHECK = "mastery_check"
    SUMMARY = "summary"
    ANSWER = "answer"


@dataclass(frozen=True)
class TutorRequest:
    """Context Python supplies to the model; it contains no workflow authority."""

    intent: TutorIntent
    goal: str | None = None
    learner_level: str | None = None
    concept: str | None = None
    exercise: str | None = None
    learner_answer: str | None = None
    hint_level: int | None = None
    recent_exercises: tuple[str, ...] = ()
    recent_attempts: tuple[str, ...] = ()
    reference_answer: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, TutorIntent):
            raise TypeError("intent must be a TutorIntent value.")

        for field_name in (
            "goal",
            "learner_level",
            "concept",
            "exercise",
            "learner_answer",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be text or None.")
            if isinstance(value, str):
                object.__setattr__(self, field_name, value.strip() or None)

        if not isinstance(self.recent_exercises, tuple):
            raise TypeError("recent_exercises must be a tuple of text values.")
        if len(self.recent_exercises) > 10:
            raise ValueError("recent_exercises must contain at most 10 items.")
        normalized_recent: list[str] = []
        for exercise in self.recent_exercises:
            if not isinstance(exercise, str):
                raise TypeError("recent_exercises must contain text values.")
            normalized_recent.append(exercise.strip())
        object.__setattr__(self, "recent_exercises", tuple(normalized_recent))
        if not isinstance(self.recent_attempts, tuple):
            raise TypeError("recent_attempts must be a tuple of text values.")
        if len(self.recent_attempts) > 5:
            raise ValueError("recent_attempts must contain at most 5 items.")
        normalized_attempts: list[str] = []
        for attempt in self.recent_attempts:
            if not isinstance(attempt, str):
                raise TypeError("recent_attempts must contain text values.")
            normalized_attempts.append(attempt.strip())
        object.__setattr__(self, "recent_attempts", tuple(normalized_attempts))
        if self.reference_answer is not None and self.intent is not TutorIntent.ANSWER:
            raise ValueError("reference_answer is only valid for the answer intent.")
        if self.reference_answer is not None and not isinstance(self.reference_answer, str):
            raise TypeError("reference_answer must be text or None.")
        if isinstance(self.reference_answer, str):
            object.__setattr__(self, "reference_answer", self.reference_answer.strip() or None)

        if self.intent is not TutorIntent.GOAL_CLARIFICATION and self.goal is None:
            raise ValueError("goal is required for this tutoring intent.")

        exercise_intents = {
            TutorIntent.GUIDED_PRACTICE,
            TutorIntent.HINT,
            TutorIntent.FEEDBACK,
            TutorIntent.ANSWER,
        }
        if self.intent in exercise_intents and self.exercise is None:
            raise ValueError(f"exercise is required for {self.intent.value}.")

        if self.intent is TutorIntent.FEEDBACK and self.learner_answer is None:
            raise ValueError("learner_answer is required for feedback.")

        if self.intent is TutorIntent.HINT:
            if type(self.hint_level) is not int or self.hint_level not in range(1, 5):
                raise ValueError("hint_level must be an integer from 1 to 4 for a hint.")
        elif self.hint_level is not None:
            raise ValueError("hint_level is only valid for the hint intent.")

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-ready payload for the model prompt."""
        return {
            "intent": self.intent.value,
            "goal": self.goal,
            "learner_level": self.learner_level,
            "concept": self.concept,
            "exercise": self.exercise,
            "learner_answer": self.learner_answer,
            "hint_level": self.hint_level,
            "recent_exercises": list(self.recent_exercises),
            "recent_attempts": list(self.recent_attempts),
            "reference_answer": self.reference_answer,
        }


class TutorResponseError(ValueError):
    """The model's JSON text does not match the application response contract."""


@dataclass(frozen=True)
class TutorResponse:
    """Learner-facing text only; it cannot set a stage or mastery result."""

    message: str
    follow_up_question: str | None = None
    success_criteria: str | None = None
    reference_answer: str | None = None

    @classmethod
    def from_json(cls, raw: str) -> "TutorResponse":
        """Parse and validate the JSON object returned by the model."""
        if not isinstance(raw, str):
            raise TutorResponseError("模型回复必须是 JSON 文本。")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TutorResponseError("模型回复不是有效 JSON。") from exc

        if not isinstance(payload, dict):
            raise TutorResponseError("模型回复必须是 JSON 对象。")

        allowed_keys = {"message", "follow_up_question", "success_criteria", "reference_answer"}
        unknown_keys = set(payload) - allowed_keys
        if unknown_keys:
            raise TutorResponseError("模型回复包含不支持的字段。")
        if "message" not in payload:
            raise TutorResponseError("模型回复缺少 message 字段。")

        message = payload["message"]
        if not isinstance(message, str) or not message.strip():
            raise TutorResponseError("模型回复的 message 必须是非空文本。")

        question = payload.get("follow_up_question")
        if question is not None and not isinstance(question, str):
            raise TutorResponseError("follow_up_question 必须是文本或 null。")
        if isinstance(question, str):
            question = question.strip() or None

        extra_fields: dict[str, str | None] = {}
        for field_name in ("success_criteria", "reference_answer"):
            value = payload.get(field_name)
            if value is not None and not isinstance(value, str):
                raise TutorResponseError(f"{field_name} 必须是文本或 null。")
            extra_fields[field_name] = value.strip() or None if isinstance(value, str) else None

        return cls(
            message=message.strip(),
            follow_up_question=question,
            success_criteria=extra_fields["success_criteria"],
            reference_answer=extra_fields["reference_answer"],
        )


@dataclass(frozen=True)
class AssessmentRequest:
    """The exercise and learner response sent for evidence-based review."""

    goal: str
    exercise: str
    learner_answer: str
    success_criteria: str | None = None
    reference_answer: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("goal", "exercise", "learner_answer"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty text.")
            object.__setattr__(self, field_name, value.strip())
        for field_name in ("success_criteria", "reference_answer"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field_name} must be text or None.")
            if isinstance(value, str):
                object.__setattr__(self, field_name, value.strip() or None)

    def to_payload(self) -> dict[str, str | None]:
        return {
            "goal": self.goal,
            "exercise": self.exercise,
            "learner_answer": self.learner_answer,
            "success_criteria": self.success_criteria,
            "reference_answer": self.reference_answer,
        }


class AssessmentResponseError(ValueError):
    """The assessment JSON did not match the narrow review contract."""


@dataclass(frozen=True)
class TutorAssessment:
    """Model review evidence; Python combines it with practice behavior."""

    assessment: AnswerAssessment

    @classmethod
    def from_json(cls, raw: str) -> "TutorAssessment":
        if not isinstance(raw, str):
            raise AssessmentResponseError("模型评价必须是 JSON 文本。")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AssessmentResponseError("模型评价不是有效 JSON。") from exc
        if not isinstance(payload, dict):
            raise AssessmentResponseError("模型评价必须是 JSON 对象。")
        expected_keys = {"correctness", "reasoning_quality", "evidence", "feedback"}
        if payload.keys() != expected_keys:
            raise AssessmentResponseError("模型评价缺少必需字段或包含不支持的字段。")
        if not isinstance(payload["evidence"], str) or not payload["evidence"].strip():
            raise AssessmentResponseError("模型评价的 evidence 必须是非空文本。")
        if not isinstance(payload["feedback"], str) or not payload["feedback"].strip():
            raise AssessmentResponseError("模型评价的 feedback 必须是非空文本。")
        try:
            assessment = AnswerAssessment(
                correctness=AnswerCorrectness(payload["correctness"]),
                reasoning_quality=ReasoningQuality(payload["reasoning_quality"]),
                evidence=payload["evidence"],
                feedback=payload["feedback"],
            )
        except (TypeError, ValueError) as exc:
            raise AssessmentResponseError("模型评价包含未知的正确性或推理类别。") from exc
        return cls(assessment=assessment)
