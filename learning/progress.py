"""Learner progress derived from a session history."""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from learning.evidence import (
    AnswerAssessment,
    AnswerCorrectness,
    PracticeAttempt,
    PracticeKind,
    ReasoningQuality,
)

if TYPE_CHECKING:
    from learning.state import LearningState


class ProgressStatus(str, Enum):
    NOT_ENCOUNTERED = "not_encountered"
    EXPLAINED = "explained"
    REPEATED = "repeated_learning"
    COMPLETED_WITH_HINTS = "completed_with_hints"
    INDEPENDENTLY_COMPLETED = "independently_completed"
    MASTERY_VERIFIED = "mastery_verified"
    UNVERIFIED = "unverified"


PROGRESS_LABELS: dict[ProgressStatus, str] = {
    ProgressStatus.NOT_ENCOUNTERED: "尚未接触",
    ProgressStatus.EXPLAINED: "已讲解",
    ProgressStatus.REPEATED: "已重复学习",
    ProgressStatus.COMPLETED_WITH_HINTS: "在提示帮助下完成",
    ProgressStatus.INDEPENDENTLY_COMPLETED: "独立完成",
    ProgressStatus.MASTERY_VERIFIED: "已通过掌握度检验",
    ProgressStatus.UNVERIFIED: "尚未验证掌握程度",
}


@dataclass(frozen=True)
class LearningProgress:
    """A readable status plus the distinct evidence counts behind it."""

    status: ProgressStatus
    explanation_count: int
    guided_attempt_count: int
    independent_attempt_count: int
    mastery_attempt_count: int

    @classmethod
    def from_state(cls, state: "LearningState") -> "LearningProgress":
        from learning.state import LearningEvent

        explanation_count = sum(
            event is LearningEvent.EXPLANATION_COMPLETED
            for event in state.events
        )
        attempts = state.practice_attempts
        if LearningEvent.MASTERY_PASSED in state.events:
            status = ProgressStatus.MASTERY_VERIFIED
        elif any(attempt.is_unassisted_independent_success for attempt in attempts):
            status = ProgressStatus.INDEPENDENTLY_COMPLETED
        elif any(
            attempt.shows_sound_work and (attempt.hint_level > 0 or attempt.answer_viewed)
            for attempt in attempts
        ):
            status = ProgressStatus.COMPLETED_WITH_HINTS
        elif not attempts and explanation_count > 1:
            status = ProgressStatus.REPEATED
        elif not attempts and explanation_count == 1:
            status = ProgressStatus.EXPLAINED
        elif not attempts:
            status = ProgressStatus.NOT_ENCOUNTERED
        else:
            status = ProgressStatus.UNVERIFIED

        return cls(
            status=status,
            explanation_count=explanation_count,
            guided_attempt_count=sum(a.kind is PracticeKind.GUIDED for a in attempts),
            independent_attempt_count=sum(a.kind is PracticeKind.INDEPENDENT for a in attempts),
            mastery_attempt_count=sum(a.kind is PracticeKind.MASTERY for a in attempts),
        )


def mastery_pass_eligibility(
    attempts: tuple[PracticeAttempt, ...],
    *,
    answer_viewed_for_current_exercise: bool,
    independent_practice_skipped: bool,
    mastery_exercise_repeated: bool = False,
) -> tuple[bool, str]:
    """Apply the Python-owned mastery rule to the submitted evidence history."""
    if not attempts or attempts[-1].kind is not PracticeKind.MASTERY:
        return False, "请先提交本次掌握度检验的作答。"

    mastery_attempt = attempts[-1]
    if mastery_exercise_repeated:
        return False, "本次检验题目与之前的练习重复，不能作为掌握证据。"
    if answer_viewed_for_current_exercise or mastery_attempt.answer_viewed:
        return False, "查看过本次掌握度检验的答案后，不能将本次结果记为通过。"
    if mastery_attempt.hint_level > 0:
        return False, "掌握度检验使用过提示，本次不能记为独立通过。"
    if mastery_attempt.correctness is not AnswerCorrectness.CORRECT:
        return False, "本次掌握度检验尚未正确完成。"
    if mastery_attempt.reasoning_quality is not ReasoningQuality.SOUND:
        return False, "本次掌握度检验的推理过程尚未验证为合理。"
    if independent_practice_skipped:
        return False, "独立练习被跳过；请完成一轮新的独立练习后再检验掌握度。"

    previous_independent = attempts[-2] if len(attempts) > 1 else None
    if previous_independent is None or not previous_independent.is_unassisted_independent_success:
        return False, "还需要先在无提示的独立练习中正确作答并说明合理推理。"
    return True, "独立练习和掌握度检验均显示正确作答及合理推理。"


def summarize_attempts(
    attempts: tuple[PracticeAttempt, ...],
    *,
    limit: int = 5,
) -> tuple[str, ...]:
    """Return compact, privacy-safe summaries of the latest practice evidence."""
    if not isinstance(attempts, tuple):
        raise TypeError("attempts must be a tuple of PracticeAttempt values.")
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a non-negative integer.")
    if any(not isinstance(attempt, PracticeAttempt) for attempt in attempts):
        raise TypeError("attempts must contain PracticeAttempt values.")
    if limit == 0:
        return ()

    return tuple(
        f"{attempt.kind.value}:{attempt.correctness.value}/{attempt.reasoning_quality.value}:"
        f"hint{attempt.hint_level}:viewed{int(attempt.answer_viewed)}"
        for attempt in attempts[-limit:]
    )
