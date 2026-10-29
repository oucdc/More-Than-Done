import pytest

from learning import evidence
from learning.evidence import (
    AnswerAssessment,
    AnswerCorrectness,
    PracticeAttempt,
    PracticeKind,
    ReasoningQuality,
)
from learning.progress import (
    AnswerAssessment as ProgressAnswerAssessment,
    AnswerCorrectness as ProgressAnswerCorrectness,
    PracticeAttempt as ProgressPracticeAttempt,
    PracticeKind as ProgressPracticeKind,
    ReasoningQuality as ProgressReasoningQuality,
)
from learning.state import LearningEvent, LearningStage, LearningState


def test_initial_state_starts_at_goal_clarification():
    state = LearningState()

    assert state.stage is LearningStage.GOAL_CLARIFICATION
    assert state.goal is None
    assert state.hint_level == 0
    assert not state.is_terminal
    assert not state.mastery_was_passed


def test_state_normalizes_goal_and_event_sequences():
    state = LearningState(
        goal="  Learn algebra  ",
        events=[LearningEvent.ANSWER_REQUESTED],
    )

    assert state.goal == "Learn algebra"
    assert state.events == (LearningEvent.ANSWER_REQUESTED,)
    assert state.answer_was_requested


@pytest.mark.parametrize("hint_level", [-1, 5, True, "2"])
def test_state_rejects_invalid_hint_levels(hint_level):
    with pytest.raises(ValueError):
        LearningState(hint_level=hint_level)


def test_state_requires_enum_values_for_stage_and_events():
    with pytest.raises(TypeError, match="LearningStage"):
        LearningState(stage="goal_clarification")

    with pytest.raises(TypeError, match="LearningEvent"):
        LearningState(events=("answer_requested",))


def test_state_normalizes_active_exercise_and_round_trips_it():
    state = LearningState(active_exercise="  Solve x + 2 = 5  ")

    assert state.active_exercise == "Solve x + 2 = 5"
    assert LearningState.from_dict(state.to_dict()) == state


def test_evidence_types_are_reexported_from_progress_for_compatibility():
    assert ProgressAnswerAssessment is evidence.AnswerAssessment is AnswerAssessment
    assert ProgressAnswerCorrectness is evidence.AnswerCorrectness is AnswerCorrectness
    assert ProgressPracticeAttempt is evidence.PracticeAttempt is PracticeAttempt
    assert ProgressPracticeKind is evidence.PracticeKind is PracticeKind
    assert ProgressReasoningQuality is evidence.ReasoningQuality is ReasoningQuality


def test_state_normalizes_scoring_metadata_and_round_trips_v4_fields():
    state = LearningState(
        active_success_criteria="  Explain the common denominator.  ",
        active_reference_answer="  3/4  ",
        recent_exercises=("First question", "Second question"),
    )

    assert state.active_success_criteria == "Explain the common denominator."
    assert state.active_reference_answer == "3/4"
    assert LearningState.from_dict(state.to_dict()) == state


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.pop("learner_level"), "missing required fields"),
        (lambda payload: payload.update(unrecognized=True), "unsupported fields"),
        (lambda payload: payload.update(recent_exercises=["q"] * 11), "at most 10"),
    ],
)
def test_state_rejects_invalid_v4_payload_fields(mutate, message):
    payload = LearningState().to_dict()
    mutate(payload)

    with pytest.raises(ValueError, match=message):
        LearningState.from_dict(payload)
