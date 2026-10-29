"""Streaming correctness: visible partial text, complete tools, safe interruption."""

import json
from pathlib import Path
import threading
from types import SimpleNamespace as NS
from urllib.request import urlopen

import pytest

from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from agent.errors import LLMConnectionError, LLMInvalidResponseError
from agent.llm_client import LLMClient
from agent.local_agent import LocalAgent
from agent.tools import ToolRuntime
from config import Settings
from desktop_app import DesktopAPI
from web_ui import MoreThanDoneHTTPServer


def chunk(text=None, tools=None, finish=None):
    return NS(choices=[NS(delta=NS(content=text, tool_calls=tools), finish_reason=finish)])


def fragment(index, *, call_id=None, name=None, arguments=None):
    return NS(index=index, id=call_id, function=NS(name=name, arguments=arguments))


def client_for(chunks):
    class Stream:
        closed = False
        def __iter__(self):
            for item in chunks:
                if isinstance(item, Exception):
                    raise item
                yield item
        def close(self):
            self.closed = True
    stream = Stream()
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return stream
    sdk = NS(chat=NS(completions=NS(create=create)))
    return LLMClient(Settings(api_key="test", model="test"), client=sdk), requests, stream


def test_stream_delivers_text_before_assembling_multiple_fragmented_tools():
    llm, requests, stream = client_for([
        chunk("先观察"), chunk("文件。"),
        chunk(tools=[fragment(0, call_id="a", name="read_file", arguments='{"path":'), fragment(1, call_id="b", name="list_dir", arguments='{"path":')]),
        chunk(tools=[fragment(1, arguments='"."}'), fragment(0, arguments='"README.md"}')]),
        chunk(finish="tool_calls"),
    ])
    seen = []
    response = llm.stream_tool_call([{"role": "user", "content": "hi"}], tools=[{}], on_delta=seen.append)
    assert seen == ["先观察", "文件。"]
    assert response.content == "先观察文件。"
    assert json.loads(response.tool_calls[0]["function"]["arguments"]) == {"path": "README.md"}
    assert json.loads(response.tool_calls[1]["function"]["arguments"]) == {"path": "."}
    assert requests[0]["stream"] is True
    assert stream.closed


def test_official_sdk_parses_provider_sse_into_streamed_text():
    import httpx
    from openai import OpenAI
    frames = [
        {"id": "test", "object": "chat.completion.chunk", "created": 0, "model": "test", "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}]}
        for text, finish in [("边生成", None), ("边显示", None), ("", "stop")]
    ]
    body = "".join("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
    def handle(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body.encode())
    with OpenAI(api_key="test", base_url="https://example.invalid", http_client=httpx.Client(transport=httpx.MockTransport(handle))) as sdk:
        llm = LLMClient(Settings(api_key="test", model="test"), client=sdk)
        seen = []
        result = llm.stream_tool_call([{"role": "user", "content": "hi"}], tools=[{}], on_delta=seen.append)
        assert seen == ["边生成", "边显示"]
        assert result.content == "边生成边显示"


@pytest.mark.parametrize("ending", [None, "length"])
def test_unfinished_stream_retains_partial_text_but_never_returns_tools(ending):
    llm, requests, stream = client_for([chunk("已经收到"), chunk(tools=[fragment(0, call_id="a", name="write_file", arguments='{"path":')]), chunk(finish=ending)])
    seen = []
    with pytest.raises(LLMInvalidResponseError):
        llm.stream_tool_call([{"role": "user", "content": "hi"}], tools=[{}], on_delta=seen.append)
    assert seen == ["已经收到"]
    assert len(requests) == 1
    assert stream.closed


def test_network_error_during_iteration_is_safely_mapped_without_retry():
    import httpx
    from openai import APIConnectionError
    llm, requests, stream = client_for([chunk("部分内容"), APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))])
    with pytest.raises(LLMConnectionError):
        llm.stream_tool_call([{"role": "user", "content": "hi"}], tools=[{}], on_delta=lambda _: None)
    assert len(requests) == 1
    assert stream.closed


def test_cancel_closes_stream_before_any_tool_can_execute():
    llm, _, stream = client_for([chunk("第一段"), chunk("第二段"), chunk(finish="stop")])
    cancel = threading.Event()
    with pytest.raises(InterruptedError):
        llm.stream_tool_call([{"role": "user", "content": "hi"}], tools=[{}], on_delta=lambda _: cancel.set(), cancel_event=cancel)
    assert stream.closed


def test_native_folder_picker_handles_selection_and_cancel(tmp_path):
    bridge = DesktopAPI()
    results = iter([(str(tmp_path),), None])
    received = []
    def pick(kind, **kwargs):
        received.append(kwargs)
        return next(results)
    bridge._window = NS(create_file_dialog=pick)
    assert bridge.choose_workspace(str(tmp_path)) == str(tmp_path.resolve())
    assert bridge.choose_workspace(str(tmp_path)) is None
    assert received[0] == {"directory": str(tmp_path), "allow_multiple": False}


@pytest.mark.parametrize("directory", ["", "missing-folder"])
def test_native_folder_picker_without_valid_explicit_path_starts_at_home(tmp_path, directory):
    received = []
    bridge = DesktopAPI()
    bridge._window = NS(create_file_dialog=lambda kind, **kwargs: received.append(kwargs))
    requested = str(tmp_path / directory) if directory else ""
    assert bridge.choose_workspace(requested) is None
    assert received == [{"directory": str(Path.home()), "allow_multiple": False}]


def test_windowed_request_logging_tolerates_an_invalid_console(monkeypatch):
    import web_ui
    class InvalidConsole:
        def write(self, text):
            raise OSError("No console handle in windowed executable")
    handler = object.__new__(web_ui.MoreThanDoneRequestHandler)
    handler.client_address = ("127.0.0.1", 0)
    with monkeypatch.context() as patch:
        patch.setattr(web_ui.sys, "stderr", InvalidConsole())
        handler.log_message('"%s" %s', "GET /workspace", 200)


def read_event(response):
    kind = None
    while True:
        line = response.readline().decode("utf-8").strip()
        if line.startswith("event: "):
            kind = line[7:]
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            response.readline()
            return kind, payload


def test_http_sse_delivers_partial_response_before_model_finishes_and_recovers(tmp_path):
    config = AgentConfigStore(tmp_path / "config.json", settings_loader=lambda: Settings(api_key="", model="test"))
    config.update({"workspace": str(tmp_path), "api_key": "test-key"})
    conversations = ConversationStore(tmp_path / "chats.json")
    server = MoreThanDoneHTTPServer(("127.0.0.1", 0), object(), config_store=config, conversation_store=conversations)
    server.workspace.create_project({"workspace": str(tmp_path)})
    first = threading.Event()
    release = threading.Event()
    class Teacher:
        def stream_tool_call(self, messages, *, on_delta, **kwargs):
            on_delta("第一段")
            first.set()
            release.wait(5)
            on_delta("，第二段。")
            return NS(content="第一段，第二段。", tool_calls=[])
    server.agent.client_factory = lambda _: Teacher()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/workspace/events", timeout=5) as response:
            kind, initial = read_event(response)
            assert kind == "state"
            server.workspace.send("开始")
            assert first.wait(2)
            while True:
                kind, partial = read_event(response)
                visible = [event for event in partial["events"] if event["kind"] == "assistant"]
                if visible:
                    break
            assert kind == "session"
            assert visible[0]["text"] == "第一段"
            assert visible[0]["streaming"] is True
            assert server.agent.worker.is_alive()
            release.set()
            while True:
                kind, final = read_event(response)
                if final["status"] == "idle":
                    break
            assert kind == "state"
            assistants = [event for event in final["events"] if event["kind"] == "assistant"]
            assert len(assistants) == 1
            assert assistants[0]["text"] == "第一段，第二段。"
            assert not assistants[0]["streaming"]
            saved = conversations.load()["conversations"][0]
            assert saved["messages"][-1]["content"] == "第一段，第二段。"
        # Reconnect returns a full state, avoiding missing or duplicate text.
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/workspace/events", timeout=5) as response:
            kind, restored = read_event(response)
            assert kind == "state"
            assert restored["events"] == final["events"]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_interrupted_agent_preserves_partial_history_and_marks_event(tmp_path):
    from agent.local_agent import AgentSession
    store = AgentConfigStore(tmp_path / "config.json", settings_loader=lambda: Settings(api_key="", model="test"))
    config = store.update({"workspace": str(tmp_path), "api_key": "test-key"})
    session = AgentSession(store)
    class Teacher:
        def stream_tool_call(self, messages, *, on_delta, **kwargs):
            on_delta("保留这部分")
            raise LLMInvalidResponseError("模型流式回复未完整结束")
    agent = LocalAgent(config, Teacher(), ToolRuntime(config), emit=session.emit, messages=session.messages)
    with pytest.raises(LLMInvalidResponseError):
        agent.run()
    assert session.messages[-1]["content"] == "保留这部分"
    event = session.events[-1]
    assert event["kind"] == "assistant" and event["interrupted"] and not event["streaming"]


def test_sse_refreshes_sidebar_when_an_inactive_chat_is_deleted(tmp_path):
    config = AgentConfigStore(tmp_path / "config.json", settings_loader=lambda: Settings(api_key="", model="test"))
    config.update({"workspace": str(tmp_path)})
    server = MoreThanDoneHTTPServer(("127.0.0.1", 0), object(), config_store=config, conversation_store=ConversationStore(tmp_path / "chats.json"))
    server.workspace.create_project({"workspace": str(tmp_path)})
    first = server.workspace.snapshot()["active_conversation_id"]
    second = server.workspace.new_conversation()["active_conversation_id"]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/workspace/events", timeout=5) as response:
            assert read_event(response)[0] == "state"
            server.workspace.delete_conversation(first)
            kind, state = read_event(response)
            assert kind == "state"
            assert state["active_conversation_id"] == second
            assert all(chat["id"] != first for chat in state["conversations"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_sse_refreshes_sidebar_when_an_inactive_chat_is_renamed_or_archived(tmp_path):
    config = AgentConfigStore(tmp_path / "config.json", settings_loader=lambda: Settings(api_key="", model="test"))
    config.update({"workspace": str(tmp_path)})
    server = MoreThanDoneHTTPServer(("127.0.0.1", 0), object(), config_store=config, conversation_store=ConversationStore(tmp_path / "chats.json"))
    server.workspace.create_project({"workspace": str(tmp_path)})
    first = server.workspace.snapshot()["active_conversation_id"]
    second = server.workspace.new_conversation()["active_conversation_id"]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/workspace/events", timeout=5) as response:
            assert read_event(response)[0] == "state"
            server.workspace.edit_conversation(first, "rename", "已重命名")
            kind, state = read_event(response)
            assert kind == "state" and state["active_conversation_id"] == second
            assert next(item for item in state["conversations"] if item["id"] == first)["title"] == "已重命名"
            server.workspace.edit_conversation(first, "archive", True)
            kind, state = read_event(response)
            assert kind == "state" and state["active_conversation_id"] == second
            assert any(item["id"] == first for item in state["archived_conversations"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_sse_pushes_true_empty_state_after_last_project_is_deleted(tmp_path):
    config = AgentConfigStore(tmp_path / "config.json", settings_loader=lambda: Settings(api_key="", model="test"))
    config.update({"workspace": str(tmp_path)})
    server = MoreThanDoneHTTPServer(("127.0.0.1", 0), object(), config_store=config, conversation_store=ConversationStore(tmp_path / "chats.json"))
    project = server.workspace.create_project({"workspace": str(tmp_path)})["active_project_id"]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/workspace/events", timeout=5) as response:
            kind, initial = read_event(response)
            assert kind == "state" and initial["active_project_id"] == project
            server.workspace.edit_project(project, "delete")
            kind, empty = read_event(response)
            assert kind == "state" and empty["projects"] == []
            assert empty["active_project_id"] is None and empty["active_conversation_id"] is None
            assert empty["events"] == [] and not empty["lesson"]["steps"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
