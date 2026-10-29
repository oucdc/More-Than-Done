import json

import pytest

import agent.llm_client as llm_client_module
from agent.errors import LLMConnectionError
from agent.schemas import (
    AssessmentResponseError,
    TutorIntent,
    TutorResponse,
    TutorResponseError,
)
from cli import EXIT_CONFIG, EXIT_DATA, EXIT_MODEL, EXIT_OK, MoreThanDoneCLI, main
from learning.progress import (
    AnswerAssessment,
    AnswerCorrectness,
    PracticeAttempt,
    PracticeKind,
    ReasoningQuality,
)
from learning.state import LearningEvent, LearningStage, LearningState
from learning.state_machine import TRANSITIONS
from storage.json_store import JsonLearningStore, LearningStoreWriteError


def good_assessment():
    return AnswerAssessment(
        correctness=AnswerCorrectness.CORRECT,
        reasoning_quality=ReasoningQuality.SOUND,
        feedback="回答正确，推理步骤清楚。",
        evidence="答案和说明符合题目要求。",
    )


class FakeTutor:
    def __init__(self, responses=None, assessments=None, error_on=None, failure=None):
        self.responses = {key: list(value) for key, value in (responses or {}).items()}
        self.assessments = list(assessments or [])
        self.error_on = error_on
        self.failure = failure or LLMConnectionError("network unavailable")
        self.responded = []
        self.assessed = []

    def respond(self, request):
        self.responded.append(request)
        if request.intent is self.error_on:
            raise self.failure
        queue = self.responses.get(request.intent, [])
        response = queue.pop(0) if queue else f"{request.intent.value} response"
        return response if isinstance(response, TutorResponse) else TutorResponse(message=response)

    def assess_answer(self, request):
        self.assessed.append(request)
        return self.assessments.pop(0) if self.assessments else good_assessment()


def test_cli_runs_and_persists_a_complete_learning_flow(tmp_path):
    tutor = FakeTutor(
        responses={
            TutorIntent.BASELINE_ASSESSMENT: ["诊断题：把 1/2 写成分母为 4 的分数。"],
            TutorIntent.CONCEPT_EXPLANATION: ["分母表示平均分成几份。"],
            TutorIntent.INDEPENDENT_PRACTICE: ["引导题：计算 1/2 + 1/4。", "独立题：计算 1/3 + 1/6。"],
            TutorIntent.GUIDED_PRACTICE: ["先把两个分数化成相同分母。"],
            TutorIntent.MASTERY_CHECK: ["迁移题：计算 2/5 + 1/10。"],
            TutorIntent.SUMMARY: ["你能通分并解释分数相加的步骤。"],
        },
        assessments=[good_assessment() for _ in range(4)],
    )
    store = JsonLearningStore(tmp_path / "learning.json")
    output = []
    answers = iter(
        [
            "Learn fractions",
            "我会把 1/2 变成 2/4。",
            "2/4 + 1/4 = 3/4。",
            "独立练习",
            "1/3 + 1/6 = 1/2，因为 1/3 等于 2/6。",
            "检验",
            "2/5 + 1/10 = 1/2，因为 2/5 等于 4/10。",
        ]
    )
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    result = cli.run()
    saved_text = store.path.read_text(encoding="utf-8")
    saved_document = json.loads(saved_text)

    assert result.stage is LearningStage.COMPLETED
    assert cli.exit_code == EXIT_OK
    assert result.mastery_was_passed
    assert len(result.practice_attempts) == 3
    assert LearningEvent.MASTERY_PASSED in result.events
    assert saved_document["schema_version"] == 4
    assert saved_document["state"]["active_exercise"] is None
    assert any("掌握度已验证" in line for line in output)
    assert any("学习总结" in line for line in output)
    assert len(tutor.assessed) == 4
    assert all("learner_level" in request.to_payload() for request in tutor.responded)
    assert tutor.responded[0].learner_level is None
    assert all(
        request.learner_level == "已能正确作答且推理完整"
        for request in tutor.responded[1:]
        if request.intent is not TutorIntent.GOAL_CLARIFICATION
    )
    assert "我会把 1/2 变成 2/4。" not in saved_text
    assert "1/3 + 1/6 = 1/2" not in saved_text


def test_cli_resumes_saved_practice_and_handles_exit_command(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(LearningState(
        stage=LearningStage.INDEPENDENT_PRACTICE,
        goal="Learn fractions",
        active_exercise="计算 1/2 + 1/4。",
    ))
    tutor = FakeTutor(assessments=[good_assessment()])
    output = []
    answers = iter(["3/4，因为 1/2 等于 2/4。", "退出"])
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    result = cli.run()

    assert result.stage is LearningStage.EXITED
    assert len(result.practice_attempts) == 1
    assert result.practice_attempts[0].kind.value == "independent"
    assert any("继续独立练习" in line for line in output)
    assert store.load() == result


def test_cli_hint_and_answer_commands_are_recorded_and_never_count_as_independent(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(LearningState(
        stage=LearningStage.GUIDED_PRACTICE,
        goal="Learn fractions",
        active_exercise="计算 1/2 + 1/4。",
        active_reference_answer="3/4 because 1/2 is 2/4.",
    ))
    tutor = FakeTutor(
        responses={
            TutorIntent.HINT: ["先检查分母。"],
            TutorIntent.ANSWER: ["答案是 3/4。"],
            TutorIntent.SUMMARY: ["这次练习使用了提示和答案。"],
        },
        assessments=[good_assessment()],
    )
    answers = iter(["提示", "答案", "3/4", "总结"])
    output = []
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    result = cli.run()

    assert result.stage is LearningStage.COMPLETED
    assert LearningEvent.HINT_REQUESTED in result.events
    assert LearningEvent.ANSWER_REQUESTED in result.events
    assert result.practice_attempts[0].hint_level == 1
    assert result.practice_attempts[0].answer_viewed
    assert not result.mastery_was_passed
    assert any("L1 提示" in line for line in output)
    assert any("参考答案" in line for line in output)
    answer_request = next(request for request in tutor.responded if request.intent is TutorIntent.ANSWER)
    assert answer_request.reference_answer == "3/4 because 1/2 is 2/4."


def test_hint_without_a_saved_exercise_does_not_advance_hint_level(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(LearningState(
        stage=LearningStage.FEEDBACK,
        goal="Learn fractions",
    ))
    answers = iter(["提示", "退出"])
    output = []
    cli = MoreThanDoneCLI(
        FakeTutor(),
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    result = cli.run()

    assert result.stage is LearningStage.EXITED
    assert result.hint_level == 0
    assert LearningEvent.HINT_REQUESTED not in result.events
    assert any("没有可以提示" in line for line in output)


def test_cli_keeps_confirmed_goal_when_the_model_is_unavailable(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    tutor = FakeTutor(error_on=TutorIntent.BASELINE_ASSESSMENT)
    output = []
    answers = iter(["Learn algebra"])
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    result = cli.run()

    assert result.stage is LearningStage.BASELINE_ASSESSMENT
    assert cli.exit_code == EXIT_MODEL
    assert result.goal == "Learn algebra"
    assert store.load() == result
    assert any("重新运行即可继续" in line for line in output)


def test_cli_handles_invalid_model_json_without_losing_the_saved_stage(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    tutor = FakeTutor(
        error_on=TutorIntent.BASELINE_ASSESSMENT,
        failure=TutorResponseError("模型回复不是有效 JSON。"),
    )
    answers = iter(["Learn algebra"])
    output = []
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    result = cli.run()

    assert result.stage is LearningStage.BASELINE_ASSESSMENT
    assert cli.exit_code == EXIT_MODEL
    assert store.load() == result
    assert any("模型回复格式无法识别" in line for line in output)


def test_starting_a_new_session_archives_the_completed_record(tmp_path):
    store = JsonLearningStore(tmp_path / "learning_state.json")
    completed = LearningState(
        stage=LearningStage.COMPLETED,
        goal="Previously completed goal",
        baseline_result="correct:sound",
        learner_level="已能正确作答且推理完整",
    )
    store.save(completed)
    original = store.path.read_bytes()
    output = []
    cli = MoreThanDoneCLI(
        FakeTutor(),
        store=store,
        input_fn=lambda _prompt: "",
        output_fn=output.append,
    )
    cli.state = completed

    assert cli._offer_new_session()

    archived_files = list((tmp_path / "archive").glob("*.json"))
    assert len(archived_files) == 1
    assert archived_files[0].read_bytes() == original
    assert store.load() == LearningState()
    assert any("上一次学习记录已归档到" in line for line in output)


def test_archive_failure_keeps_old_session_and_sets_data_exit_code(tmp_path, monkeypatch):
    store = JsonLearningStore(tmp_path / "learning_state.json")
    completed = LearningState(stage=LearningStage.COMPLETED, goal="Keep this record")
    store.save(completed)
    original = store.path.read_bytes()
    output = []
    cli = MoreThanDoneCLI(
        FakeTutor(),
        store=store,
        input_fn=lambda _prompt: "",
        output_fn=output.append,
    )
    cli.state = completed

    def fail_archive():
        raise LearningStoreWriteError("archive write failed")

    monkeypatch.setattr(store, "archive", fail_archive)

    assert not cli._offer_new_session()

    assert cli.state == completed
    assert store.path.read_bytes() == original
    assert store.load() == completed
    assert cli.exit_code == EXIT_DATA
    assert any("无法归档上一次学习记录" in line for line in output)


def test_first_new_session_does_not_create_an_archive_folder(tmp_path):
    store = JsonLearningStore(tmp_path / "learning_state.json")
    completed = LearningState(stage=LearningStage.COMPLETED, goal="No prior file")
    cli = MoreThanDoneCLI(
        FakeTutor(),
        store=store,
        input_fn=lambda _prompt: "",
        output_fn=lambda _line: None,
    )
    cli.state = completed

    assert cli._offer_new_session()

    assert not (tmp_path / "archive").exists()
    assert store.load() == LearningState()


def test_declining_new_session_preserves_completed_record_without_archiving(tmp_path):
    store = JsonLearningStore(tmp_path / "learning_state.json")
    completed = LearningState(stage=LearningStage.COMPLETED, goal="Keep this record")
    store.save(completed)
    original = store.path.read_bytes()
    output = []
    cli = MoreThanDoneCLI(
        FakeTutor(),
        store=store,
        input_fn=lambda _prompt: "q",
        output_fn=output.append,
    )
    cli.state = completed

    assert not cli._offer_new_session()

    assert cli.state == completed
    assert store.path.read_bytes() == original
    assert store.load() == completed
    assert not (tmp_path / "archive").exists()


def test_skip_goal_uses_fallback_and_exit_is_stable(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    tutor = FakeTutor({TutorIntent.BASELINE_ASSESSMENT: ["诊断问题"]})
    answers = iter(["跳过", "退出"])
    output = []
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    result = cli.run()

    assert result.stage is LearningStage.EXITED
    assert result.goal == "自主探索"
    assert result.skipped_stages == (LearningStage.GOAL_CLARIFICATION,)
    skip_output = next(line for line in output if "已跳过当前阶段" in line)
    assert "基础诊断" in skip_output
    assert "baseline_assessment" not in skip_output


def test_corrupt_store_sets_data_exit_code_and_preserves_original(tmp_path):
    path = tmp_path / "learning.json"
    original = b"{broken"
    path.write_bytes(original)
    output = []
    cli = MoreThanDoneCLI(
        FakeTutor(),
        store=JsonLearningStore(path),
        output_fn=output.append,
    )

    cli.run()

    assert cli.exit_code == EXIT_DATA
    assert path.read_bytes() == original
    assert any("学习记录无法读取或保存" in line for line in output)


def test_assessment_response_error_preserves_progress_and_sets_model_exit_code(tmp_path):
    class InvalidAssessmentTutor(FakeTutor):
        def assess_answer(self, request):
            self.assessed.append(request)
            raise AssessmentResponseError("模型评价不是有效 JSON。")

    store = JsonLearningStore(tmp_path / "learning.json")
    original_state = LearningState(
        stage=LearningStage.GUIDED_PRACTICE,
        goal="Learn fractions",
        active_exercise="Calculate 1/2 + 1/4",
    )
    store.save(original_state)
    output = []
    cli = MoreThanDoneCLI(
        InvalidAssessmentTutor(),
        store=store,
        input_fn=lambda _prompt: "3/4 because 1/2 is 2/4",
        output_fn=output.append,
    )

    result = cli.run()

    assert cli.exit_code == EXIT_MODEL
    assert result == original_state
    assert store.load() == original_state
    assert not result.practice_attempts
    assert any("模型回复格式无法识别" in line for line in output)


def test_mid_session_store_write_error_is_caught_and_saved_progress_is_preserved(
    tmp_path, monkeypatch
):
    store = JsonLearningStore(tmp_path / "learning.json")
    original_state = LearningState(
        stage=LearningStage.GUIDED_PRACTICE,
        goal="Learn fractions",
        active_exercise="Calculate 1/2 + 1/4",
    )
    store.save(original_state)
    output = []
    cli = MoreThanDoneCLI(
        FakeTutor(assessments=[good_assessment()]),
        store=store,
        input_fn=lambda _prompt: "3/4 because 1/2 is 2/4",
        output_fn=output.append,
    )

    def fail_save(_state):
        raise LearningStoreWriteError("disk unavailable")

    monkeypatch.setattr(store, "save", fail_save)

    result = cli.run()

    assert cli.exit_code == EXIT_DATA
    assert result.stage is LearningStage.FEEDBACK
    assert store.load() == original_state
    assert any("学习记录无法读取或保存" in line for line in output)


def test_feedback_uses_chinese_progress_label(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(LearningState(
        stage=LearningStage.FEEDBACK,
        goal="Learn fractions",
        practice_attempts=(PracticeAttempt(
            kind=PracticeKind.GUIDED,
            correctness=AnswerCorrectness.INCORRECT,
            reasoning_quality=ReasoningQuality.UNSOUND,
            hint_level=0,
            answer_viewed=False,
        ),),
    ))
    answers = iter(["退出"])
    output = []
    cli = MoreThanDoneCLI(
        FakeTutor(),
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    cli.run()

    progress_line = next(line for line in output if "当前进度" in line)
    assert "尚未验证掌握程度" in progress_line
    assert "unverified" not in progress_line


def test_generated_scoring_metadata_reaches_assessment_without_leaking_to_practice_output(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(LearningState(
        stage=LearningStage.INDEPENDENT_PRACTICE,
        goal="Learn fractions",
    ))
    reference_answer = "private reference result 3/4"
    tutor = FakeTutor(responses={
        TutorIntent.INDEPENDENT_PRACTICE: [TutorResponse(
            message="Calculate 1/2 + 1/4.",
            success_criteria="Use a common denominator and explain the addition.",
            reference_answer=reference_answer,
        )],
    })
    answers = iter(["3/4, because 1/2 is 2/4.", "退出"])
    output = []
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    cli.run()

    assert tutor.assessed[0].success_criteria == "Use a common denominator and explain the addition."
    assert tutor.assessed[0].reference_answer == reference_answer
    assert reference_answer not in "\n".join(output)
    generation_request = next(
        request for request in tutor.responded
        if request.intent is TutorIntent.INDEPENDENT_PRACTICE
    )
    assert generation_request.to_payload()["recent_exercises"] == []


def test_guided_exercise_output_does_not_reveal_response_metadata(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(LearningState(
        stage=LearningStage.GUIDED_PRACTICE,
        goal="Learn fractions",
    ))
    reference_answer = "private guided reference 7/8"
    success_criteria = "private criteria: explain equivalent fractions"
    tutor = FakeTutor(responses={
        TutorIntent.INDEPENDENT_PRACTICE: [TutorResponse(
            message="Calculate 3/8 + 1/2.",
            success_criteria=success_criteria,
            reference_answer=reference_answer,
        )],
        TutorIntent.GUIDED_PRACTICE: ["Convert to the same denominator first."],
    })
    answers = iter(["7/8, because 1/2 is 4/8.", "退出"])
    output = []
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    cli.run()

    displayed = "\n".join(output)
    assert reference_answer not in displayed
    assert success_criteria not in displayed
    assert tutor.assessed[0].reference_answer == reference_answer
    assert tutor.assessed[0].success_criteria == success_criteria


def _mastery_state_with_prior_success(recent_exercises=("Prior question",)):
    return LearningState(
        stage=LearningStage.MASTERY_CHECK,
        goal="Learn fractions",
        recent_exercises=recent_exercises,
        practice_attempts=(PracticeAttempt(
            kind=PracticeKind.INDEPENDENT,
            correctness=AnswerCorrectness.CORRECT,
            reasoning_quality=ReasoningQuality.SOUND,
            hint_level=0,
            answer_viewed=False,
        ),),
    )


def test_mastery_duplicate_retries_once_then_marks_repeated_and_blocks_pass(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(_mastery_state_with_prior_success())
    tutor = FakeTutor(responses={
        TutorIntent.MASTERY_CHECK: [
            TutorResponse(message="  PRIOR   question ", success_criteria="Show work", reference_answer="42"),
            TutorResponse(message="prior question", success_criteria="Show work", reference_answer="42"),
        ],
    }, assessments=[good_assessment()])
    answers = iter(["my solution", "退出"])
    output = []
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    result = cli.run()

    mastery_requests = [request for request in tutor.responded if request.intent is TutorIntent.MASTERY_CHECK]
    assert len(mastery_requests) == 2
    assert all(request.recent_exercises == ("Prior question",) for request in mastery_requests)
    assert result.mastery_exercise_repeated
    assert not result.mastery_was_passed
    assert any("本题与之前的练习重复" in line for line in output)
    assert any("重复" in line and "不会记为掌握" in line for line in output)
    assert tutor.assessed[0].success_criteria == "Show work"
    assert tutor.assessed[0].reference_answer == "42"


def test_distinct_mastery_question_uses_one_request_and_can_pass(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(_mastery_state_with_prior_success())
    tutor = FakeTutor(responses={
        TutorIntent.MASTERY_CHECK: [TutorResponse(
            message="A genuinely new transfer question",
            success_criteria="Show the steps.",
            reference_answer="42",
        )],
        TutorIntent.SUMMARY: ["You transferred the idea."],
    }, assessments=[good_assessment()])
    answers = iter(["my solution"])
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _line: None,
    )

    result = cli.run()

    mastery_requests = [request for request in tutor.responded if request.intent is TutorIntent.MASTERY_CHECK]
    assert len(mastery_requests) == 1
    assert mastery_requests[0].recent_exercises == ("Prior question",)
    assert result.mastery_was_passed
    assert not result.mastery_exercise_repeated


def test_mastery_duplicate_check_covers_all_ten_saved_exercises(tmp_path):
    recent = ("Prior question", *(f"Question {index}" for index in range(1, 10)))
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(_mastery_state_with_prior_success(recent))
    tutor = FakeTutor(responses={
        TutorIntent.MASTERY_CHECK: [
            TutorResponse(message="prior question"),
            TutorResponse(message="prior question"),
        ],
    }, assessments=[good_assessment()])
    answers = iter(["my solution", "退出"])
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _line: None,
    )

    result = cli.run()

    mastery_requests = [request for request in tutor.responded if request.intent is TutorIntent.MASTERY_CHECK]
    assert len(mastery_requests) == 2
    assert mastery_requests[0].recent_exercises == recent[-5:]
    assert result.mastery_exercise_repeated


def test_main_returns_configuration_exit_code_without_reading_real_dotenv(
    monkeypatch,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")

    assert main() == EXIT_CONFIG


def test_main_returns_cli_exit_code(monkeypatch):
    from config import Settings

    monkeypatch.setattr(
        Settings,
        "from_environment",
        classmethod(lambda cls: Settings(api_key="test-key")),
    )
    monkeypatch.setattr(llm_client_module, "LLMClient", lambda _settings: object())
    monkeypatch.setattr("cli.TutorAgent", lambda _client: object())

    class FakeCLI:
        exit_code = EXIT_OK

        def __init__(self, _tutor):
            pass

        def run(self):
            return None

    monkeypatch.setattr("cli.MoreThanDoneCLI", FakeCLI)

    assert main() == EXIT_OK


def test_commands_are_exact_local_mappings():
    assert MoreThanDoneCLI._command("提示") == "hint"
    assert MoreThanDoneCLI._command("/answer") == "answer"
    assert MoreThanDoneCLI._command("检验") == "mastery"
    assert MoreThanDoneCLI._command("a learner answer") is None


def test_independent_and_mastery_cli_guards_match_transition_table_for_every_stage(
    tmp_path,
):
    independent_attempt = PracticeAttempt(
        kind=PracticeKind.INDEPENDENT,
        correctness=AnswerCorrectness.CORRECT,
        reasoning_quality=ReasoningQuality.SOUND,
        hint_level=0,
        answer_viewed=False,
    )
    commands = {
        "independent": LearningEvent.FEEDBACK_READY_FOR_INDEPENDENT,
        "mastery": LearningEvent.FEEDBACK_READY_FOR_MASTERY,
    }

    for stage in LearningStage:
        for command, event in commands.items():
            state = LearningState(
                stage=stage,
                goal="Learn fractions",
                practice_attempts=(
                    (independent_attempt,)
                    if stage is LearningStage.FEEDBACK
                    else ()
                ),
            )
            store = JsonLearningStore(tmp_path / f"{stage.value}-{command}.json")
            output = []
            cli = MoreThanDoneCLI(
                FakeTutor(),
                store=store,
                output_fn=output.append,
            )
            cli.state = state
            expected = (stage, event) in TRANSITIONS

            handled = cli._handle_command(command)

            assert handled is expected, (stage, command)
            if expected:
                assert cli.state.stage is TRANSITIONS[(stage, event)]
                assert store.load() == cli.state
            else:
                assert cli.state == state


def test_failed_mastery_then_new_independent_practice_can_pass_retry(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(_mastery_state_with_prior_success(recent_exercises=("Earlier question",)))
    failed_assessment = AnswerAssessment(
        correctness=AnswerCorrectness.INCORRECT,
        reasoning_quality=ReasoningQuality.UNSOUND,
        feedback="Please revisit the steps.",
        evidence="The conversion is incorrect.",
    )
    tutor = FakeTutor(
        responses={
            TutorIntent.MASTERY_CHECK: [
                "A fresh mastery question",
                "A different mastery retry question",
            ],
            TutorIntent.INDEPENDENT_PRACTICE: ["A new independent question"],
            TutorIntent.SUMMARY: ["You completed the retry successfully."],
        },
        assessments=[failed_assessment, good_assessment(), good_assessment()],
    )
    answers = iter([
        "incorrect first attempt",
        "独立练习",
        "correct independent response",
        "检验",
        "correct mastery response",
    ])
    output = []
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    result = cli.run()

    assert result.stage is LearningStage.COMPLETED
    assert result.mastery_was_passed
    assert LearningEvent.MASTERY_NOT_PASSED in result.events
    assert LearningEvent.MASTERY_PASSED in result.events
    assert [attempt.kind for attempt in result.practice_attempts] == [
        PracticeKind.INDEPENDENT,
        PracticeKind.MASTERY,
        PracticeKind.INDEPENDENT,
        PracticeKind.MASTERY,
    ]
    assert sum(request.intent is TutorIntent.MASTERY_CHECK for request in tutor.responded) == 2
    assert any("本次尚未通过掌握度检验" in line for line in output)
    assert any("掌握度已验证" in line for line in output)


def test_specific_goal_is_accepted_without_goal_clarification_call(tmp_path):
    tutor = FakeTutor()
    cli = MoreThanDoneCLI(
        tutor,
        store=JsonLearningStore(tmp_path / "learning.json"),
        input_fn=lambda _prompt: "Learn differential equations",
        output_fn=lambda _line: None,
    )

    cli._run_goal()

    assert cli.state.goal == "Learn differential equations"
    assert all(request.intent is not TutorIntent.GOAL_CLARIFICATION for request in tutor.responded)


def test_ambiguous_goal_is_clarified_once_then_short_answer_is_accepted(tmp_path):
    tutor = FakeTutor(responses={
        TutorIntent.GOAL_CLARIFICATION: ["你最想学数学的哪一部分？"],
    })
    answers = iter(["数学?", "x"])
    output = []
    cli = MoreThanDoneCLI(
        tutor,
        store=JsonLearningStore(tmp_path / "learning.json"),
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    cli._run_goal()

    requests = [request for request in tutor.responded if request.intent is TutorIntent.GOAL_CLARIFICATION]
    assert len(requests) == 1
    assert requests[0].learner_answer == "数学?"
    assert cli.state.goal == "x"
    assert sum("你最想学数学的哪一部分？" in line for line in output) == 1


@pytest.mark.parametrize(
    ("failure", "command", "expected_stage"),
    [
        (LLMConnectionError("network unavailable"), "退出", LearningStage.EXITED),
        (TutorResponseError("invalid response"), "跳过", LearningStage.BASELINE_ASSESSMENT),
    ],
)
def test_goal_clarification_failure_falls_back_and_commands_still_work(
    tmp_path, failure, command, expected_stage
):
    tutor = FakeTutor(error_on=TutorIntent.GOAL_CLARIFICATION, failure=failure)
    answers = iter(["?", command])
    output = []
    cli = MoreThanDoneCLI(
        tutor,
        store=JsonLearningStore(tmp_path / "learning.json"),
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
    )

    cli._run_goal()

    assert cli.state.stage is expected_stage
    assert sum(request.intent is TutorIntent.GOAL_CLARIFICATION for request in tutor.responded) == 1
    assert any("暂时无法生成澄清问题" in line for line in output)


def test_baseline_result_is_saved_and_passed_to_following_requests(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    tutor = FakeTutor(
        responses={TutorIntent.BASELINE_ASSESSMENT: ["诊断题：解释分母的含义。"]},
        assessments=[good_assessment()],
        error_on=TutorIntent.CONCEPT_EXPLANATION,
    )
    answers = iter(["Learn fractions", "分母表示平均分成几份。"])
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _line: None,
    )

    result = cli.run()
    restored = store.load()

    expected_level = "已能正确作答且推理完整"
    assert result.stage is LearningStage.CONCEPT_EXPLANATION
    assert result.baseline_result == "correct:sound"
    assert result.learner_level == expected_level
    assert result.practice_attempts == ()
    assert restored.baseline_result == "correct:sound"
    assert restored.learner_level == expected_level
    explanation_request = next(
        request for request in tutor.responded
        if request.intent is TutorIntent.CONCEPT_EXPLANATION
    )
    assert explanation_request.learner_level == expected_level


def test_baseline_scoring_metadata_is_passed_to_review(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    criteria = "说明分母表示平均分成的份数。"
    reference = "分母表示整体被平均分成的份数。"
    tutor = FakeTutor(
        responses={TutorIntent.BASELINE_ASSESSMENT: [TutorResponse(
            message="诊断题：解释分母的含义。",
            success_criteria=criteria,
            reference_answer=reference,
        )]},
        assessments=[good_assessment()],
        error_on=TutorIntent.CONCEPT_EXPLANATION,
    )
    answers = iter(["Learn fractions", "分母表示平均分成几份。"])
    cli = MoreThanDoneCLI(
        tutor,
        store=store,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _line: None,
    )

    result = cli.run()

    assert result.baseline_result == "correct:sound"
    assert tutor.assessed[0].success_criteria == criteria
    assert tutor.assessed[0].reference_answer == reference
    assert store.load().baseline_result == "correct:sound"


def test_concept_request_includes_only_the_latest_five_attempt_summaries(tmp_path):
    attempts = tuple(PracticeAttempt(
        kind=PracticeKind.GUIDED,
        correctness=AnswerCorrectness.INCORRECT,
        reasoning_quality=ReasoningQuality.UNSOUND,
        hint_level=index % 5,
        answer_viewed=(index % 2 == 0),
    ) for index in range(6))
    state = LearningState(
        stage=LearningStage.CONCEPT_EXPLANATION,
        goal="Learn fractions",
        learner_level="基础薄弱，需要从概念讲起",
        practice_attempts=attempts,
    )
    tutor = FakeTutor()
    cli = MoreThanDoneCLI(
        tutor,
        store=JsonLearningStore(tmp_path / "learning.json"),
        output_fn=lambda _line: None,
    )
    cli.state = state

    cli._run_explanation()

    request = tutor.responded[0]
    assert len(request.recent_attempts) == 5
    assert request.recent_attempts == (
        "guided:incorrect/unsound:hint1:viewed0",
        "guided:incorrect/unsound:hint2:viewed1",
        "guided:incorrect/unsound:hint3:viewed0",
        "guided:incorrect/unsound:hint4:viewed1",
        "guided:incorrect/unsound:hint0:viewed0",
    )
    assert request.learner_level == "基础薄弱，需要从概念讲起"
