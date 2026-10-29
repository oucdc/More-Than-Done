"""Native desktop window for More Than Done using the local WebView runtime."""

from __future__ import annotations

import argparse
import threading
import sys
from pathlib import Path

import webview

from agent.llm_client import LLMClient
from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from agent.tutor_agent import TutorAgent
from config import APP_VERSION, DEFAULT_BASE_URL, DEFAULT_MODEL, Settings
from web_ui import MoreThanDoneHTTPServer


class DesktopAPI:
    """Expose the native workspace folder picker to the local workbench."""

    def __init__(self) -> None:
        self._window = None

    def choose_workspace(self, directory: str = "") -> str | None:
        if self._window is None:
            raise RuntimeError("窗口尚未就绪，请稍后重试。")
        initial = Path(directory).expanduser() if isinstance(directory, str) and directory.strip() else Path.home()
        if not initial.is_dir():
            initial = Path.home()
        selected = self._window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=str(initial),
            allow_multiple=False,
        )
        return str(Path(selected[0]).resolve()) if selected else None


def _make_tutor() -> TutorAgent:
    try:
        settings = Settings.from_environment()
    except (ValueError, OSError):
        # The Agent workbench can be configured from its own settings page.
        # Keep the window available even before a key is supplied.
        settings = Settings(
            api_key="desktop-placeholder",
            model=DEFAULT_MODEL,
            base_url=DEFAULT_BASE_URL,
        )
    return TutorAgent(LLMClient(settings))


def main() -> int:
    parser = argparse.ArgumentParser(description="More Than Done native desktop window.")
    parser.add_argument(
        "--start-page",
        choices=("workspace",),
        default="workspace",
        help="Agent page shown when the window opens.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable WebView developer tools.")
    parser.add_argument("--data-dir", type=Path, help="Optional isolated directory for application settings and conversations.")
    parser.add_argument("--debug-log", type=Path, help="Optional diagnostic log for desktop startup and HTTP failures.")
    args = parser.parse_args()

    if args.debug_log:
        args.debug_log.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = args.debug_log.open("a", encoding="utf-8", buffering=1)

    data_root = args.data_dir.resolve() if args.data_dir else None
    server = MoreThanDoneHTTPServer(
        ("127.0.0.1", 0), _make_tutor(),
        config_store=AgentConfigStore(data_root / "agent_config.json") if data_root else None,
        conversation_store=ConversationStore(data_root / "conversations.json") if data_root else None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    path = "/workspace"
    url = f"http://127.0.0.1:{port}{path}"

    try:
        desktop_api = DesktopAPI()
        desktop_api._window = webview.create_window(
            f"More Than Done {APP_VERSION}",
            url,
            width=1440,
            height=960,
            min_size=(800, 600),
            text_select=True,
            js_api=desktop_api,
        )
        webview.start(gui="edgechromium", debug=args.debug)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
