import pytest

from learning.state import LearningEvent as Event
from learning.state import LearningStage as Stage
from learning.state import LearningState
from learning.progress import AnswerAssessment, AnswerCorrectness, ReasoningQuality
from learning.state_machine import (
    HintLimitReachedError,
    InvalidTransitionError,
    LearningStateMachine,
)


@pytest.fixture
def machine():
    return LearningStateMachine()


def advance_to_guided(machine):
    state = machine.start()
    state = machine.transition(state, Event.GOAL_CONFIRMED, goal="Learn fractions")
    state = machine.transition(state, Event.BASELINE_COMPLETED)
    return machine.transition(state, Event.EXPLANATION_COMPLETED)


def reviewed_answer(
    correctness=AnswerCorrectness.CORRECT,
    reasoning=ReasoningQuality.SOUND,
):
    return AnswerAssessment(
        correctness=correctness,
        reasoning_quality=reasoning,
        feedback="Reviewed answer.",
    )


def advance_to_independent(machine):
    state = advance_to_guided(machine)
    state = machine.submit_answer(state, reviewed_answer())
    state = machine.transition(state, Event.FEEDBACK_READY_FOR_INDEPENDENT)
    return machine.set_active_exercise(state, "Independent question")


def advance_to_mastery(machine):
    state = advance_to_independent(machine)
    state = machine.submit_answer(state, reviewed_answer())
    state = machine.transition(state, Event.FEEDBACK_READY_FOR_MASTERY)
    return machine.set_active_exercise(state, "Mastery question")


def test_happy_path_uses_explicit_python_events(machine):
    state = advance_to_guided(machine)
    assert state.stage is Stage.GUIDED_PRACTICE

    state = machine.submit_answer(state, reviewed_answer())
    assert state.stage is Stage.FEEDBACK

    state = machine.transition(state, Event.FEEDBACK_READY_FOR_INDEPENDENT)
    assert state.stage is Stage.INDEPENDENT_PRACTICE

    state = machine.submit_answer(state, reviewed_answer())
    state = machine.transition(state, Event.FEEDBACK_READY_FOR_MASTERY)
    assert state.stage is Stage.MASTERY_CHECK

    state = machine.submit_answer(state, reviewed_answer())
    state = machine.transition(state, Event.MASTERY_PASSED)
    assert state.stage is Stage.SUMMARY
    assert state.mastery_was_passed

    state = machine.transition(state, Event.SUMMARY_COMPLETED)
    assert state.stage is Stage.COMPLETED
    assert state.is_terminal


def test_invalid_transition_is_rejected_without_mutating_previous_state(machine):
    state = machine.start()

    with pytest.raises(InvalidTransitionError) as error:
        machine.transition(state, Event.BASELINE_COMPLETED)

    assert "目标确认" in str(error.value)
    assert "完成基础诊断" in str(error.value)
    assert "goal_clarification" not in str(error.value)
    assert "baseline_completed" not in str(error.value)

    assert state.stage is Stage.GOAL_CLARIFICATION
    assert state.events == ()


def test_goal_must_be_confirmed_before_the_flow_advances(machine):
    state = machine.start()

    with pytest.raises(ValueError, match="non-empty goal"):
        machine.transition(state, Event.GOAL_CONFIRMED, goal="  ")

    assert state.stage is Stage.GOAL_CLARIFICATION


@pytest.mark.parametrize(
    ("correctness", "reasoning", "result", "level"),
    [
        (
            AnswerCorrectness.CORRECT,
            ReasoningQuality.SOUND,
            "correct:sound",
            "已能正确作答且推理完整",
        ),
        (
            AnswerCorrectness.CORRECT,
            ReasoningQuality.PARTIAL,
            "correct:partial",
            "能得出正确答案，但推理不完整",
        ),
        (
            AnswerCorrectness.CORRECT,
            ReasoningQuality.UNSOUND,
            "correct:unsound",
            "能得出正确答案，但推理不完整",
        ),
        (
            AnswerCorrectness.PARTIALLY_CORRECT,
            ReasoningQuality.SOUND,
            "partially_correct:sound",
            "部分掌握，需要补关键步骤",
        ),
        (
            AnswerCorrectness.INCORRECT,
            ReasoningQuality.UNSOUND,
            "incorrect:unsound",
            "基础薄弱，需要从概念讲起",
        ),
        (
            AnswerCorrectness.UNCERTAIN,
            ReasoningQuality.PARTIAL,
            "uncertain:partial",
            "基础未知，先用低难度问题确认",
        ),
        (
            AnswerCorrectness.PARTIALLY_CORRECT,
            ReasoningQuality.UNAVAILABLE,
            "partially_correct:unavailable",
            "基础未知，先用低难度问题确认",
        ),
    ],
)
def test_baseline_assessment_sets_classification_without_creating_attempt(
    machine, correctness, reasoning, result, level
):
    state = machine.transition(machine.start(), Event.GOAL_CONFIRMED, goal="Learn fractions")

    state = machine.transition(
        state,
        Event.BASELINE_COMPLETED,
        assessment=reviewed_answer(correctness, reasoning),
    )

    assert state.stage is Stage.CONCEPT_EXPLANATION
    assert state.baseline_result == result
    assert state.learner_level == level
    assert state.practice_attempts == ()


def test_baseline_without_assessment_keeps_classification_unknown(machine):
    state = machine.transition(machine.start(), Event.GOAL_CONFIRMED, goal="Learn fractions")

    state = machine.transition(state, Event.BASELINE_COMPLETED)

    assert state.baseline_result is None
    assert state.learner_level is None
    assert state.practice_attempts == ()


def test_hints_escalate_from_l1_to_l4_and_stop(machine):
    state = advance_to_guided(machine)

    for expected_level in range(1, 5):
        state = machine.transition(state, Event.HINT_REQUESTED)
        assert state.hint_level == expected_level

    events_before = state.events
    with pytest.raises(HintLimitReachedError):
        machine.transition(state, Event.HINT_REQUESTED)

    assert state.hint_level == 4
    assert state.events == events_before


def test_hint_in_independent_practice_returns_to_guided_practice(machine):
    state = advance_to_independent(machine)

    state = machine.transition(state, Event.HINT_REQUESTED)

    assert state.stage is Stage.GUIDED_PRACTICE
    assert state.hint_level == 1
    assert state.events[-1] is Event.HINT_REQUESTED
    assert not state.mastery_was_passed


def test_hint_in_mastery_check_returns_to_guided_and_cannot_pass(machine):
    state = advance_to_mastery(machine)

    state = machine.transition(state, Event.HINT_REQUESTED)

    assert state.stage is Stage.GUIDED_PRACTICE
    assert state.hint_level == 1
    assert not state.mastery_answer_submitted
    with pytest.raises(InvalidTransitionError):
        machine.transition(state, Event.MASTERY_PASSED)
    assert not state.mastery_was_passed


def test_answer_request_is_recorded_without_advancing_or_awarding_mastery(machine):
    state = machine.set_active_exercise(advance_to_guided(machine), "Guided question")

    answered = machine.transition(state, Event.ANSWER_REQUESTED)

    assert answered.stage is Stage.GUIDED_PRACTICE
    assert answered.answer_was_requested
    assert not answered.mastery_was_passed


def test_answer_view_during_mastery_check_blocks_a_pass(machine):
    state = advance_to_mastery(machine)
    state = machine.transition(state, Event.ANSWER_REQUESTED)
    state = machine.submit_answer(state, reviewed_answer())

    with pytest.raises(InvalidTransitionError, match="查看过本次掌握度检验的答案"):
        machine.transition(state, Event.MASTERY_PASSED)

    assert state.stage is Stage.MASTERY_CHECK
    assert not state.mastery_was_passed


def test_answer_view_during_independent_practice_requires_a_fresh_attempt(machine):
    state = advance_to_independent(machine)
    state = machine.transition(state, Event.ANSWER_REQUESTED)
    state = machine.submit_answer(state, reviewed_answer())

    with pytest.raises(InvalidTransitionError, match="无提示的独立练习"):
        machine.transition(state, Event.FEEDBACK_READY_FOR_MASTERY)

    state = machine.transition(state, Event.FEEDBACK_READY_FOR_INDEPENDENT)
    assert not state.answer_viewed_for_current_exercise
    state = machine.set_active_exercise(state, "A new independent question")
    state = machine.submit_answer(state, reviewed_answer())
    state = machine.transition(state, Event.FEEDBACK_READY_FOR_MASTERY)
    state = machine.set_active_exercise(state, "A new mastery question")
    state = machine.submit_answer(state, reviewed_answer())
    state = machine.transition(state, Event.MASTERY_PASSED)
    assert state.mastery_was_passed


def test_answer_view_survives_hint_transition_and_is_recorded_on_guided_attempt(machine):
    state = advance_to_independent(machine)
    state = machine.transition(state, Event.ANSWER_REQUESTED)
    state = machine.transition(state, Event.HINT_REQUESTED)

    state = machine.submit_answer(state, reviewed_answer())

    assert state.practice_attempts[-1].kind.value == "guided"
    assert state.practice_attempts[-1].answer_viewed is True
    assert state.practice_attempts[-1].hint_level == 1


def test_answer_viewed_in_feedback_clears_for_new_mastery_exercise(machine):
    state = advance_to_independent(machine)
    state = machine.submit_answer(state, reviewed_answer())
    state = machine.transition(state, Event.ANSWER_REQUESTED)
    assert state.answer_viewed_for_current_exercise

    state = machine.transition(state, Event.FEEDBACK_READY_FOR_MASTERY)
    assert state.active_exercise is None
    assert not state.answer_viewed_for_current_exercise
    state = machine.set_active_exercise(state, "Different mastery question")
    state = machine.submit_answer(state, reviewed_answer())
    state = machine.transition(state, Event.MASTERY_PASSED)

    assert state.mastery_was_passed


def test_skipping_stages_is_recorded_and_never_marks_mastery(machine):
    state = machine.start()

    for _ in range(7):
        state = machine.transition(state, Event.SKIP_STAGE)

    assert state.stage is Stage.COMPLETED
    assert len(state.skipped_stages) == 7
    assert not state.mastery_was_passed
    assert Event.MASTERY_PASSED not in state.events


def test_skipping_goal_uses_a_neutral_fallback_for_the_remaining_flow(machine):
    state = machine.transition(machine.start(), Event.SKIP_STAGE)

    assert state.stage is Stage.BASELINE_ASSESSMENT
    assert state.goal == "自主探索"
    assert state.skipped_stages == (Stage.GOAL_CLARIFICATION,)


def test_active_exercise_is_cleared_when_a_new_practice_stage_starts(machine):
    state = advance_to_guided(machine)
    state = machine.set_active_exercise(
        state,
        "Solve x + 2 = 5",
        success_criteria="Show the inverse operation.",
        reference_answer="x = 3",
    )
    state = machine.submit_answer(state, reviewed_answer())
    state = machine.transition(state, Event.FEEDBACK_READY_FOR_INDEPENDENT)

    assert state.stage is Stage.INDEPENDENT_PRACTICE
    assert state.active_exercise is None
    assert state.active_success_criteria is None
    assert state.active_reference_answer is None
    assert not state.answer_viewed_for_current_exercise


def test_setting_a_new_exercise_resets_stale_hint_level(machine):
    state = LearningState(stage=Stage.GUIDED_PRACTICE, goal="Learn fractions", hint_level=3)

    state = machine.set_active_exercise(state, "Calculate 1/2 + 1/4")

    assert state.active_exercise == "Calculate 1/2 + 1/4"
    assert state.hint_level == 0


def test_setting_exercises_keeps_only_the_last_ten_in_order(machine):
    state = LearningState(
        stage=Stage.INDEPENDENT_PRACTICE,
        goal="Learn fractions",
        recent_exercises=("q0", "q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8"),
    )

    state = machine.set_active_exercise(state, "q9")
    state = machine.set_active_exercise(state, "q10")

    assert state.recent_exercises == ("q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "q9", "q10")


def test_new_exercise_clears_answer_view_evidence_and_stores_scoring_metadata(machine):
    state = LearningState(
        stage=Stage.MASTERY_CHECK,
        goal="Learn fractions",
        active_exercise="Old question",
        answer_viewed_for_current_exercise=True,
    )

    state = machine.set_active_exercise(
        state,
        "New question",
        success_criteria="Explain the steps.",
        reference_answer="42",
        repeated=True,
    )

    assert not state.answer_viewed_for_current_exercise
    assert state.active_success_criteria == "Explain the steps."
    assert state.active_reference_answer == "42"
    assert state.mastery_exercise_repeated
    assert state.recent_exercises == ("New question",)


def test_mastery_failure_returns_to_feedback(machine):
    state = advance_to_mastery(machine)
    state = machine.submit_answer(
        state,
        reviewed_answer(AnswerCorrectness.INCORRECT, ReasoningQuality.UNSOUND),
    )

    state = machine.transition(state, Event.MASTERY_NOT_PASSED)

    assert state.stage is Stage.FEEDBACK
    assert not state.mastery_was_passed


def test_mastery_result_requires_a_submitted_answer(machine):
    state = advance_to_mastery(machine)

    with pytest.raises(InvalidTransitionError, match="先提交本次掌握度检验"):
        machine.transition(state, Event.MASTERY_PASSED)

    assert state.stage is Stage.MASTERY_CHECK
    assert not state.mastery_was_passed


def test_exit_and_completed_states_are_terminal(machine):
    state = machine.transition(machine.start(), Event.EXIT_SESSION)

    assert state.stage is Stage.EXITED
    assert state.is_terminal
    with pytest.raises(InvalidTransitionError, match="会话已经结束"):
        machine.transition(state, Event.SKIP_STAGE)


def test_hint_is_not_available_before_practice(machine):
    state = machine.start()

    with pytest.raises(InvalidTransitionError):
        machine.transition(state, Event.HINT_REQUESTED)

    assert state.stage is Stage.GOAL_CLARIFICATION


def test_explanation_command_can_return_to_concept_without_mastery(machine):
    state = advance_to_guided(machine)
    state = machine.transition(state, Event.EXPLANATION_REQUESTED)

    assert state.stage is Stage.CONCEPT_EXPLANATION
    assert state.events[-1] is Event.EXPLANATION_REQUESTED
    assert not state.mastery_was_passed


def test_practice_command_starts_guided_practice_and_resets_hint_level(machine):
    state = advance_to_mastery(machine)
    state = machine.transition(state, Event.HINT_REQUESTED)
    assert state.hint_level == 1

    state = machine.transition(state, Event.PRACTICE_REQUESTED)

    assert state.stage is Stage.GUIDED_PRACTICE
    assert state.hint_level == 0
    assert not state.mastery_was_passed


def test_summary_command_does_not_mark_mastery(machine):
    state = advance_to_guided(machine)

    state = machine.transition(state, Event.SUMMARY_REQUESTED)

    assert state.stage is Stage.SUMMARY
    assert not state.mastery_was_passed
