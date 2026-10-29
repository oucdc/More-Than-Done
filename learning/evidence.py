"""Structured evidence collected from one learner practice attempt."""

from dataclasses import dataclass
from enum import Enum


class PracticeKind(str, Enum):
    GUIDED = "guided"
    INDEPENDENT = "independent"
    MASTERY = "mastery"


class AnswerCorrectness(str, Enum):
    CORRECT = "correct"
    PARTIALLY_CORRECT = "partially_correct"
    INCORRECT = "incorrect"
    UNCERTAIN = "uncertain"


class ReasoningQuality(str, Enum):
    SOUND = "sound"
    PARTIAL = "partial"
    UNSOUND = "unsound"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AnswerAssessment:
    """Structured correctness and reasoning evidence; it does not decide mastery."""

    correctness: AnswerCorrectness
    reasoning_quality: ReasoningQuality
    feedback: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.correctness, AnswerCorrectness):
            raise TypeError("correctness must be an AnswerCorrectness value.")
        if not isinstance(self.reasoning_quality, ReasoningQuality):
            raise TypeError("reasoning_quality must be a ReasoningQuality value.")
        if not isinstance(self.feedback, str) or not self.feedback.strip():
            raise ValueError("feedback must be non-empty text.")
        if not isinstance(self.evidence, str):
            raise TypeError("evidence must be text.")
        object.__setattr__(self, "feedback", self.feedback.strip())
        object.__setattr__(self, "evidence", self.evidence.strip())


@dataclass(frozen=True)
class PracticeAttempt:
    """Privacy-conscious evidence for one submitted answer (no raw answer text)."""

    kind: PracticeKind
    correctness: AnswerCorrectness
    reasoning_quality: ReasoningQuality
    hint_level: int
    answer_viewed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PracticeKind):
            raise TypeError("kind must be a PracticeKind value.")
        if not isinstance(self.correctness, AnswerCorrectness):
            raise TypeError("correctness must be an AnswerCorrectness value.")
        if not isinstance(self.reasoning_quality, ReasoningQuality):
            raise TypeError("reasoning_quality must be a ReasoningQuality value.")
        if type(self.hint_level) is not int or self.hint_level not in range(5):
            raise ValueError("hint_level must be an integer from 0 to 4.")
        if type(self.answer_viewed) is not bool:
            raise TypeError("answer_viewed must be a bool.")

    @property
    def shows_sound_work(self) -> bool:
        return (
            self.correctness is AnswerCorrectness.CORRECT
            and self.reasoning_quality is ReasoningQuality.SOUND
        )

    @property
    def is_unassisted_independent_success(self) -> bool:
        return (
            self.kind is PracticeKind.INDEPENDENT
            and self.shows_sound_work
            and self.hint_level == 0
            and not self.answer_viewed
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "correctness": self.correctness.value,
            "reasoning_quality": self.reasoning_quality.value,
            "hint_level": self.hint_level,
            "answer_viewed": self.answer_viewed,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "PracticeAttempt":
        required = {
            "kind", "correctness", "reasoning_quality", "hint_level", "answer_viewed"
        }
        if not isinstance(payload, dict):
            raise ValueError("Practice attempt must be a JSON object.")
        if payload.keys() != required:
            raise ValueError("Practice attempt is missing required fields or has unsupported fields.")
        try:
            return cls(
                kind=PracticeKind(payload["kind"]),
                correctness=AnswerCorrectness(payload["correctness"]),
                reasoning_quality=ReasoningQuality(payload["reasoning_quality"]),
                hint_level=payload["hint_level"],
                answer_viewed=payload["answer_viewed"],
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Practice attempt contains invalid evidence.") from exc
