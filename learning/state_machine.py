"""Deterministic learning-stage transitions owned by Python."""

from dataclasses import replace
from types import MappingProxyType

from learning.evidence import (
    AnswerAssessment,
    AnswerCorrectness,
    PracticeAttempt,
    PracticeKind,
    ReasoningQuality,
)
from learning.progress import mastery_pass_eligibility
from learning.state import EVENT_LABELS, STAGE_LABELS, LearningEvent, LearningStage, LearningState


class InvalidTransitionError(ValueError):
    """Raised when an event is not legal in the current learning stage."""

    def __init__(self, stage: LearningStage, event: LearningEvent, reason: str | None = None):
        self.stage = stage
        self.event = event
        message = reason or (
            f"不能在「{STAGE_LABELS.get(stage, '未知阶段')}」阶段执行「{EVENT_LABELS.get(event, '未知操作')}」操作。"
        )
        super().__init__(message)


class HintLimitReachedError(InvalidTransitionError):
    """Raised when the session already reached the strongest hint level."""

    def __init__(self, stage: LearningStage, event: LearningEvent):
        super().__init__(stage, event, "已达到 L4 提示上限，请选择继续练习、讲解或查看答案。")


TRANSITIONS = MappingProxyType({
    (LearningStage.GOAL_CLARIFICATION, LearningEvent.GOAL_CONFIRMED): LearningStage.BASELINE_ASSESSMENT,
    (LearningStage.BASELINE_ASSESSMENT, LearningEvent.BASELINE_COMPLETED): LearningStage.CONCEPT_EXPLANATION,
    (LearningStage.CONCEPT_EXPLANATION, LearningEvent.EXPLANATION_COMPLETED): LearningStage.GUIDED_PRACTICE,
    (LearningStage.GUIDED_PRACTICE, LearningEvent.PRACTICE_ANSWER_SUBMITTED): LearningStage.FEEDBACK,
    (LearningStage.INDEPENDENT_PRACTICE, LearningEvent.PRACTICE_ANSWER_SUBMITTED): LearningStage.FEEDBACK,
    (LearningStage.FEEDBACK, LearningEvent.FEEDBACK_CONTINUE_GUIDED): LearningStage.GUIDED_PRACTICE,
    (LearningStage.FEEDBACK, LearningEvent.FEEDBACK_READY_FOR_INDEPENDENT): LearningStage.INDEPENDENT_PRACTICE,
    (LearningStage.FEEDBACK, LearningEvent.FEEDBACK_READY_FOR_MASTERY): LearningStage.MASTERY_CHECK,
    (LearningStage.MASTERY_CHECK, LearningEvent.MASTERY_PASSED): LearningStage.SUMMARY,
    (LearningStage.MASTERY_CHECK, LearningEvent.MASTERY_NOT_PASSED): LearningStage.FEEDBACK,
    (LearningStage.SUMMARY, LearningEvent.SUMMARY_COMPLETED): LearningStage.COMPLETED,
})

SKIP_DESTINATIONS = MappingProxyType({
    LearningStage.GOAL_CLARIFICATION: LearningStage.BASELINE_ASSESSMENT,
    LearningStage.BASELINE_ASSESSMENT: LearningStage.CONCEPT_EXPLANATION,
    LearningStage.CONCEPT_EXPLANATION: LearningStage.GUIDED_PRACTICE,
    LearningStage.GUIDED_PRACTICE: LearningStage.INDEPENDENT_PRACTICE,
    LearningStage.FEEDBACK: LearningStage.INDEPENDENT_PRACTICE,
    LearningStage.INDEPENDENT_PRACTICE: LearningStage.MASTERY_CHECK,
    LearningStage.MASTERY_CHECK: LearningStage.SUMMARY,
    LearningStage.SUMMARY: LearningStage.COMPLETED,
})


class LearningStateMachine:
    """Apply explicit events and return new immutable states."""

    def start(self) -> LearningState:
        return LearningState()

    def transition(
        self,
        state: LearningState,
        event: LearningEvent,
        *,
        goal: str | None = None,
        assessment: AnswerAssessment | None = None,
    ) -> LearningState:
        if not isinstance(state, LearningState):
            raise TypeError("state must be a LearningState value.")
        if not isinstance(event, LearningEvent):
            raise TypeError("event must be a LearningEvent value.")
        if state.is_terminal:
            raise InvalidTransitionError(state.stage, event, "学习会话已经结束。")
        if goal is not None and event is not LearningEvent.GOAL_CONFIRMED:
            raise ValueError("goal may only be supplied with GOAL_CONFIRMED.")
        if assessment is not None and not isinstance(assessment, AnswerAssessment):
            raise TypeError("assessment must be an AnswerAssessment value.")
        if assessment is not None and event not in {
            LearningEvent.BASELINE_COMPLETED,
            LearningEvent.PRACTICE_ANSWER_SUBMITTED,
            LearningEvent.MASTERY_ANSWER_SUBMITTED,
        }:
            raise ValueError("assessment may only be supplied when completing a baseline or submitting an answer.")
        if event in {
            LearningEvent.PRACTICE_ANSWER_SUBMITTED,
            LearningEvent.MASTERY_ANSWER_SUBMITTED,
        }:
            if assessment is None:
                raise ValueError("An AnswerAssessment is required; use submit_answer().")
            if event is LearningEvent.MASTERY_ANSWER_SUBMITTED and state.stage is not LearningStage.MASTERY_CHECK:
                raise InvalidTransitionError(state.stage, event)
            if event is LearningEvent.PRACTICE_ANSWER_SUBMITTED and state.stage not in {
                LearningStage.GUIDED_PRACTICE,
                LearningStage.INDEPENDENT_PRACTICE,
            }:
                raise InvalidTransitionError(state.stage, event)
            return self.submit_answer(state, assessment)

        if event is LearningEvent.EXIT_SESSION:
            return self._record(state, event, stage=LearningStage.EXITED, **self._clear_active_exercise())
        if event is LearningEvent.EXPLANATION_REQUESTED:
            return self._record(
                state,
                event,
                stage=LearningStage.CONCEPT_EXPLANATION,
                hint_level=0,
                **self._clear_active_exercise(),
                mastery_answer_submitted=False,
            )
        if event is LearningEvent.PRACTICE_REQUESTED:
            return self._record(
                state,
                event,
                stage=LearningStage.GUIDED_PRACTICE,
                hint_level=0,
                **self._clear_active_exercise(),
                mastery_answer_submitted=False,
            )
        if event is LearningEvent.SUMMARY_REQUESTED:
            return self._record(state, event, stage=LearningStage.SUMMARY, **self._clear_active_exercise())
        if event is LearningEvent.ANSWER_REQUESTED:
            return self._record(
                state,
                event,
                answer_viewed_for_current_exercise=(
                    state.answer_viewed_for_current_exercise or bool(state.active_exercise)
                ),
            )
        if event is LearningEvent.HINT_REQUESTED:
            return self._request_hint(state)
        if event is LearningEvent.SKIP_STAGE:
            return self._skip_stage(state)

        destination = TRANSITIONS.get((state.stage, event))
        if destination is None:
            raise InvalidTransitionError(state.stage, event)

        changes: dict[str, object] = {"stage": destination}
        if event is LearningEvent.GOAL_CONFIRMED:
            confirmed_goal = goal if goal is not None else state.goal
            if not isinstance(confirmed_goal, str) or not confirmed_goal.strip():
                raise ValueError("A non-empty goal is required to confirm the learning goal.")
            changes["goal"] = confirmed_goal.strip()

        if event is LearningEvent.BASELINE_COMPLETED:
            changes["baseline_result"] = None
            changes["learner_level"] = None
            if assessment is not None:
                changes["baseline_result"] = (
                    f"{assessment.correctness.value}:{assessment.reasoning_quality.value}"
                )
                changes["learner_level"] = self._baseline_learner_level(assessment)

        if event is LearningEvent.MASTERY_PASSED:
            eligible, explanation = mastery_pass_eligibility(
                state.practice_attempts,
                answer_viewed_for_current_exercise=state.answer_viewed_for_current_exercise,
                independent_practice_skipped=state.independent_practice_skipped,
                mastery_exercise_repeated=state.mastery_exercise_repeated,
            )
            if not eligible:
                raise InvalidTransitionError(state.stage, event, explanation)
        if event in {LearningEvent.MASTERY_PASSED, LearningEvent.MASTERY_NOT_PASSED}:
            if not state.mastery_answer_submitted:
                raise InvalidTransitionError(
                    state.stage,
                    event,
                    "请先提交本次掌握度检验的作答，再记录检验结果。",
                )
        if event is LearningEvent.FEEDBACK_READY_FOR_MASTERY:
            if (
                not state.practice_attempts
                or not state.practice_attempts[-1].is_unassisted_independent_success
            ):
                raise InvalidTransitionError(
                    state.stage,
                    event,
                    "请先在无提示的独立练习中正确作答并说明合理推理。",
                )
        if event is LearningEvent.FEEDBACK_READY_FOR_INDEPENDENT:
            changes["hint_level"] = 0
        if destination is LearningStage.MASTERY_CHECK:
            changes["hint_level"] = 0
        if event is LearningEvent.EXPLANATION_COMPLETED:
            changes["hint_level"] = 0
        if destination in {
            LearningStage.GUIDED_PRACTICE,
            LearningStage.INDEPENDENT_PRACTICE,
            LearningStage.MASTERY_CHECK,
        }:
            changes.update(self._clear_active_exercise())
            changes["mastery_answer_submitted"] = False
        if destination is LearningStage.MASTERY_CHECK:
            changes["mastery_exercise_repeated"] = False

        if destination in {LearningStage.SUMMARY, LearningStage.COMPLETED, LearningStage.EXITED}:
            changes.update(self._clear_active_exercise())
        if event is LearningEvent.BASELINE_COMPLETED:
            changes.update(self._clear_active_exercise())

        return self._record(state, event, **changes)

    def _request_hint(self, state: LearningState) -> LearningState:
        if state.stage in {LearningStage.GUIDED_PRACTICE, LearningStage.FEEDBACK}:
            next_level = state.hint_level + 1
            if next_level > 4:
                raise HintLimitReachedError(state.stage, LearningEvent.HINT_REQUESTED)
            return self._record(
                state,
                LearningEvent.HINT_REQUESTED,
                stage=LearningStage.GUIDED_PRACTICE,
                hint_level=next_level,
            )

        if state.stage in {LearningStage.INDEPENDENT_PRACTICE, LearningStage.MASTERY_CHECK}:
            return self._record(
                state,
                LearningEvent.HINT_REQUESTED,
                stage=LearningStage.GUIDED_PRACTICE,
                hint_level=1,
                mastery_answer_submitted=False,
            )

        raise InvalidTransitionError(state.stage, LearningEvent.HINT_REQUESTED)

    def _skip_stage(self, state: LearningState) -> LearningState:
        destination = SKIP_DESTINATIONS.get(state.stage)
        if destination is None:
            raise InvalidTransitionError(state.stage, LearningEvent.SKIP_STAGE)
        return self._record(
            state,
            LearningEvent.SKIP_STAGE,
            stage=destination,
            goal=(
                (state.goal or "自主探索")
                if state.stage is LearningStage.GOAL_CLARIFICATION
                else state.goal
            ),
            skipped_stages=state.skipped_stages + (state.stage,),
            hint_level=(
                0
                if destination
                in {
                    LearningStage.GUIDED_PRACTICE,
                    LearningStage.INDEPENDENT_PRACTICE,
                    LearningStage.MASTERY_CHECK,
                }
                else state.hint_level
            ),
            mastery_answer_submitted=(
                False if destination is LearningStage.MASTERY_CHECK else state.mastery_answer_submitted
            ),
            independent_practice_skipped=(
                state.independent_practice_skipped
                or state.stage is LearningStage.INDEPENDENT_PRACTICE
            ),
            **self._clear_active_exercise(),
        )

    def submit_answer(self, state: LearningState, assessment: AnswerAssessment) -> LearningState:
        """Record an answer with behavior evidence captured from the current state."""
        if not isinstance(state, LearningState):
            raise TypeError("state must be a LearningState value.")
        if not isinstance(assessment, AnswerAssessment):
            raise TypeError("assessment must be an AnswerAssessment value.")
        if state.is_terminal:
            raise InvalidTransitionError(state.stage, LearningEvent.PRACTICE_ANSWER_SUBMITTED)

        if state.stage is LearningStage.GUIDED_PRACTICE:
            kind = PracticeKind.GUIDED
            event = LearningEvent.PRACTICE_ANSWER_SUBMITTED
            destination = LearningStage.FEEDBACK
        elif state.stage is LearningStage.INDEPENDENT_PRACTICE:
            kind = PracticeKind.INDEPENDENT
            event = LearningEvent.PRACTICE_ANSWER_SUBMITTED
            destination = LearningStage.FEEDBACK
        elif state.stage is LearningStage.MASTERY_CHECK:
            kind = PracticeKind.MASTERY
            event = LearningEvent.MASTERY_ANSWER_SUBMITTED
            destination = LearningStage.MASTERY_CHECK
        else:
            raise InvalidTransitionError(state.stage, LearningEvent.PRACTICE_ANSWER_SUBMITTED)

        attempt = PracticeAttempt(
            kind=kind,
            correctness=assessment.correctness,
            reasoning_quality=assessment.reasoning_quality,
            hint_level=state.hint_level,
            answer_viewed=state.answer_viewed_for_current_exercise,
        )
        return self._record(
            state,
            event,
            stage=destination,
            practice_attempts=state.practice_attempts + (attempt,),
            mastery_answer_submitted=(kind is PracticeKind.MASTERY),
            independent_practice_skipped=(
                False if kind is PracticeKind.INDEPENDENT else state.independent_practice_skipped
            ),
        )

    @staticmethod
    def set_active_exercise(
        state: LearningState,
        exercise: str,
        *,
        success_criteria: str | None = None,
        reference_answer: str | None = None,
        repeated: bool = False,
    ) -> LearningState:
        """Attach the current prompt so a CLI session can resume after restart."""
        if not isinstance(state, LearningState):
            raise TypeError("state must be a LearningState value.")
        if not isinstance(exercise, str) or not exercise.strip():
            raise ValueError("exercise must be non-empty text.")
        if success_criteria is not None and not isinstance(success_criteria, str):
            raise TypeError("success_criteria must be text or None.")
        if reference_answer is not None and not isinstance(reference_answer, str):
            raise TypeError("reference_answer must be text or None.")
        if type(repeated) is not bool:
            raise TypeError("repeated must be a bool.")
        if state.stage not in {
            LearningStage.BASELINE_ASSESSMENT,
            LearningStage.GUIDED_PRACTICE,
            LearningStage.INDEPENDENT_PRACTICE,
            LearningStage.MASTERY_CHECK,
        }:
            raise InvalidTransitionError(
                state.stage,
                LearningEvent.PRACTICE_REQUESTED,
                "当前阶段不能保存练习题。",
            )
        return replace(
            state,
            active_exercise=exercise.strip(),
            active_success_criteria=success_criteria,
            active_reference_answer=reference_answer,
            answer_viewed_for_current_exercise=False,
            mastery_exercise_repeated=(
                repeated if state.stage is LearningStage.MASTERY_CHECK else False
            ),
            recent_exercises=(state.recent_exercises + (exercise.strip(),))[-10:],
            hint_level=0 if state.active_exercise is None else state.hint_level,
        )

    @staticmethod
    def _clear_active_exercise() -> dict[str, object]:
        return {
            "active_exercise": None,
            "active_success_criteria": None,
            "active_reference_answer": None,
            "answer_viewed_for_current_exercise": False,
        }

    @staticmethod
    def _baseline_learner_level(assessment: AnswerAssessment) -> str:
        correctness = assessment.correctness
        reasoning = assessment.reasoning_quality
        if correctness is AnswerCorrectness.UNCERTAIN or reasoning is ReasoningQuality.UNAVAILABLE:
            return "基础未知，先用低难度问题确认"
        if correctness is AnswerCorrectness.CORRECT and reasoning is ReasoningQuality.SOUND:
            return "已能正确作答且推理完整"
        if correctness is AnswerCorrectness.CORRECT:
            return "能得出正确答案，但推理不完整"
        if correctness is AnswerCorrectness.PARTIALLY_CORRECT:
            return "部分掌握，需要补关键步骤"
        if correctness is AnswerCorrectness.INCORRECT:
            return "基础薄弱，需要从概念讲起"
        return "基础未知，先用低难度问题确认"

    @staticmethod
    def _record(state: LearningState, event: LearningEvent, **changes: object) -> LearningState:
        return replace(state, events=state.events + (event,), **changes)
