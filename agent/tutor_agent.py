"""Application boundary for learner-facing tutoring and answer review."""

from typing import Any

from agent.prompts import build_assessment_messages, build_messages
from agent.schemas import (
    AssessmentRequest,
    TutorAssessment,
    TutorIntent,
    TutorRequest,
    TutorResponse,
)
from learning.evidence import AnswerAssessment


TOKEN_BUDGETS: dict[TutorIntent, int] = {
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


class TutorAgent:
    """Turn validated requests into safe model calls and validated responses."""

    def __init__(self, client: Any) -> None:
        if not callable(getattr(client, "complete_json", None)):
            raise TypeError("client must provide complete_json(messages).")
        self._client = client

    def respond(self, request: TutorRequest) -> TutorResponse:
        """Generate one learner-facing hint, exercise, explanation, or response."""
        if not isinstance(request, TutorRequest):
            raise TypeError("request must be a TutorRequest value.")
        raw = self._client.complete_json(
            build_messages(request),
            max_tokens=TOKEN_BUDGETS[request.intent],
        )
        return TutorResponse.from_json(raw)

    def assess_answer(self, request: AssessmentRequest) -> AnswerAssessment:
        """Return correctness and reasoning evidence, never a mastery decision."""
        if not isinstance(request, AssessmentRequest):
            raise TypeError("request must be an AssessmentRequest value.")
        raw = self._client.complete_json(
            build_assessment_messages(request),
            max_tokens=512,
        )
        return TutorAssessment.from_json(raw).assessment
