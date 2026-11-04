"""Exercise data ownership, checkpoints and browser authorization at normal APIs."""

import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore, ConversationStoreError
from agent.data_lock import DataDirectoryInUseError, DataDirectoryLock
from config import Settings
from web_ui import MoreThanDoneHTTPServer


@pytest.fixture
def workbench(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = AgentConfigStore(tmp_path / "config.json", settings_loader=lambda: Settings(api_key="", model="fake"))
    config.update({"workspace": str(workspace), "api_key": "fake-test-only", "permission_mode": "full"})
    records = ConversationStore(tmp_path / "chats.json")
    server = MoreThanDoneHTTPServer(("127.0.0.1", 0), object(), config_store=config, conversation_store=records)
    server.workspace.create_project({"workspace": str(workspace)})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, records, workspace
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def post(server, path, payload, **headers):
    request = Request(f"http://127.0.0.1:{server.server_port}" + path,
                      data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def test_closing_server_rejects_late_mutations(workbench):
    server, records, _ = workbench
    before = server.agent_config.path.read_bytes()
    server.closing.set()
    status, result = post(server, "/api/workspace/config", {"permission_mode": "request"})
    assert status == 503
    assert server.agent_config.path.read_bytes() == before


def test_failed_practice_project_save_removes_only_the_new_copy(workbench, monkeypatch):
    server, records, workspace = workbench
    (workspace / "work.txt").write_bytes(b"original\r\n")
    before = server.workspace.snapshot()
    saved = records.path.read_bytes()

    def fail(_):
        raise ConversationStoreError("Simulated project save failure")

    monkeypatch.setattr(records, "save", fail)
    with pytest.raises(ConversationStoreError):
        server.workspace.make_practice_copy(["work.txt"], conversation_id=before["active_conversation_id"])
    practice = server.agent_config.path.parent / "practice"
    assert not list(practice.iterdir())
    assert (workspace / "work.txt").read_bytes() == b"original\r\n"
    assert records.path.read_bytes() == saved


def test_second_server_process_cannot_open_the_owned_data_directory(workbench):
    server, records, _ = workbench
    script = r'''
import sys
from pathlib import Path
from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from agent.data_lock import DataDirectoryInUseError
from web_ui import MoreThanDoneHTTPServer
directory = Path(sys.argv[1])
try:
    server = MoreThanDoneHTTPServer(('127.0.0.1', 0), object(),
        config_store=AgentConfigStore(directory / 'config.json'),
        conversation_store=ConversationStore(directory / 'chats.json'))
except DataDirectoryInUseError:
    print('second server blocked')
else:
    server.server_close()
    raise SystemExit('second server unexpectedly acquired the directory')
'''
    result = subprocess.run([sys.executable, "-c", script, str(records.path.parent)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "second server blocked"
    assert server.socket.fileno() >= 0


def test_browser_mutations_require_exact_origin_json_and_current_token(workbench):
    server, _, _ = workbench
    base = f"http://127.0.0.1:{server.server_port}"
    payload = {"permission_mode": "request"}
    assert post(server, "/api/workspace/config", payload, Origin="http://127.0.0.1:1")[0] == 403
    assert post(server, "/api/workspace/config", payload, Origin=base)[0] == 403
    assert post(server, "/api/workspace/config", payload, Origin=base,
                **{"X-MTD-CSRF": "outdated"})[0] == 403
    assert server.agent_config.load().permission_mode == "full"
    assert post(server, "/api/workspace/config", payload, Origin=base,
                **{"X-MTD-CSRF": server.csrf_token, "Content-Type": "text/plain"})[0] == 400
    status, state = post(server, "/api/workspace/config", payload, Origin=base,
                         **{"X-MTD-CSRF": server.csrf_token})
    assert status == 200 and state["config"]["permission_mode"] == "request"


def test_user_and_completed_tool_are_saved_while_the_next_model_request_is_running(workbench):
    server, records, workspace = workbench
    blocked = threading.Event()
    release = threading.Event()

    class Writer:
        def __init__(self):
            self.calls = 0

        def complete_tool_call(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content=None, tool_calls=[{
                    "id": "write-test", "type": "function", "function": {
                        "name": "write_file", "arguments": json.dumps({"path": "result.txt", "content": "saved"}),
                    },
                }])
            blocked.set()
            release.wait(5)
            return SimpleNamespace(content="已完成。", tool_calls=None)

        def cancel(self):
            release.set()

    server.agent.client_factory = lambda settings: Writer()
    chat_id = server.workspace.snapshot()["active_conversation_id"]
    try:
        assert post(server, "/api/workspace/chat", {"message": "写入隔离文件", "conversation_id": chat_id})[0] == 200
        assert blocked.wait(2)
        assert server.agent.worker is not None and server.agent.worker.is_alive()
        chat = next(item for item in records.load()["conversations"] if item["id"] == chat_id)
        assert chat["messages"][0] == {"role": "user", "content": "写入隔离文件"}
        assert any(item.get("tool_call_id") == "write-test" for item in chat["messages"])
        assert any(item["kind"] == "tool_result" for item in chat["events"])
        assert (workspace / "result.txt").read_text(encoding="utf-8") == "saved"
        server.agent.close(timeout=1)
        assert server.agent.worker is None and server.agent.status == "idle"
    finally:
        release.set()


def test_failed_initial_checkpoint_does_not_start_the_model(workbench, monkeypatch):
    server, records, _ = workbench
    called = []
    server.agent.client_factory = lambda settings: called.append(settings)
    before = records.path.read_bytes()
    chat_id = server.workspace.snapshot()["active_conversation_id"]

    def fail(document):
        raise ConversationStoreError("模拟检查点保存失败")

    with monkeypatch.context() as patch:
        patch.setattr(records, "save", fail)
        status, body = post(server, "/api/workspace/chat", {"message": "不能启动", "conversation_id": chat_id})
        assert status == 400 and "检查点" in body["error"]
        assert not called
        assert server.agent.worker is None
        assert records.path.read_bytes() == before


def test_unfinished_shutdown_keeps_data_directory_owned(workbench):
    server, records, _ = workbench
    server.shutdown()
    server.agent.worker = SimpleNamespace(is_alive=lambda: True, join=lambda timeout: None)
    try:
        with pytest.raises(RuntimeError, match="锁定"):
            server.server_close()
        assert server.socket.fileno() == -1
        with pytest.raises(DataDirectoryInUseError):
            DataDirectoryLock(records.path.parent).acquire()
    finally:
        server.agent.worker = None
        server.server_close()
    with DataDirectoryLock(records.path.parent):
        pass


def test_save_failure_during_close_still_closes_socket_and_other_session(workbench, monkeypatch):
    server, records, _ = workbench
    server.shutdown()
    closed = []

    def fail():
        raise ConversationStoreError("模拟退出保存失败")

    with monkeypatch.context() as patch:
        patch.setattr(server.agent, "checkpoint", fail)
        patch.setattr(server.legacy_agent, "close", lambda: closed.append(True))
        with pytest.raises(ConversationStoreError, match="退出保存"):
            server.server_close()
        assert server.socket.fileno() == -1
        assert closed == [True]
    with DataDirectoryLock(records.path.parent):
        pass
