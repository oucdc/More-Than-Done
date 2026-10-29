import pytest

from agent.schemas import (
    AssessmentRequest,
    AssessmentResponseError,
    TutorAssessment,
    TutorIntent,
    TutorRequest,
    TutorResponse,
    TutorResponseError,
)
from learning.progress import AnswerCorrectness, ReasoningQuality


def test_goal_clarification_can_start_without_a_goal():
    request = TutorRequest(intent=TutorIntent.GOAL_CLARIFICATION)

    assert request.to_payload()["goal"] is None


def test_request_normalizes_text_and_serializes_intent():
    request = TutorRequest(
        intent=TutorIntent.FEEDBACK,
        goal="  Learn fractions  ",
        exercise="  1/2 + 1/4  ",
        learner_answer="  3/4  ",
    )

    assert request.goal == "Learn fractions"
    assert request.exercise == "1/2 + 1/4"
    assert request.to_payload()["intent"] == "feedback"


def test_request_requires_goal_except_for_goal_clarification():
    with pytest.raises(ValueError, match="goal is required"):
        TutorRequest(intent=TutorIntent.CONCEPT_EXPLANATION)


@pytest.mark.parametrize("hint_level", [None, 0, 5, True, "2"])
def test_hint_request_requires_valid_level(hint_level):
    with pytest.raises(ValueError, match="hint_level"):
        TutorRequest(
            intent=TutorIntent.HINT,
            goal="Learn fractions",
            exercise="1/2 + 1/4",
            hint_level=hint_level,
        )


def test_hint_level_is_rejected_for_other_intents():
    with pytest.raises(ValueError, match="only valid for the hint"):
        TutorRequest(
            intent=TutorIntent.CONCEPT_EXPLANATION,
            goal="Learn fractions",
            hint_level=1,
        )


def test_feedback_requires_exercise_and_learner_answer():
    with pytest.raises(ValueError, match="learner_answer"):
        TutorRequest(
            intent=TutorIntent.FEEDBACK,
            goal="Learn fractions",
            exercise="1/2 + 1/4",
        )


def test_response_parses_learner_facing_text():
    result = TutorResponse.from_json(
        '{"message":" Try a common denominator. ","follow_up_question":"What denominator works?"}'
    )

    assert result.message == "Try a common denominator."
    assert result.follow_up_question == "What denominator works?"


def test_response_normalizes_empty_follow_up_to_none():
    result = TutorResponse.from_json('{"message":"Good effort.","follow_up_question":"  "}')

    assert result.follow_up_question is None


def test_response_parses_and_normalizes_assessment_metadata():
    result = TutorResponse.from_json(
        '{"message":"Problem","success_criteria":" Use a common denominator. ",'
        '"reference_answer":" 3/4 "}'
    )

    assert result.success_criteria == "Use a common denominator."
    assert result.reference_answer == "3/4"


def test_response_normalizes_blank_assessment_metadata_to_none():
    result = TutorResponse.from_json(
        '{"message":"Problem","success_criteria":"  ","reference_answer":null}'
    )

    assert result.success_criteria is None
    assert result.reference_answer is None


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        "{}",
        '{"message":"  "}',
        '{"message":"Good","mastery":true}',
        '{"message":"Good","follow_up_question":42}',
        '{"message":"Good","success_criteria":42}',
        '{"message":"Good","reference_answer":false}',
    ],
)
def test_response_rejects_invalid_shapes(raw):
    with pytest.raises(TutorResponseError):
        TutorResponse.from_json(raw)


def test_assessment_request_requires_problem_and_answer():
    request = AssessmentRequest(
        goal="Learn fractions",
        exercise="1/2 + 1/4",
        learner_answer="3/4",
    )

    assert request.to_payload()["learner_answer"] == "3/4"


def test_request_serializes_recent_exercises_as_json_array():
    request = TutorRequest(
        intent=TutorIntent.MASTERY_CHECK,
        goal="Learn fractions",
        recent_exercises=("First question", "Second question"),
    )

    assert request.to_payload()["recent_exercises"] == ["First question", "Second question"]


def test_request_serializes_recent_attempts_as_json_array_and_defaults_empty():
    empty = TutorRequest(intent=TutorIntent.CONCEPT_EXPLANATION, goal="Learn fractions")
    request = TutorRequest(
        intent=TutorIntent.CONCEPT_EXPLANATION,
        goal="Learn fractions",
        recent_attempts=("guided:incorrect/unsound:hint1:viewed0",),
    )

    assert empty.to_payload()["recent_attempts"] == []
    assert request.to_payload()["recent_attempts"] == [
        "guided:incorrect/unsound:hint1:viewed0"
    ]


@pytest.mark.parametrize(
    "recent_exercises",
    [
        ["question"],
        ("question", 1),
        tuple(str(index) for index in range(11)),
    ],
)
def test_request_rejects_invalid_or_too_many_recent_exercises(recent_exercises):
    with pytest.raises((TypeError, ValueError), match="recent_exercises"):
        TutorRequest(
            intent=TutorIntent.INDEPENDENT_PRACTICE,
            goal="Learn fractions",
            recent_exercises=recent_exercises,
        )


@pytest.mark.parametrize(
    "recent_attempts",
    [
        ["guided:incorrect/unsound:hint1:viewed0"],
        ("valid", 1),
        tuple(f"attempt-{index}" for index in range(6)),
    ],
)
def test_request_rejects_invalid_or_too_many_recent_attempts(recent_attempts):
    with pytest.raises((TypeError, ValueError), match="recent_attempts"):
        TutorRequest(
            intent=TutorIntent.CONCEPT_EXPLANATION,
            goal="Learn fractions",
            recent_attempts=recent_attempts,
        )


def test_reference_answer_is_limited_to_answer_intent():
    request = TutorRequest(
        intent=TutorIntent.ANSWER,
        goal="Learn fractions",
        exercise="1/2 + 1/4",
        reference_answer="3/4",
    )

    assert request.to_payload()["reference_answer"] == "3/4"
    with pytest.raises(ValueError, match="only valid for the answer"):
        TutorRequest(
            intent=TutorIntent.HINT,
            goal="Learn fractions",
            exercise="1/2 + 1/4",
            hint_level=1,
            reference_answer="3/4",
        )


def test_assessment_response_parses_only_correctness_and_reasoning_evidence():
    result = TutorAssessment.from_json(
        '{"correctness":"correct","reasoning_quality":"sound",'
        '"evidence":"Uses a common denominator.","feedback":"Correct; explain why."}'
    )

    assert result.assessment.correctness is AnswerCorrectness.CORRECT
    assert result.assessment.reasoning_quality is ReasoningQuality.SOUND
    assert result.assessment.feedback == "Correct; explain why."


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"correctness":"correct","reasoning_quality":"sound",'
        '"evidence":"x","feedback":"y","mastery":true}',
        '{"correctness":"great","reasoning_quality":"sound",'
        '"evidence":"x","feedback":"y"}',
        '{"correctness":"correct","reasoning_quality":"sound",'
        '"evidence":"","feedback":"y"}',
    ],
)
def test_assessment_response_rejects_untrusted_or_incomplete_shapes(raw):
    with pytest.raises(AssessmentResponseError):
        TutorAssessment.from_json(raw)
