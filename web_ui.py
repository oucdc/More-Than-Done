"""Local browser interface for the More Than Done learning CLI."""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
import webbrowser

from agent.agent_config import AgentConfigError, AgentConfigStore
from agent.conversations import ConversationStore, ConversationStoreError
from agent.llm_client import LLMClient
from agent.local_agent import AgentSession
from agent.tutor_agent import TutorAgent
from agent.workspace import WorkspaceManager
from cli import MoreThanDoneCLI
from config import Settings
from learning.progress import LearningProgress, PROGRESS_LABELS
from learning.state import STAGE_LABELS, LearningState
from storage.json_store import JsonLearningStore, LearningStoreError


PROJECT_ROOT = Path(__file__).resolve().parent
WEB_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)) / "web"
MAX_INPUT_LENGTH = 20_000
MAX_REQUEST_BYTES = 100_000
MAX_EVENTS = 1_000


class WebSession:
    """Bridge the existing prompt-driven CLI to a browser event stream."""

    def __init__(self, tutor: TutorAgent, store: JsonLearningStore | None = None) -> None:
        self.tutor = tutor
        self.store = store or JsonLearningStore()
        self.lock = threading.RLock()
        self.input_queue: queue.Queue[str] = queue.Queue()
        self.events: list[dict[str, object]] = []
        self.next_event_id = 1
        self.cli: MoreThanDoneCLI | None = None
        self.worker: threading.Thread | None = None
        self.last_state = LearningState()
        self.status = "idle"
        self.awaiting_input = False
        self.exit_code: int | None = None

    def _append_locked(self, kind: str, text: str) -> None:
        self.events.append({
            "id": self.next_event_id,
            "kind": kind,
            "text": text,
            "time": datetime.now().astimezone().strftime("%H:%M"),
        })
        self.next_event_id += 1
        if len(self.events) > MAX_EVENTS:
            self.events = self.events[-MAX_EVENTS:]

    def emit(self, kind: str, text: str) -> None:
        with self.lock:
            self._append_locked(kind, text)

    def _input_fn(self, prompt: str) -> str:
        with self.lock:
            self._append_locked("prompt", prompt)
            try:
                prefilled = self.input_queue.get_nowait()
            except queue.Empty:
                self.awaiting_input = True
                self.status = "waiting"
                wait_for_user = True
            else:
                self.awaiting_input = False
                wait_for_user = False

        if wait_for_user:
            answer = self.input_queue.get()
            with self.lock:
                self.awaiting_input = False
                if self.status != "error":
                    self.status = "running"
            return answer
        return prefilled

    def _output_fn(self, line: str) -> None:
        self.emit("assistant", str(line))

    def _run(self) -> None:
        cli = self.cli
        if cli is None:
            return
        try:
            result = cli.run()
            with self.lock:
                self.last_state = result
                self.exit_code = cli.exit_code
                self.status = "finished" if result.is_terminal else "error"
                self.awaiting_input = False
        except Exception:
            with self.lock:
                self.last_state = cli.state
                self.status = "error"
                self.awaiting_input = False
                self._append_locked(
                    "assistant",
                    "界面遇到未处理错误。如果此前保存过学习进度，修复问题后可以继续。",
                )

    def start(self, *, prefilled_inputs: tuple[str, ...] = ()) -> bool:
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                return False
            self.input_queue = queue.Queue()
            for value in prefilled_inputs:
                self.input_queue.put(value)
            self.cli = MoreThanDoneCLI(
                self.tutor,
                store=self.store,
                input_fn=self._input_fn,
                output_fn=self._output_fn,
            )
            self.status = "starting"
            self.awaiting_input = False
            self.exit_code = None
            self.worker = threading.Thread(
                target=self._run,
                name="more-than-done-session",
                daemon=True,
            )
            self.worker.start()
            return True

    def submit(self, text: str) -> None:
        if not isinstance(text, str):
            raise ValueError("输入必须是文本。")
        if len(text) > MAX_INPUT_LENGTH:
            raise ValueError("单次输入最多 20,000 个字符。")
        with self.lock:
            if not self.awaiting_input:
                raise RuntimeError("当前没有等待中的输入。")
            self.awaiting_input = False
            self.status = "running"
            self._append_locked("user", text)
            self.input_queue.put(text)

    def new_session(self) -> None:
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError("当前学习流程仍在运行，请等它结束或先退出。")
        state = self.store.load()
        if state is not None and not state.is_terminal:
            raise RuntimeError("当前学习尚未完成。请先继续当前会话或退出后再新建。")
        self.emit("system", "正在归档上一轮学习，并开始新会话。")
        self.start(prefilled_inputs=("",))

    def restore_archive(self, filename: str) -> LearningState:
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("归档文件名无效。")
        if Path(filename).suffix.casefold() != ".json":
            raise ValueError("只能恢复 JSON 归档文件。")
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError("当前学习流程仍在运行，请先退出后再恢复归档。")

        archive_dir = (self.store.path.parent / "archive").resolve()
        archive_path = (archive_dir / filename).resolve()
        if archive_path.parent != archive_dir or not archive_path.is_file():
            raise ValueError("找不到所选归档。")

        restored_state = JsonLearningStore(archive_path).load()
        if restored_state is None:
            raise ValueError("所选归档为空，无法恢复。")
        current_state = self.store.load()
        if current_state is not None:
            self.store.archive()
        self.store.save(restored_state)

        with self.lock:
            self.last_state = restored_state
            self.cli = None
            self.exit_code = None
            self.status = "finished" if restored_state.is_terminal else "paused"
            self._append_locked(
                "system",
                "归档已恢复。当前学习记录已先行归档；你可以继续学习或开始新会话。",
            )
        if not restored_state.is_terminal:
            self.start()
        return restored_state

    def snapshot(self, after_id: int = 0) -> dict[str, object]:
        with self.lock:
            state = self.cli.state if self.cli is not None else self.last_state
            progress = LearningProgress.from_state(state)
            return {
                "events": [event for event in self.events if event["id"] > after_id],
                "next_id": self.next_event_id - 1,
                "status": self.status,
                "awaiting_input": self.awaiting_input,
                "exit_code": self.exit_code,
                "state": {
                    "stage": state.stage.value,
                    "stage_label": STAGE_LABELS[state.stage],
                    "goal": state.goal,
                    "learner_level": state.learner_level,
                    "hint_level": state.hint_level,
                    "attempt_count": len(state.practice_attempts),
                    "progress_status": progress.status.value,
                    "progress_label": PROGRESS_LABELS[progress.status],
                    "mastery_verified": state.mastery_was_passed,
                    "active_exercise": state.active_exercise,
                },
            }

    def archives(self) -> list[dict[str, object]]:
        archive_dir = self.store.path.parent / "archive"
        if not archive_dir.is_dir():
            return []

        rows: list[dict[str, object]] = []
        paths = sorted(
            archive_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[:30]
        for path in paths:
            try:
                state = JsonLearningStore(path).load()
                stat = path.stat()
            except (LearningStoreError, OSError):
                rows.append({
                    "filename": path.name,
                    "label": path.stem,
                    "goal": "无法读取的归档",
                    "stage": "unavailable",
                    "stage_label": "无法读取",
                    "time": "",
                })
                continue
            if state is None:
                continue
            rows.append({
                "filename": path.name,
                "label": path.stem,
                "goal": state.goal or "自主探索",
                "stage": state.stage.value,
                "stage_label": STAGE_LABELS[state.stage],
                "time": datetime.fromtimestamp(stat.st_mtime).astimezone().strftime(
                    "%Y-%m-%d %H:%M"
                ),
            })
        return rows


class MoreThanDoneHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        tutor: TutorAgent,
        *,
        config_store: AgentConfigStore | None = None,
        conversation_store: ConversationStore | None = None,
    ) -> None:
        self.session = WebSession(tutor)
        self.closing = threading.Event()
        self.agent_config = config_store or AgentConfigStore()
        self.agent = AgentSession(self.agent_config)
        # The older single-chat page must not reset the workspace's live chat.
        self.legacy_agent = AgentSession(self.agent_config)
        self.workspace = WorkspaceManager(
            self.agent_config,
            conversation_store or ConversationStore(),
            self.agent,
        )
        super().__init__(address, MoreThanDoneRequestHandler)

    def shutdown_in_background(self) -> None:
        threading.Thread(target=self.shutdown, name="more-than-done-shutdown", daemon=True).start()

    def server_close(self) -> None:
        self.closing.set()
        super().server_close()


class MoreThanDoneRequestHandler(BaseHTTPRequestHandler):
    server: MoreThanDoneHTTPServer
    server_version = "MoreThanDoneLocal/1"

    def _send(self, status: int, content_type: str, content: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; style-src 'self'; "
            "script-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", encoded)

    def _host_is_local(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").casefold()
        if host not in {"127.0.0.1", "localhost"}:
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).hostname in {"127.0.0.1", "localhost"}

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效。") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("请求内容过大。")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求内容必须是有效 JSON。") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是 JSON 对象。")
        return payload

    def do_GET(self) -> None:
        if not self._host_is_local():
            self._json(403, {"error": "仅允许本机访问。"})
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/workspace/events":
            self._stream_workspace()
            return
        if parsed.path == "/":
            self._serve_file("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/agent":
            self._serve_file("agent.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/workspace":
            self._serve_file("workspace.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/assets/styles.css":
            self._serve_file("styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/assets/app.js":
            self._serve_file("app.js", "text/javascript; charset=utf-8")
            return
        if parsed.path == "/agent-assets/agent.css":
            self._serve_file("agent.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/agent-assets/agent.js":
            self._serve_file("agent.js", "text/javascript; charset=utf-8")
            return
        if parsed.path == "/workspace-assets/workspace.css":
            self._serve_file("workspace.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/workspace-assets/workspace.js":
            self._serve_file("workspace.js", "text/javascript; charset=utf-8")
            return
        if parsed.path.startswith("/vendor/"):
            vendor_root = (WEB_ROOT / "vendor").resolve()
            target = (vendor_root / parsed.path.removeprefix("/vendor/")).resolve()
            if not target.is_relative_to(vendor_root) or not target.is_file():
                self._json(404, {"error": "找不到界面资源。"})
            else:
                self._send(200, mimetypes.guess_type(target.name)[0] or "application/octet-stream", target.read_bytes())
            return
        if parsed.path == "/api/workspace/state":
            try:
                self._json(200, self.server.workspace.snapshot())
            except (AgentConfigError, ConversationStoreError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if parsed.path == "/api/agent/config":
            try:
                self._json(200, {"config": self.server.legacy_agent.public_config()})
            except AgentConfigError as exc:
                self._json(400, {"error": str(exc), "config": {}})
            return
        if parsed.path == "/api/agent/state":
            try:
                after_id = int(parse_qs(parsed.query).get("after", ["0"])[0])
            except ValueError:
                self._json(400, {"error": "事件游标无效。"})
                return
            self._json(200, self.server.legacy_agent.snapshot(max(0, after_id)))
            return
        if parsed.path == "/api/state":
            try:
                after_id = int(parse_qs(parsed.query).get("after", ["0"])[0])
            except ValueError:
                self._json(400, {"error": "事件游标无效。"})
                return
            self._json(200, self.server.session.snapshot(max(0, after_id)))
            return
        if parsed.path == "/api/archives":
            self._json(200, {"archives": self.server.session.archives()})
            return
        self._json(404, {"error": "找不到请求的页面。"})

    def _stream_workspace(self) -> None:
        """Push live updates without saving or resending the whole chat per token."""
        try:
            initial = self.server.workspace.snapshot()
        except (AgentConfigError, ConversationStoreError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        def send(kind, payload):
            encoded = json.dumps(payload, ensure_ascii=False)
            self.wfile.write(f"event: {kind}\ndata: {encoded}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            send("state", initial)
            previous = initial
            conversation_id = initial["active_conversation_id"]
            workspace_version = initial["workspace_version"]
            while not self.server.closing.is_set():
                self.server.agent.wait_snapshot(previous["version"])
                if self.server.closing.is_set():
                    break
                # Coalesce nearby provider tokens into a single UI frame.
                time.sleep(0.04)
                with self.server.workspace.lock:
                    current = self.server.agent.snapshot()
                    active_id = self.server.workspace._loaded_conversation_id
                    if active_id != conversation_id or self.server.workspace.revision != workspace_version or (
                        current["status"] in {"idle", "error"}
                        and previous["status"] != current["status"]
                    ):
                        current = self.server.workspace.snapshot()
                        send("state", current)
                        conversation_id = current["active_conversation_id"]
                        workspace_version = current["workspace_version"]
                    elif current["version"] != previous["version"]:
                        old_events = {event["id"]: event for event in previous["events"]}
                        updated = [event for event in current["events"] if event != old_events.get(event["id"])]
                        send("session", {**current, "events": updated, "active_conversation_id": conversation_id,
                                         "state_epoch": self.server.workspace.state_epoch})
                    else:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                previous = current
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def do_POST(self) -> None:
        if not self._host_is_local():
            self._json(403, {"error": "仅允许本机访问。"})
            return
        try:
            payload = self._read_json()
            if self.path.startswith("/api/workspace/"):
                if self.path == "/api/workspace/project":
                    state = self.server.workspace.create_project(payload)
                elif self.path == "/api/workspace/select-project":
                    state = self.server.workspace.select_project(payload.get("id"))
                elif self.path == "/api/workspace/conversation":
                    state = self.server.workspace.new_conversation(payload.get("project_id"))
                elif self.path == "/api/workspace/select-conversation":
                    state = self.server.workspace.select_conversation(payload.get("id"))
                elif self.path == "/api/workspace/edit-project":
                    state = self.server.workspace.edit_project(payload.get("id"), payload.get("action"), payload.get("value"))
                elif self.path == "/api/workspace/delete-conversation":
                    state = self.server.workspace.delete_conversation(payload.get("id"))
                elif self.path == "/api/workspace/edit-conversation":
                    state = self.server.workspace.edit_conversation(payload.get("id"), payload.get("action"), payload.get("value"))
                elif self.path == "/api/workspace/config":
                    state = self.server.workspace.save_config(payload)
                elif self.path == "/api/workspace/chat":
                    state = self.server.workspace.send(payload.get("message"), conversation_id=payload.get("conversation_id") or "")
                elif self.path == "/api/workspace/approve":
                    state = self.server.workspace.approve(payload.get("id"), payload.get("allow"))
                elif self.path == "/api/workspace/cancel":
                    state = self.server.workspace.cancel()
                elif self.path == "/api/workspace/learning/action":
                    state = self.server.workspace.teaching_action(payload.get("action"), conversation_id=payload.get("conversation_id") or "")
                elif self.path == "/api/workspace/learning/attempt":
                    state = self.server.workspace.submit_attempt(payload.get("answer"), conversation_id=payload.get("conversation_id") or "")
                else:
                    self._json(404, {"error": "找不到请求的操作。"})
                    return
                self._json(200, state)
                return
            if self.path.startswith("/api/agent/"):
                if self.path == "/api/agent/config":
                    with self.server.workspace.lock, self.server.agent.lock:
                        self.server.legacy_agent.save_config(payload)
                        # Model and permissions are shared; execution roots are not.
                        self.server.workspace.revision += 1
                        self.server.agent.notify_state_change()
                elif self.path == "/api/agent/chat":
                    self.server.legacy_agent.send(payload.get("message"))
                elif self.path == "/api/agent/approve":
                    self.server.legacy_agent.approve(payload.get("id"), payload.get("allow"))
                elif self.path == "/api/agent/cancel":
                    self.server.legacy_agent.cancel()
                elif self.path == "/api/agent/new":
                    self.server.legacy_agent.new_conversation()
                else:
                    self._json(404, {"error": "找不到请求的操作。"})
                    return
                self._json(200, self.server.legacy_agent.snapshot())
                return
            if self.path == "/api/start" or self.path == "/api/resume":
                self.server.session.start()
            elif self.path == "/api/input":
                self.server.session.submit(payload.get("text"))
            elif self.path == "/api/new-session":
                self.server.session.new_session()
            elif self.path == "/api/restore":
                filename = payload.get("filename")
                if not isinstance(filename, str):
                    raise ValueError("归档文件名无效。")
                self.server.session.restore_archive(filename)
            elif self.path == "/api/shutdown":
                self._json(200, {"status": "shutting_down"})
                self.server.shutdown_in_background()
                return
            else:
                self._json(404, {"error": "找不到请求的操作。"})
                return
        except AgentConfigError as exc:
            self._json(400, {"error": str(exc)})
            return
        except ConversationStoreError as exc:
            self._json(400, {"error": str(exc)})
            return
        except (ValueError, LearningStoreError) as exc:
            self._json(400, {"error": str(exc)})
            return
        except RuntimeError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._json(200, self.server.session.snapshot())

    def _serve_file(self, filename: str, content_type: str) -> None:
        path = WEB_ROOT / filename
        try:
            content = path.read_bytes()
        except OSError:
            self._json(500, {"error": "界面文件无法读取。"})
            return
        self._send(200, content_type, content)

    def log_message(self, format: str, *args: object) -> None:
        # Keep the local terminal useful without logging request bodies or learner text.
        # Windowed builds may have no console or an invalid/closed stderr handle.
        # An optional request log must never abort an HTTP response.
        try:
            if sys.stderr is not None:
                super().log_message(format, *args)
        except (AttributeError, OSError, ValueError):
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the local More Than Done browser interface.")
    parser.add_argument("--port", type=int, default=8765, help="Local web port (default: 8765).")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not automatically open the browser.",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    try:
        settings = Settings.from_environment()
        tutor = TutorAgent(LLMClient(settings))
        server = MoreThanDoneHTTPServer(("127.0.0.1", args.port), tutor)
    except ValueError as exc:
        _show_startup_error("配置错误", str(exc))
        return 2
    except OSError as exc:
        _show_startup_error("无法启动图形界面", str(exc))
        return 3

    url = f"http://127.0.0.1:{server.server_address[1]}"
    if not getattr(sys, "frozen", False):
        print(f"More Than Done 图形界面已启动：{url}")
        print("此服务仅监听本机。按 Ctrl+C 停止。")
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open_new_tab(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not getattr(sys, "frozen", False):
            print("正在关闭图形界面…")
    finally:
        server.server_close()
    return 0


def _show_startup_error(title: str, message: str) -> None:
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    else:
        print(f"{title}：{message}")


if __name__ == "__main__":
    raise SystemExit(main())
