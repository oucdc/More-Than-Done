"""Python-owned rules that combine answer evidence with learner behavior."""

from dataclasses import dataclass

from learning.evidence import AnswerAssessment
from learning.progress import mastery_pass_eligibility
from learning.state import LearningEvent, LearningStage, LearningState
from learning.state_machine import LearningStateMachine


@dataclass(frozen=True)
class MasteryEvaluation:
    passed: bool
    explanation: str


class LearningEvaluator:
    """Record reviewed answers and apply explicit independent-mastery criteria."""

    def submit_answer(
        self,
        machine: LearningStateMachine,
        state: LearningState,
        assessment: AnswerAssessment,
    ) -> LearningState:
        """Attach answer quality to the current practice without saving raw text."""
        return machine.submit_answer(state, assessment)

    def evaluate_mastery(self, state: LearningState) -> MasteryEvaluation:
        """Require successful unassisted independent practice plus a sound check."""
        if state.stage is not LearningStage.MASTERY_CHECK:
            raise ValueError("Mastery can only be evaluated in the mastery_check stage.")
        if not state.mastery_answer_submitted:
            raise ValueError("Submit an answer to the mastery check before evaluating it.")
        passed, explanation = mastery_pass_eligibility(
            state.practice_attempts,
            answer_viewed_for_current_exercise=state.answer_viewed_for_current_exercise,
            independent_practice_skipped=state.independent_practice_skipped,
            mastery_exercise_repeated=state.mastery_exercise_repeated,
        )
        return MasteryEvaluation(passed=passed, explanation=explanation)

    def record_mastery_result(
        self,
        machine: LearningStateMachine,
        state: LearningState,
    ) -> tuple[LearningState, MasteryEvaluation]:
        """Evaluate then ask Python's state machine to record pass or retry."""
        result = self.evaluate_mastery(state)
        event = LearningEvent.MASTERY_PASSED if result.passed else LearningEvent.MASTERY_NOT_PASSED
        return machine.transition(state, event), result
