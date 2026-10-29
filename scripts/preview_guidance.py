"""Manual desktop acceptance with a fake model and disposable learner data.

Run: python -m scripts.preview_guidance
This never reads the user's API configuration or calls an external model.
"""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from config import Settings
from web_ui import MoreThanDoneHTTPServer


def _response(content=None, tool=None, arguments=None):
    calls = [SimpleNamespace(id="preview-" + uuid4().hex, function=SimpleNamespace(name=tool, arguments=json.dumps(arguments, ensure_ascii=False)))] if tool else None
    return SimpleNamespace(content=content, tool_calls=calls)


class PreviewTeacher:
    def stream_tool_call(self, messages, *, tools, max_tokens, on_delta, cancel_event):
        response = self.complete_tool_call(messages, tools=tools, max_tokens=max_tokens)
        text = response.content or ""
        for offset in range(0, len(text), 8):
            if cancel_event.is_set():
                raise InterruptedError("Preview cancelled")
            on_delta(text[offset:offset + 8])
            time.sleep(0.06)
        return response

    def complete_tool_call(self, messages, *, tools, max_tokens):
        context = json.loads(messages[1]["content"].split("\n", 1)[1])
        last_user = next((item.get("content", "") for item in reversed(messages) if item.get("role") == "user"), "")
        if last_user in {"验收Python", "验收命令失败", "验收停止"}:
            if messages[-1].get("role") == "tool":
                return _response("命令检查结束。请查看卡片中的退出码、输出和建议。")
            commands = {"验收Python": 'python -c "import sys; print(sys.executable); print(\'中文环境正常\')"', "验收命令失败": "exit 7", "验收停止": 'python -u -c "import time; print(\'开始等待\',flush=True); time.sleep(60)"'}
            return _response(tool="run_command", arguments={"command": commands[last_user], "cwd": "."})
        if last_user == "验收审批":
            if messages[-1].get("role") == "tool":
                return _response("审批操作已处理。这只是隔离目录中的本地验收示例。")
            return _response(tool="run_command", arguments={"command": "echo More Than Done", "cwd": "."})
        if not context["goal"]:
            return _response(tool="plan_learning", arguments={"goal": "学会读懂一个本地项目", "steps": [
                {"title": "找到项目的入口", "task": "打开工作区中的 README.md，用自己的话说明这个项目做什么、应先查看哪个文件。", "success_criteria": "说明项目用途，并为下一步选择一个文件和理由。", "kind": "guided"},
                {"title": "自己阅读一份说明", "task": "独立阅读 GUIDE.md，解释如何判断程序有没有成功运行。", "success_criteria": "给出自己的判断方法和依据，不依赖演示。", "kind": "independent"},
                {"title": "换个场景再试", "task": "如果拿到另一个陌生项目，你会先查看什么，为什么？", "success_criteria": "把刚学到的阅读方法迁移到新情境。", "kind": "transfer"},
            ]})
        if context["submission"]:
            if messages[-1].get("role") != "tool":
                return _response(tool="read_file", arguments={"path": "README.md"})
            answer = context["submission"]["text"]
            adequate = len(answer) >= 15 and "不知道" not in answer
            return _response(tool="review_learning_attempt", arguments={"submission_id": context["submission"]["id"], "correctness": "correct" if adequate else "uncertain", "reasoning_quality": "sound" if adequate else "unavailable", "evidence": "验收假模型：根据真实提交文本及临时 README 生成固定反馈，不代表真实模型质量。", "feedback": "你已说明自己的观察和下一步理由。可以继续下一小步。" if adequate else "我还看不到足够依据。请补充你观察到了什么，以及这样判断的原因。"})
        last_user = next((item.get("content", "") for item in reversed(messages) if item.get("role") == "user"), "")
        if "界面已记录提示" in last_user:
            return _response("先看 README 的第一段和启动说明。\n\n只回答一个问题：**哪个文件最可能是程序入口？你从哪里看出来？**")
        if context["completed"]:
            return _response("本轮计划已完成。回顾你自己的观察、判断和操作，再选一个新项目复做。掌握记录以右侧证据为准。")
        return _response("### 现在只做这一小步\n\n先观察 README，**不要急着修改项目**。\n\n1. 找出项目用途。\n2. 说说你准备查看哪个文件，并解释原因。\n\n你可以在编辑器中打开它，也可以先请求提示。写好后点击 **提交我的尝试**。\n\n```text\n观察 → 提出自己的判断 → 说明依据\n```\n\n我会等你完成，再讨论下一步。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-only", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--permission-mode", choices=("request", "auto", "full"), default="request")
    parser.add_argument("--pending-review", action="store_true", help="Show a partial review followed by a genuine pending submission.")
    parser.add_argument("--empty-project", action="store_true", help="Preview the initial no-project state.")
    args = parser.parse_args()
    if args.empty_project and args.pending_review:
        parser.error("empty-project and pending-review cannot be combined")
    with TemporaryDirectory(prefix="mtd-guidance-preview-") as temporary:
        root = Path(temporary)
        workspace = root / "练习项目"
        workspace.mkdir()
        (workspace / "README.md").write_text("# 问候程序\n这是一个打印问候语的小项目。先查看 main.py，再运行它。\n", encoding="utf-8")
        (workspace / "GUIDE.md").write_text("程序退出码为 0 且输出包含问候语时，初步认为运行成功。\n", encoding="utf-8")
        (workspace / "main.py").write_text("print('Hello, learner!')\n", encoding="utf-8")
        config = AgentConfigStore(root / "agent_config.json", settings_loader=lambda: Settings(api_key="", model="preview", base_url="https://example.invalid"))
        config.update({"workspace": str(workspace), "api_key": "preview-only", "model": "preview-offline", "permission_mode": args.permission_mode})
        server = MoreThanDoneHTTPServer(("127.0.0.1", args.port), object(), config_store=config, conversation_store=ConversationStore(root / "conversations.json"))
        server.agent.client_factory = lambda _: PreviewTeacher()
        if not args.empty_project:
            server.workspace.create_project({"name": "练习项目", "workspace": str(workspace)})
        if args.pending_review:
            server.workspace.snapshot()
            lesson = server.agent.lesson
            lesson.plan({"goal": "学会读懂项目", "steps": [
                {"title": "理解项目入口", "task": "阅读 README 并说明先查看哪个文件和理由。", "success_criteria": "说明项目用途、入口文件和依据。", "kind": "guided"},
                {"title": "运行一个 Python 文件", "task": "阅读 main.py，运行后说明观察到的结果。", "success_criteria": "给出自己的操作和判断依据。", "kind": "guided"}]})
            submission = lesson.submit("hello 7")
            lesson.review({"submission_id": submission["id"], "correctness": "correct", "reasoning_quality": "partial", "evidence": "已提供输出，尚未说明判断依据。", "feedback": "输出正确，请补充你为何选择这个入口文件。"})
            lesson.submit("没有")
            server.workspace.snapshot()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/workspace"
        print(url, flush=True)
        try:
            if args.browser_only:
                thread.join()
            else:
                import webview
                from desktop_app import DesktopAPI
                bridge = DesktopAPI()
                bridge._window = webview.create_window("More Than Done · 教学验收", url, width=1440, height=960, min_size=(800, 600), js_api=bridge)
                webview.start(gui="edgechromium")
        except KeyboardInterrupt:
            pass
        finally:
            server.agent.cancel()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
