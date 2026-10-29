import pytest

from learning.evaluation import LearningEvaluator
from learning.progress import (
    AnswerAssessment,
    AnswerCorrectness,
    LearningProgress,
    PracticeKind,
    ProgressStatus,
    ReasoningQuality,
    PracticeAttempt,
    summarize_attempts,
)
from learning.state import LearningEvent, LearningStage
from learning.state_machine import InvalidTransitionError, LearningStateMachine


def assessment(
    correctness=AnswerCorrectness.CORRECT,
    reasoning=ReasoningQuality.SOUND,
):
    return AnswerAssessment(
        correctness=correctness,
        reasoning_quality=reasoning,
        feedback="反馈内容",
        evidence="答案与步骤符合标准",
    )


def to_guided(machine):
    state = machine.start()
    state = machine.transition(state, LearningEvent.GOAL_CONFIRMED, goal="学习分数加法")
    state = machine.transition(state, LearningEvent.BASELINE_COMPLETED)
    return machine.transition(state, LearningEvent.EXPLANATION_COMPLETED)


def to_mastery(machine, evaluator, *, independent_assessment=None):
    state = to_guided(machine)
    state = evaluator.submit_answer(machine, state, assessment())
    state = machine.transition(state, LearningEvent.FEEDBACK_READY_FOR_INDEPENDENT)
    state = machine.set_active_exercise(state, "Independent question")
    state = evaluator.submit_answer(
        machine,
        state,
        independent_assessment or assessment(),
    )
    state = machine.transition(state, LearningEvent.FEEDBACK_READY_FOR_MASTERY)
    return machine.set_active_exercise(state, "Mastery question")


def test_mastery_requires_correct_reasoned_unassisted_independent_work():
    machine = LearningStateMachine()
    evaluator = LearningEvaluator()
    state = to_mastery(machine, evaluator)

    state = evaluator.submit_answer(machine, state, assessment())
    state, result = evaluator.record_mastery_result(machine, state)

    assert result.passed
    assert state.stage is LearningStage.SUMMARY
    assert state.mastery_was_passed
    assert state.practice_attempts[-2].kind is PracticeKind.INDEPENDENT
    assert state.practice_attempts[-1].kind is PracticeKind.MASTERY
    assert LearningProgress.from_state(state).status is ProgressStatus.MASTERY_VERIFIED


@pytest.mark.parametrize(
    "mastery_assessment",
    [
        assessment(AnswerCorrectness.PARTIALLY_CORRECT, ReasoningQuality.SOUND),
        assessment(AnswerCorrectness.CORRECT, ReasoningQuality.PARTIAL),
        assessment(AnswerCorrectness.UNCERTAIN, ReasoningQuality.UNAVAILABLE),
    ],
)
def test_mastery_needs_both_correctness_and_sound_reasoning(mastery_assessment):
    machine = LearningStateMachine()
    evaluator = LearningEvaluator()
    state = to_mastery(machine, evaluator)

    state = evaluator.submit_answer(machine, state, mastery_assessment)
    state, result = evaluator.record_mastery_result(machine, state)

    assert not result.passed
    assert state.stage is LearningStage.FEEDBACK
    assert not state.mastery_was_passed


def test_failed_independent_attempt_cannot_advance_to_mastery_check():
    machine = LearningStateMachine()
    evaluator = LearningEvaluator()
    state = to_guided(machine)
    state = evaluator.submit_answer(machine, state, assessment())
    state = machine.transition(state, LearningEvent.FEEDBACK_READY_FOR_INDEPENDENT)
    state = evaluator.submit_answer(
        machine,
        state,
        assessment(AnswerCorrectness.INCORRECT, ReasoningQuality.UNSOUND),
    )

    with pytest.raises(InvalidTransitionError, match="独立练习中正确作答"):
        machine.transition(state, LearningEvent.FEEDBACK_READY_FOR_MASTERY)


def test_answer_view_for_current_mastery_exercise_is_recorded_and_never_awards_pass():
    machine = LearningStateMachine()
    evaluator = LearningEvaluator()
    state = to_mastery(machine, evaluator)
    state = machine.transition(state, LearningEvent.ANSWER_REQUESTED)
    state = evaluator.submit_answer(machine, state, assessment())

    with pytest.raises(InvalidTransitionError, match="查看过本次掌握度检验的答案"):
        machine.transition(state, LearningEvent.MASTERY_PASSED)

    assert state.practice_attempts[-1].answer_viewed
    assert not state.mastery_was_passed


def test_repeated_mastery_question_never_awards_pass():
    machine = LearningStateMachine()
    evaluator = LearningEvaluator()
    state = to_mastery(machine, evaluator)
    repeated_question = state.active_exercise
    state = machine.set_active_exercise(
        state,
        repeated_question,
        repeated=True,
    )
    state = evaluator.submit_answer(machine, state, assessment())
    state, result = evaluator.record_mastery_result(machine, state)

    assert not result.passed
    assert "重复" in result.explanation
    assert not state.mastery_was_passed


def test_skipping_an_independent_practice_cannot_be_used_as_its_evidence():
    machine = LearningStateMachine()
    evaluator = LearningEvaluator()
    state = to_guided(machine)
    state = evaluator.submit_answer(machine, state, assessment())
    state = machine.transition(state, LearningEvent.FEEDBACK_READY_FOR_INDEPENDENT)
    state = evaluator.submit_answer(machine, state, assessment())
    state = machine.transition(state, LearningEvent.FEEDBACK_READY_FOR_INDEPENDENT)
    state = machine.transition(state, LearningEvent.SKIP_STAGE)
    state = evaluator.submit_answer(machine, state, assessment())

    state, result = evaluator.record_mastery_result(machine, state)

    assert not result.passed
    assert "跳过" in result.explanation
    assert not state.mastery_was_passed


def test_answer_submission_requires_structured_assessment():
    machine = LearningStateMachine()
    state = to_guided(machine)

    with pytest.raises(ValueError, match="AnswerAssessment"):
        machine.transition(state, LearningEvent.PRACTICE_ANSWER_SUBMITTED)


def test_progress_status_distinguishes_learning_evidence():
    machine = LearningStateMachine()
    evaluator = LearningEvaluator()
    state = machine.start()
    assert LearningProgress.from_state(state).status is ProgressStatus.NOT_ENCOUNTERED

    state = machine.transition(state, LearningEvent.GOAL_CONFIRMED, goal="Learn fractions")
    state = machine.transition(state, LearningEvent.BASELINE_COMPLETED)
    state = machine.transition(state, LearningEvent.EXPLANATION_COMPLETED)
    assert LearningProgress.from_state(state).status is ProgressStatus.EXPLAINED

    state = machine.transition(state, LearningEvent.EXPLANATION_REQUESTED)
    state = machine.transition(state, LearningEvent.EXPLANATION_COMPLETED)
    assert LearningProgress.from_state(state).status is ProgressStatus.REPEATED

    state = machine.transition(state, LearningEvent.HINT_REQUESTED)
    state = evaluator.submit_answer(machine, state, assessment())
    progress = LearningProgress.from_state(state)
    assert progress.status is ProgressStatus.COMPLETED_WITH_HINTS
    assert progress.guided_attempt_count == 1


def test_independent_success_is_distinct_from_hint_assisted_completion():
    machine = LearningStateMachine()
    evaluator = LearningEvaluator()
    state = to_mastery(machine, evaluator)

    progress = LearningProgress.from_state(state)

    assert progress.status is ProgressStatus.INDEPENDENTLY_COMPLETED
    assert progress.independent_attempt_count == 1
    assert progress.guided_attempt_count == 1


def test_attempt_summaries_keep_the_latest_five_in_order_and_exclude_answers():
    attempts = tuple(
        PracticeAttempt(
            kind=kind,
            correctness=correctness,
            reasoning_quality=reasoning,
            hint_level=hint_level,
            answer_viewed=viewed,
        )
        for kind, correctness, reasoning, hint_level, viewed in (
            (PracticeKind.GUIDED, AnswerCorrectness.INCORRECT, ReasoningQuality.UNSOUND, 1, False),
            (PracticeKind.GUIDED, AnswerCorrectness.PARTIALLY_CORRECT, ReasoningQuality.PARTIAL, 2, True),
            (PracticeKind.INDEPENDENT, AnswerCorrectness.INCORRECT, ReasoningQuality.UNSOUND, 0, False),
            (PracticeKind.INDEPENDENT, AnswerCorrectness.CORRECT, ReasoningQuality.SOUND, 0, False),
            (PracticeKind.MASTERY, AnswerCorrectness.PARTIALLY_CORRECT, ReasoningQuality.PARTIAL, 0, False),
            (PracticeKind.MASTERY, AnswerCorrectness.CORRECT, ReasoningQuality.SOUND, 0, True),
        )
    )

    summaries = summarize_attempts(attempts)

    assert summaries == (
        "guided:partially_correct/partial:hint2:viewed1",
        "independent:incorrect/unsound:hint0:viewed0",
        "independent:correct/sound:hint0:viewed0",
        "mastery:partially_correct/partial:hint0:viewed0",
        "mastery:correct/sound:hint0:viewed1",
    )
    assert summarize_attempts((), limit=5) == ()
    assert summarize_attempts(attempts, limit=0) == ()

