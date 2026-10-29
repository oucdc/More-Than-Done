import json

import pytest

from agent.schemas import AssessmentRequest, TutorIntent, TutorRequest, TutorResponse
from agent.tutor_agent import TOKEN_BUDGETS, TutorAgent
from learning.progress import AnswerCorrectness, ReasoningQuality


class FakeTutorClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []
        self.max_tokens = []

    def complete_json(self, messages, *, max_tokens):
        self.messages.append(messages)
        self.max_tokens.append(max_tokens)
        return next(self.responses)


def test_agent_returns_validated_tutor_response():
    client = FakeTutorClient(['{"message":"Try a common denominator.","follow_up_question":null}'])
    agent = TutorAgent(client)

    result = agent.respond(
        TutorRequest(
            intent=TutorIntent.HINT,
            goal="Learn fractions",
            exercise="1/2 + 1/4",
            hint_level=1,
        )
    )

    assert isinstance(result, TutorResponse)
    assert result.message == "Try a common denominator."
    assert "L1" in client.messages[0][0]["content"]
    assert client.max_tokens == [TOKEN_BUDGETS[TutorIntent.HINT]]


def test_agent_returns_assessment_evidence_for_python_rules():
    client = FakeTutorClient(
        [
            json.dumps(
                {
                    "correctness": "correct",
                    "reasoning_quality": "sound",
                    "evidence": "The denominators are aligned correctly.",
                    "feedback": "Your explanation matches the steps.",
                }
            )
        ]
    )
    agent = TutorAgent(client)

    result = agent.assess_answer(
        AssessmentRequest(
            goal="Learn fractions",
            exercise="1/2 + 1/4",
            learner_answer="3/4, using 2/4 + 1/4",
            success_criteria="Use a common denominator and explain the conversion.",
            reference_answer="3/4",
        )
    )

    assert result.correctness is AnswerCorrectness.CORRECT
    assert result.reasoning_quality is ReasoningQuality.SOUND
    assert "mastery" not in client.messages[0][0]["content"]
    assert (
        '"success_criteria": "Use a common denominator and explain the conversion."'
        in client.messages[0][1]["content"]
    )
    assert '"reference_answer": "3/4"' in client.messages[0][1]["content"]
    assert client.max_tokens == [512]


def test_intent_token_budgets_are_applied():
    assert TOKEN_BUDGETS == {
        TutorIntent.GOAL_CLARIFICATION: 256,
        TutorIntent.BASELINE_ASSESSMENT: 512,
        TutorIntent.CONCEPT_EXPLANATION: 1024,
        TutorIntent.GUIDED_PRACTICE: 512,
        TutorIntent.HINT: 384,
        TutorIntent.FEEDBACK: 512,
        TutorIntent.INDEPENDENT_PRACTICE: 768,
        TutorIntent.MASTERY_CHECK: 768,
        TutorIntent.SUMMARY: 1024,
        TutorIntent.ANSWER: 1024,
    }
    assert TOKEN_BUDGETS[TutorIntent.CONCEPT_EXPLANATION] > TOKEN_BUDGETS[TutorIntent.HINT]
    assert TOKEN_BUDGETS[TutorIntent.INDEPENDENT_PRACTICE] >= 768
    assert TOKEN_BUDGETS[TutorIntent.MASTERY_CHECK] >= 768


def test_agent_rejects_invalid_request_types():
    with pytest.raises(TypeError, match="complete_json"):
        TutorAgent(object())
