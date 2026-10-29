"""Interactive Windows-friendly CLI that connects tutoring and learning state."""

import json
from collections.abc import Callable

from agent.errors import LLMClientError
from agent.schemas import (
    AssessmentRequest,
    AssessmentResponseError,
    TutorIntent,
    TutorRequest,
    TutorResponseError,
)
from agent.tutor_agent import TutorAgent
from learning.evaluation import LearningEvaluator
from learning.progress import PROGRESS_LABELS, LearningProgress, summarize_attempts
from learning.state import STAGE_LABELS, LearningEvent, LearningStage, LearningState
from learning.state_machine import HintLimitReachedError, InvalidTransitionError, LearningStateMachine
from storage.json_store import JsonLearningStore, LearningStoreError


COMMANDS = {
    "提示": "hint", "/hint": "hint", "hint": "hint",
    "讲解": "explain", "/explain": "explain", "explain": "explain",
    "答案": "answer", "/answer": "answer", "answer": "answer",
    "跳过": "skip", "/skip": "skip", "skip": "skip",
    "练习": "practice", "/practice": "practice", "practice": "practice",
    "独立练习": "independent", "独立": "independent", "/independent": "independent",
    "检验": "mastery", "掌握度检验": "mastery", "/mastery": "mastery",
    "总结": "summary", "/summary": "summary", "summary": "summary",
    "退出": "exit", "/quit": "exit", "quit": "exit", "exit": "exit",
}

COMMAND_HELP = "命令：提示、讲解、答案、跳过、练习、独立练习、检验、总结、退出。"
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_DATA = 3
EXIT_MODEL = 4


class MoreThanDoneCLI:
    """Run one resumable learning session with injectable console and services."""

    def __init__(
        self,
        tutor: TutorAgent,
        *,
        store: JsonLearningStore | None = None,
        state_machine: LearningStateMachine | None = None,
        evaluator: LearningEvaluator | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], object] = print,
    ) -> None:
        self.tutor = tutor
        self.store = store or JsonLearningStore()
        self.machine = state_machine or LearningStateMachine()
        self.evaluator = evaluator or LearningEvaluator()
        self.input = input_fn
        self.output = output_fn
        self.state = LearningState()
        self.exit_code = EXIT_OK

    def run(self) -> LearningState:
        """Load saved progress, then continue until the learner exits or completes."""
        self.exit_code = EXIT_OK
        try:
            self.state = self.store.load_or_start()
            if self.state.is_terminal and not self._offer_new_session():
                return self.state
            if not self.state.is_terminal:
                self.output("More Than Done：一起练习，让你逐步学会独立解决问题。")
                self.output(COMMAND_HELP)

            while not self.state.is_terminal:
                self._run_current_stage()

            if self.state.stage is LearningStage.COMPLETED:
                self.output("本次学习已完成。学习记录已保存。")
            elif self.state.stage is LearningStage.EXITED:
                self.output("已退出本次学习。学习记录已保存。")
            return self.state
        except (EOFError, KeyboardInterrupt):
            self.output("已收到退出信号，正在保存当前学习进度。")
            try:
                self._exit_session()
            except LearningStoreError as exc:
                self.output(f"学习记录无法保存：{exc}")
                self.exit_code = EXIT_DATA
            return self.state
        except LLMClientError as exc:
            self.output(f"模型请求暂时无法完成：{exc}")
            self.output("已经保存的学习进度仍在本地；重新运行即可继续。")
            self.exit_code = EXIT_MODEL
            return self.state
        except (TutorResponseError, AssessmentResponseError) as exc:
            self.output(f"模型回复格式无法识别：{exc}")
            self.output("当前已保存的学习进度仍在本地；重新运行即可继续。")
            self.exit_code = EXIT_MODEL
            return self.state
        except LearningStoreError as exc:
            self.output(f"学习记录无法读取或保存：{exc}")
            self.exit_code = EXIT_DATA
            return self.state

    def _offer_new_session(self) -> bool:
        verb = "完成" if self.state.stage is LearningStage.COMPLETED else "退出"
        self.output(f"上一次学习已{verb}。按 Enter 开始新会话，输入 q 返回：")
        choice = self.input("> ").strip().casefold()
        if choice in {"q", "quit", "exit", "/quit", "退出"}:
            return False
        try:
            archived_path = self.store.archive()
        except LearningStoreError as exc:
            self.output(f"无法归档上一次学习记录：{exc}")
            self.exit_code = EXIT_DATA
            return False
        if archived_path is not None:
            self.output(f"上一次学习记录已归档到 {archived_path}")
        self.state = self.machine.start()
        self._save()
        return True

    def _run_current_stage(self) -> None:
        stage = self.state.stage
        if stage is LearningStage.GOAL_CLARIFICATION:
            self._run_goal()
        elif stage is LearningStage.BASELINE_ASSESSMENT:
            self._run_baseline()
        elif stage is LearningStage.CONCEPT_EXPLANATION:
            self._run_explanation()
        elif stage in {LearningStage.GUIDED_PRACTICE, LearningStage.INDEPENDENT_PRACTICE}:
            self._run_practice(stage)
        elif stage is LearningStage.FEEDBACK:
            self._run_feedback()
        elif stage is LearningStage.MASTERY_CHECK:
            self._run_mastery()
        elif stage is LearningStage.SUMMARY:
            self._run_summary()

    def _run_goal(self) -> None:
        self.output("你想学会什么？也可以输入“跳过”开始自主探索。")
        clarification_attempted = False
        while self.state.stage is LearningStage.GOAL_CLARIFICATION:
            raw = self.input("学习目标> ").strip()
            command = self._command(raw)
            if command:
                if self._handle_command(command):
                    return
                continue
            if raw.startswith("/"):
                self.output(f"未识别的命令。{COMMAND_HELP}")
                continue
            if not raw:
                self.output("请写下一个学习目标，或输入“跳过”。")
                continue
            if not clarification_attempted and self._is_ambiguous_goal(raw):
                clarification_attempted = True
                try:
                    response = self.tutor.respond(TutorRequest(
                        intent=TutorIntent.GOAL_CLARIFICATION,
                        learner_answer=raw,
                    ))
                except (LLMClientError, TutorResponseError):
                    self.output("暂时无法生成澄清问题，请直接写下学习目标。")
                else:
                    self.output(response.message)
                continue
            self._commit(self.machine.transition(
                self.state,
                LearningEvent.GOAL_CONFIRMED,
                goal=raw,
            ))

    def _run_baseline(self) -> None:
        if not self.state.active_exercise:
            request = TutorRequest(
                intent=TutorIntent.BASELINE_ASSESSMENT,
                goal=self._goal_text(),
                learner_level=self.state.learner_level,
            )
            response = self.tutor.respond(request)
            self._commit(self.machine.set_active_exercise(
                self.state,
                response.message,
                success_criteria=response.success_criteria,
                reference_answer=response.reference_answer,
            ))
        self.output(f"基础诊断：\n{self.state.active_exercise}")
        while self.state.stage is LearningStage.BASELINE_ASSESSMENT:
            raw = self.input("你的回答> ").strip()
            command = self._command(raw)
            if command:
                if self._handle_command(command):
                    return
                continue
            if raw.startswith("/"):
                self.output(f"未识别的命令。{COMMAND_HELP}")
                continue
            if not raw:
                self.output("先试着回答；也可以使用“提示”或“跳过”。")
                continue
            review = self.tutor.assess_answer(AssessmentRequest(
                goal=self._goal_text(),
                exercise=self.state.active_exercise or "基础诊断问题",
                learner_answer=raw,
                success_criteria=self.state.active_success_criteria,
                reference_answer=self.state.active_reference_answer,
            ))
            self.output(review.feedback)
            self._commit(self.machine.transition(
                self.state,
                LearningEvent.BASELINE_COMPLETED,
                assessment=review,
            ))
            return

    def _run_explanation(self) -> None:
        response = self.tutor.respond(TutorRequest(
            intent=TutorIntent.CONCEPT_EXPLANATION,
            goal=self._goal_text(),
            learner_level=self.state.learner_level,
            recent_attempts=self._recent_attempts(),
        ))
        self.output(f"概念讲解：\n{response.message}")
        if response.follow_up_question:
            self.output(response.follow_up_question)
        self._commit(self.machine.transition(self.state, LearningEvent.EXPLANATION_COMPLETED))

    def _run_practice(self, stage: LearningStage) -> None:
        intent = (
            TutorIntent.GUIDED_PRACTICE
            if stage is LearningStage.GUIDED_PRACTICE
            else TutorIntent.INDEPENDENT_PRACTICE
        )
        if not self.state.active_exercise:
            if stage is LearningStage.GUIDED_PRACTICE:
                problem = self.tutor.respond(TutorRequest(
                    intent=TutorIntent.INDEPENDENT_PRACTICE,
                    goal=self._goal_text(),
                    learner_level=self.state.learner_level,
                    recent_exercises=self.state.recent_exercises[-5:],
                    recent_attempts=self._recent_attempts(),
                ))
                self._commit(self.machine.set_active_exercise(
                    self.state,
                    problem.message,
                    success_criteria=problem.success_criteria,
                    reference_answer=problem.reference_answer,
                ))
                guidance = self.tutor.respond(TutorRequest(
                    intent=TutorIntent.GUIDED_PRACTICE,
                    goal=self._goal_text(),
                    learner_level=self.state.learner_level,
                    exercise=problem.message,
                    recent_attempts=self._recent_attempts(),
                ))
                self.output(f"引导练习：\n{problem.message}\n\n先试这一小步：\n{guidance.message}")
                if guidance.follow_up_question:
                    self.output(guidance.follow_up_question)
            else:
                problem = self.tutor.respond(TutorRequest(
                    intent=intent,
                    goal=self._goal_text(),
                    learner_level=self.state.learner_level,
                    recent_exercises=self.state.recent_exercises[-5:],
                    recent_attempts=self._recent_attempts(),
                ))
                self._commit(self.machine.set_active_exercise(
                    self.state,
                    problem.message,
                    success_criteria=problem.success_criteria,
                    reference_answer=problem.reference_answer,
                ))
                self.output(f"独立练习：\n{problem.message}")
        else:
            label = "引导练习" if stage is LearningStage.GUIDED_PRACTICE else "独立练习"
            self.output(f"继续{label}：\n{self.state.active_exercise}")

        while self.state.stage is stage:
            raw = self.input("你的回答（或输入命令）> ").strip()
            command = self._command(raw)
            if command:
                if self._handle_command(command):
                    return
                continue
            if raw.startswith("/"):
                self.output(f"未识别的命令。{COMMAND_HELP}")
                continue
            if not raw:
                self.output("请先尝试作答，或使用“提示”“答案”“跳过”等命令。")
                continue

            exercise = self.state.active_exercise or "本轮练习"
            assessment = self.tutor.assess_answer(AssessmentRequest(
                goal=self._goal_text(),
                exercise=exercise,
                learner_answer=raw,
                success_criteria=self.state.active_success_criteria,
                reference_answer=self.state.active_reference_answer,
            ))
            self._commit(self.evaluator.submit_answer(self.machine, self.state, assessment))
            self.output(assessment.feedback)
            if stage is LearningStage.MASTERY_CHECK:
                self._record_mastery_result()
            return

    def _run_feedback(self) -> None:
        progress = LearningProgress.from_state(self.state)
        self.output(
            "练习记录："
            f"{progress.guided_attempt_count} 次引导练习，"
            f"{progress.independent_attempt_count} 次独立练习；"
            f"当前进度：{PROGRESS_LABELS[progress.status]}。"
        )
        self.output("可输入“练习”继续引导练习、“独立练习”开始新题，或输入“检验”检查掌握度。")
        while self.state.stage is LearningStage.FEEDBACK:
            raw = self.input("下一步> ").strip()
            command = self._command(raw)
            if command:
                if self._handle_command(command):
                    return
                continue
            if raw.startswith("/"):
                self.output(f"未识别的命令。{COMMAND_HELP}")
            else:
                self.output(f"请输入一个命令。{COMMAND_HELP}")

    def _run_mastery(self) -> None:
        if self.state.mastery_answer_submitted:
            self._record_mastery_result()
            return
        if not self.state.active_exercise:
            recent = self.state.recent_exercises[-5:]
            known_exercises = {
                self._normalize_exercise(exercise)
                for exercise in self.state.recent_exercises
            }
            response = self.tutor.respond(TutorRequest(
                intent=TutorIntent.MASTERY_CHECK,
                goal=self._goal_text(),
                learner_level=self.state.learner_level,
                recent_exercises=recent,
                recent_attempts=self._recent_attempts(),
            ))
            repeated = self._normalize_exercise(response.message) in known_exercises
            if repeated:
                response = self.tutor.respond(TutorRequest(
                    intent=TutorIntent.MASTERY_CHECK,
                    goal=self._goal_text(),
                    learner_level=self.state.learner_level,
                    recent_exercises=recent,
                    recent_attempts=self._recent_attempts(),
                ))
                repeated = self._normalize_exercise(response.message) in known_exercises
            self._commit(self.machine.set_active_exercise(
                self.state,
                response.message,
                success_criteria=response.success_criteria,
                reference_answer=response.reference_answer,
                repeated=repeated,
            ))
        self.output(f"掌握度检验（独立完成，不提供提示）：\n{self.state.active_exercise}")
        if self.state.mastery_exercise_repeated:
            self.output("本题与之前的练习重复，本次结果不会记为掌握，建议稍后重新检验。")
        self._collect_practice_answer(LearningStage.MASTERY_CHECK)

    def _collect_practice_answer(self, stage: LearningStage) -> None:
        while self.state.stage is stage and not self.state.mastery_answer_submitted:
            raw = self.input("你的回答（或输入命令）> ").strip()
            command = self._command(raw)
            if command:
                if self._handle_command(command):
                    return
                continue
            if raw.startswith("/"):
                self.output(f"未识别的命令。{COMMAND_HELP}")
                continue
            if not raw:
                self.output("请先尝试作答。")
                continue
            assessment = self.tutor.assess_answer(AssessmentRequest(
                goal=self._goal_text(),
                exercise=self.state.active_exercise or "掌握度检验",
                learner_answer=raw,
                success_criteria=self.state.active_success_criteria,
                reference_answer=self.state.active_reference_answer,
            ))
            self._commit(self.evaluator.submit_answer(self.machine, self.state, assessment))
            self.output(assessment.feedback)
            self._record_mastery_result()
            return

    def _record_mastery_result(self) -> None:
        state, result = self.evaluator.record_mastery_result(self.machine, self.state)
        self._commit(state)
        if result.passed:
            self.output(f"掌握度已验证：{result.explanation}")
        else:
            self.output(f"本次尚未通过掌握度检验：{result.explanation}")
            self.output("可以继续练习或查看讲解；之后再用新的独立练习验证。")

    def _run_summary(self) -> None:
        progress = LearningProgress.from_state(self.state)
        record = {
            "progress_status": progress.status.value,
            "explanation_count": progress.explanation_count,
            "guided_attempt_count": progress.guided_attempt_count,
            "independent_attempt_count": progress.independent_attempt_count,
            "mastery_attempt_count": progress.mastery_attempt_count,
            "mastery_verified": self.state.mastery_was_passed,
            "skipped_stages": [stage.value for stage in self.state.skipped_stages],
        }
        response = self.tutor.respond(TutorRequest(
            intent=TutorIntent.SUMMARY,
            goal=self._goal_text(),
            learner_level=self.state.learner_level,
            concept=json.dumps(record, ensure_ascii=False),
            recent_attempts=self._recent_attempts(),
        ))
        self.output(f"学习总结：\n{response.message}")
        self._commit(self.machine.transition(self.state, LearningEvent.SUMMARY_COMPLETED))

    def _handle_command(self, command: str) -> bool:
        """Run an exact CLI command; return True when stage dispatch should restart."""
        state = self.state
        if command == "hint":
            if not state.active_exercise:
                self.output("当前没有可以提示的练习题。请先开始或继续一道练习。")
                return False
            try:
                next_state = self.machine.transition(state, LearningEvent.HINT_REQUESTED)
            except (InvalidTransitionError, HintLimitReachedError) as exc:
                self.output(str(exc))
                return False
            self._commit(next_state)
            self._show_hint()
            return self.state.stage is not state.stage
        if command == "explain":
            if not state.goal:
                self.output("先设置一个学习目标，再请求讲解。")
                return False
            self._commit(self.machine.transition(state, LearningEvent.EXPLANATION_REQUESTED))
            return True
        if command == "answer":
            if not state.active_exercise:
                self.output("当前没有可以查看答案的练习题。")
                return False
            self._commit(self.machine.transition(state, LearningEvent.ANSWER_REQUESTED))
            response = self.tutor.respond(TutorRequest(
                intent=TutorIntent.ANSWER,
                goal=self._goal_text(),
                learner_level=state.learner_level,
                exercise=state.active_exercise,
                reference_answer=state.active_reference_answer,
                recent_attempts=self._recent_attempts(),
            ))
            self.output(f"参考答案：\n{response.message}")
            return False
        if command == "skip":
            try:
                next_state = self.machine.transition(state, LearningEvent.SKIP_STAGE)
            except InvalidTransitionError as exc:
                self.output(str(exc))
                return False
            self._commit(next_state)
            self.output(
                f"已跳过当前阶段；现在进入「{STAGE_LABELS.get(self.state.stage, '未知阶段')}」。"
            )
            return True
        if command == "practice":
            if not state.goal:
                self.output("先设置一个学习目标，再开始练习。")
                return False
            event = (
                LearningEvent.FEEDBACK_CONTINUE_GUIDED
                if state.stage is LearningStage.FEEDBACK
                else LearningEvent.PRACTICE_REQUESTED
            )
            self._commit(self.machine.transition(state, event))
            return True
        if command == "independent":
            if state.stage is not LearningStage.FEEDBACK:
                self.output("完成当前练习后，可以从反馈阶段开始独立练习。")
                return False
            try:
                self._commit(self.machine.transition(
                    state,
                    LearningEvent.FEEDBACK_READY_FOR_INDEPENDENT,
                ))
            except InvalidTransitionError as exc:
                self.output(str(exc))
                return False
            return True
        if command == "mastery":
            if state.stage is not LearningStage.FEEDBACK:
                self.output("请先完成独立练习，再从反馈阶段进入掌握度检验。")
                return False
            try:
                self._commit(self.machine.transition(
                    state,
                    LearningEvent.FEEDBACK_READY_FOR_MASTERY,
                ))
            except InvalidTransitionError as exc:
                self.output(str(exc))
                return False
            return True
        if command == "summary":
            self._commit(self.machine.transition(state, LearningEvent.SUMMARY_REQUESTED))
            return True
        if command == "exit":
            self._exit_session()
            return True
        return False

    def _show_hint(self) -> None:
        if not self.state.active_exercise or self.state.hint_level not in range(1, 5):
            self.output("当前没有可以提示的练习题。")
            return
        response = self.tutor.respond(TutorRequest(
            intent=TutorIntent.HINT,
            goal=self._goal_text(),
            learner_level=self.state.learner_level,
            exercise=self.state.active_exercise,
            hint_level=self.state.hint_level,
            recent_attempts=self._recent_attempts(),
        ))
        self.output(f"L{self.state.hint_level} 提示：\n{response.message}")

    def _exit_session(self) -> None:
        if not self.state.is_terminal:
            self._commit(self.machine.transition(self.state, LearningEvent.EXIT_SESSION))

    def _save(self) -> None:
        self.store.save(self.state)

    def _commit(self, state: LearningState) -> None:
        self.state = state
        self._save()

    def _goal_text(self) -> str:
        return self.state.goal or "自主探索"

    def _recent_attempts(self) -> tuple[str, ...]:
        return summarize_attempts(self.state.practice_attempts, limit=5)

    @staticmethod
    def _is_ambiguous_goal(goal: str) -> bool:
        vague_phrases = ("不知道", "随便", "都行", "什么都行", "帮我学", "学点什么")
        return (
            len(goal) < 6
            or any(phrase in goal for phrase in vague_phrases)
            or goal.endswith(("?", "？"))
        )

    @staticmethod
    def _normalize_exercise(exercise: str) -> str:
        return " ".join(exercise.split()).casefold()

    @staticmethod
    def _command(raw: str) -> str | None:
        return COMMANDS.get(raw.strip().casefold())


def main() -> int:
    """Build the configured DeepSeek client and run the local interactive CLI."""
    from agent.llm_client import LLMClient
    from config import Settings

    try:
        settings = Settings.from_environment()
        tutor = TutorAgent(LLMClient(settings))
        cli = MoreThanDoneCLI(tutor)
        cli.run()
        return cli.exit_code
    except ValueError as exc:
        print(f"配置错误：{exc}")
        return EXIT_CONFIG
    except LearningStoreError as exc:
        print(f"学习记录错误：{exc}")
        return EXIT_DATA


if __name__ == "__main__":
    raise SystemExit(main())
