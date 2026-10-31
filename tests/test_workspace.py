from types import SimpleNamespace
import json
import time

import pytest

from agent.agent_config import AgentConfigError, AgentConfigStore
from agent.conversations import ConversationStore, ConversationStoreError
from agent.local_agent import AgentSession
from agent.workspace import WorkspaceManager
from config import Settings


def no_env_settings():
    return Settings(api_key="", model="test-model", base_url="https://example.invalid")


class FakeClient:
    def complete_tool_call(self, messages, *, tools, max_tokens):
        return SimpleNamespace(content="完成。", tool_calls=None)


def make_manager(tmp_path, *, create_initial=True):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_store = AgentConfigStore(tmp_path / "agent_config.json", settings_loader=no_env_settings)
    config_store.update({
        "workspace": str(workspace),
        "api_key": "test-key",
        "model": "deepseek-flash",
        "base_url": "https://example.invalid",
        "allow_read": True,
        "allow_write": False,
        "allow_shell": False,
        "confirm_write": True,
        "confirm_shell": True,
        "reasoning_level": "medium",
    })
    conversation_store = ConversationStore(tmp_path / "conversations.json")
    session = AgentSession(
        config_store,
        client_factory=lambda _settings: FakeClient(),
    )
    manager = WorkspaceManager(config_store, conversation_store, session)
    if create_initial:
        manager.create_project({"name": "workspace", "workspace": str(workspace)})
    return manager, conversation_store, config_store, workspace


def wait_idle(session, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if session.status == "idle":
            return
        time.sleep(0.02)
    raise AssertionError(f"session status is {session.status!r}")


def test_fresh_workspace_remains_empty_until_user_creates_project(tmp_path):
    manager, store, config_store, workspace = make_manager(tmp_path, create_initial=False)
    versions = []
    for _ in range(3):
        state = manager.snapshot()
        versions.append(state["version"])
        assert state["projects"] == [] and state["conversations"] == []
        assert state["active_project"] is None and state["active_conversation"] is None
        assert state["active_project_id"] is None and state["active_conversation_id"] is None
        assert not state["events"] and not state["lesson"]["steps"]
        assert state["config"].get("workspace") in (None, "")
        assert state["environment"] == {}
    assert len(set(versions)) == 1
    restored = WorkspaceManager(config_store, store, AgentSession(config_store)).snapshot()
    assert restored["projects"] == [] and restored["active_project"] is None
    created = manager.create_project({"workspace": str(workspace)})
    assert len(created["projects"]) == 1 and len(created["conversations"]) == 1
    assert created["active_project"]["workspace"] == str(workspace.resolve())


def test_send_persists_conversation_and_title(tmp_path):
    manager, store, config_store, workspace = make_manager(tmp_path)
    state = manager.snapshot()

    manager.send("请读取 notes")
    wait_idle(manager.session)
    state = manager.snapshot()

    assert state["status"] == "idle"
    assert state["events"][-1]["kind"] == "assistant"
    assert state["active_conversation"]["title"] == "请读取 notes"
    assert state["active_conversation"]["message_count"] >= 2

    new_manager = WorkspaceManager(
        config_store,
        store,
        AgentSession(
            config_store,
            client_factory=lambda _settings: FakeClient(),
        ),
    )
    restored = new_manager.snapshot()
    assert restored["active_conversation_id"] == state["active_conversation_id"]
    assert any(event["kind"] == "user" and event["text"] == "请读取 notes" for event in restored["events"])
    assert any(event["kind"] == "assistant" and event["text"] == "完成。" for event in restored["events"])


def test_projects_conversations_and_workspace_switch(tmp_path):
    manager, store, config_store, workspace = make_manager(tmp_path)
    first = manager.snapshot()
    first_conversation = first["active_conversation_id"]
    first_project = first["active_project_id"]

    second_workspace = tmp_path / "second"
    second_workspace.mkdir()
    state = manager.create_project({"name": "第二项目", "workspace": str(second_workspace)})
    assert len(state["projects"]) == 2
    assert state["active_project"]["name"] == "第二项目"
    assert state["active_project"]["workspace"] == str(second_workspace.resolve())
    assert manager.session.load_config().workspace == second_workspace.resolve()

    manager.new_conversation()
    state = manager.snapshot()
    assert len([item for item in state["conversations"] if item["project_id"] == state["active_project_id"]]) >= 2

    manager.select_conversation(first_conversation)
    state = manager.snapshot()
    assert state["active_project_id"] == first_project
    assert state["active_conversation_id"] == first_conversation
    assert manager.session.load_config().workspace == workspace.resolve()


def test_save_config_updates_model_reasoning_and_permissions(tmp_path):
    manager, store, config_store, workspace = make_manager(tmp_path)
    state = manager.snapshot()

    state = manager.save_config({
        "model": "deepseek-chat",
        "reasoning_level": "high",
        "allow_read": True,
        "allow_write": True,
        "allow_shell": True,
        "confirm_write": False,
        "confirm_shell": True,
    })

    assert state["config"]["model"] == "deepseek-chat"
    assert state["config"]["reasoning_level"] == "high"
    assert state["config"]["allow_write"] is True
    assert state["config"]["allow_shell"] is True
    assert state["config"]["workspace"] == str(workspace.resolve())

    with pytest.raises(AgentConfigError):
        manager.save_config({"reasoning_level": "extreme"})


def test_delete_active_chat_switches_to_another_chat_and_survives_restart(tmp_path):
    manager, store, config_store, _ = make_manager(tmp_path)
    first = manager.snapshot()["active_conversation_id"]
    manager.send("保留的对话")
    wait_idle(manager.session)
    second = manager.new_conversation()["active_conversation_id"]
    manager.send("要删除的对话")
    wait_idle(manager.session)
    deleted = manager.delete_conversation(second)
    assert deleted["active_conversation_id"] == first
    assert all(chat["id"] != second for chat in deleted["conversations"])
    assert any(event["text"] == "保留的对话" for event in deleted["events"])
    assert all(event["text"] != "要删除的对话" for event in deleted["events"])
    restored = WorkspaceManager(config_store, store, AgentSession(config_store)).snapshot()
    assert restored["active_conversation_id"] == first
    assert all(chat["id"] != second for chat in restored["conversations"])


def test_delete_last_chat_creates_an_empty_replacement_in_same_project(tmp_path):
    manager, store, config_store, workspace = make_manager(tmp_path)
    before = manager.snapshot()
    (workspace / "keep.txt").write_text("keep", encoding="utf-8")
    state = manager.delete_conversation(before["active_conversation_id"])
    assert state["active_project_id"] == before["active_project_id"]
    assert state["active_conversation_id"] != before["active_conversation_id"]
    assert len(state["conversations"]) == 1
    assert not state["events"] and not state["lesson"]["steps"]
    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_delete_inactive_chat_does_not_switch_project_or_clear_current_chat(tmp_path):
    manager, store, config_store, workspace = make_manager(tmp_path)
    first = manager.snapshot()
    other = tmp_path / "other"
    other.mkdir()
    manager.create_project({"workspace": str(other)})
    manager.send("当前项目的消息")
    wait_idle(manager.session)
    before = manager.snapshot()
    state = manager.delete_conversation(first["active_conversation_id"])
    assert state["active_conversation_id"] == before["active_conversation_id"]
    assert state["active_project_id"] == before["active_project_id"]
    assert state["events"] == before["events"]
    assert manager.session.load_config().workspace == other.resolve()
    assert state["version"] > before["version"] and state["workspace_version"] > before["workspace_version"]
    first_project = next(project for project in state["projects"] if project["id"] == first["active_project_id"])
    assert first_project["conversation_count"] == 0


@pytest.mark.parametrize("chat_id", [None, 1, "", "does-not-exist"])
def test_delete_unknown_chat_keeps_store_unchanged(tmp_path, chat_id):
    manager, store, _, _ = make_manager(tmp_path)
    manager.snapshot()
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        manager.delete_conversation(chat_id)
    assert store.path.read_bytes() == before


def test_cannot_delete_during_a_running_turn_or_pending_approval(tmp_path):
    manager, store, _, _ = make_manager(tmp_path)
    before = manager.snapshot()
    manager.session.worker = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(RuntimeError, match="运行"):
        manager.delete_conversation(before["active_conversation_id"])
    assert store.load()["active_conversation_id"] == before["active_conversation_id"]


def test_failed_delete_save_keeps_disk_and_in_memory_chat(tmp_path, monkeypatch):
    manager, store, _, _ = make_manager(tmp_path)
    state = manager.snapshot()
    before = store.path.read_bytes()
    def fail(_):
        raise ConversationStoreError("无法保存")
    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(ConversationStoreError):
        manager.delete_conversation(state["active_conversation_id"])
    assert store.path.read_bytes() == before
    assert manager._loaded_conversation_id == state["active_conversation_id"]


def test_rename_and_pin_survive_restart_and_custom_default_title_is_not_overwritten(tmp_path):
    manager, store, config_store, _ = make_manager(tmp_path)
    chat = manager.snapshot()["active_conversation_id"]
    manager.edit_conversation(chat, "rename", "新对话")
    state = manager.edit_conversation(chat, "pin", True)
    assert state["active_conversation"]["pinned"] is True
    manager.send("自定义标题不应该被替换")
    wait_idle(manager.session)
    restored = WorkspaceManager(config_store, store, AgentSession(config_store)).snapshot()
    assert restored["active_conversation"]["title"] == "新对话"
    assert restored["active_conversation"]["pinned"] is True
    assert manager.edit_conversation(chat, "pin", False)["active_conversation"]["pinned"] is False


def test_archive_last_active_chat_preserves_history_and_can_be_restored_after_restart(tmp_path):
    manager, store, config_store, _ = make_manager(tmp_path)
    first = manager.snapshot()
    chat = first["active_conversation_id"]
    manager.send("要归档的消息")
    wait_idle(manager.session)
    state = manager.edit_conversation(chat, "archive", True)
    assert state["active_conversation_id"] != chat
    assert all(item["id"] != chat for item in state["conversations"])
    assert [item["id"] for item in state["archived_conversations"]] == [chat]
    with pytest.raises(ValueError, match="先恢复"):
        manager.select_conversation(chat)
    restored = WorkspaceManager(config_store, store, AgentSession(config_store))
    assert restored.snapshot()["archived_conversations"][0]["id"] == chat
    state = restored.edit_conversation(chat, "archive", False)
    assert not state["archived_conversations"]
    state = restored.select_conversation(chat)
    assert any(event["text"] == "要归档的消息" for event in state["events"])


def test_archived_chat_is_not_selected_when_switching_projects(tmp_path):
    manager, _, _, _ = make_manager(tmp_path)
    first = manager.snapshot()
    manager.edit_conversation(first["active_conversation_id"], "archive", True)
    other = tmp_path / "other_project"
    other.mkdir()
    manager.create_project({"workspace": str(other)})
    state = manager.select_project(first["active_project_id"])
    assert state["active_conversation_id"] != first["active_conversation_id"]
    assert state["archived_conversations"][0]["id"] == first["active_conversation_id"]


def test_move_active_chat_switches_workspace_and_keeps_its_history(tmp_path):
    manager, store, config_store, workspace = make_manager(tmp_path)
    first = manager.snapshot()
    chat = first["active_conversation_id"]
    manager.send("随对话移动的消息")
    wait_idle(manager.session)
    other = tmp_path / "new_workspace"
    other.mkdir()
    target_project = manager.create_project({"name": "另一项目", "workspace": str(other)})["active_project_id"]
    manager.select_conversation(chat)
    state = manager.edit_conversation(chat, "move", target_project)
    assert state["active_conversation_id"] == chat and state["active_project_id"] == target_project
    assert manager.session.load_config().workspace == other.resolve()
    assert any(event["text"] == "随对话移动的消息" for event in state["events"])
    assert not any(message.get("role") == "system" for message in manager.session.messages)
    restored = WorkspaceManager(config_store, store, AgentSession(config_store)).snapshot()
    assert restored["active_conversation_id"] == chat and restored["active_project_id"] == target_project


def test_move_inactive_chat_keeps_active_project_and_input_state(tmp_path):
    manager, _, config_store, workspace = make_manager(tmp_path)
    first = manager.snapshot()
    old_chat = first["active_conversation_id"]
    other = tmp_path / "other_project"
    other.mkdir()
    target = manager.create_project({"workspace": str(other)})["active_project_id"]
    current = manager.snapshot()
    state = manager.edit_conversation(old_chat, "move", target)
    assert state["active_conversation_id"] == current["active_conversation_id"]
    assert state["active_project_id"] == current["active_project_id"]
    assert manager.session.load_config().workspace == other.resolve()
    assert next(item for item in state["conversations"] if item["id"] == old_chat)["project_id"] == target


def test_move_save_failure_restores_workspace_and_preserves_chat(tmp_path, monkeypatch):
    manager, store, config_store, workspace = make_manager(tmp_path)
    chat = manager.snapshot()["active_conversation_id"]
    other = tmp_path / "other_project"
    other.mkdir()
    target = manager.create_project({"workspace": str(other)})["active_project_id"]
    manager.select_conversation(chat)
    before = store.path.read_bytes()
    def fail(_):
        raise ConversationStoreError("无法保存")
    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(ConversationStoreError):
        manager.edit_conversation(chat, "move", target)
    assert store.path.read_bytes() == before
    assert manager.session.load_config().workspace == workspace.resolve()
    assert manager._loaded_conversation_id == chat


@pytest.mark.parametrize(("action", "value"), [("rename", "  "), ("rename", "x" * 81), ("pin", "yes"), ("archive", 1), ("move", "missing"), ("unknown", None)])
def test_invalid_chat_changes_leave_disk_unchanged(tmp_path, action, value):
    manager, store, _, _ = make_manager(tmp_path)
    chat = manager.snapshot()["active_conversation_id"]
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        manager.edit_conversation(chat, action, value)
    assert store.path.read_bytes() == before


def test_chat_management_is_blocked_while_agent_running(tmp_path):
    manager, store, _, _ = make_manager(tmp_path)
    chat = manager.snapshot()["active_conversation_id"]
    before = store.path.read_bytes()
    manager.session.worker = SimpleNamespace(is_alive=lambda: True)
    with pytest.raises(RuntimeError, match="运行"):
        manager.edit_conversation(chat, "archive", True)
    assert store.path.read_bytes() == before


def test_invalid_archive_metadata_never_rewrites_original_store(tmp_path):
    import json
    manager, store, _, _ = make_manager(tmp_path)
    manager.snapshot()
    document = json.loads(store.path.read_text(encoding="utf-8"))
    document["conversations"][0]["archived"] = "false"
    store.path.write_text(json.dumps(document), encoding="utf-8")
    before = store.path.read_bytes()
    with pytest.raises(ConversationStoreError, match="archived"):
        manager.snapshot()
    assert store.path.read_bytes() == before


def test_project_actions_preserve_conversations_and_restore_individual_archive(tmp_path):
    manager, store, config, workspace = make_manager(tmp_path)
    initial = manager.snapshot()
    first_project, first_chat = initial['active_project_id'], initial['active_conversation_id']
    manager.edit_conversation(first_chat, 'archive', True)
    state = manager.snapshot()
    visible_chat = state['active_conversation_id']
    manager.edit_project(first_project, 'rename', '学习项目')
    manager.edit_project(first_project, 'pin', True)
    second = tmp_path / 'second-project'
    second.mkdir()
    state = manager.create_project({'name': '其他', 'workspace': str(second)})
    second_id = state['active_project_id']
    archived = manager.edit_project(first_project, 'archive', True)
    assert archived['active_project_id'] == second_id
    assert archived['archived_projects'][0]['name'] == '学习项目'
    assert archived['archived_projects'][0]['pinned'] is True
    assert not any(c['project_id'] == first_project for c in archived['conversations'] + archived['archived_conversations'])
    with pytest.raises(ValueError):
        manager.select_conversation(visible_chat)
    with pytest.raises(ValueError):
        manager.edit_conversation(archived['active_conversation_id'], 'move', first_project)
    restored = manager.edit_project(first_project, 'archive', False)
    assert restored['active_project_id'] == second_id
    assert any(c['id'] == visible_chat for c in restored['conversations'])
    assert any(c['id'] == first_chat for c in restored['archived_conversations'])
    assert store.load()['projects'][0]['pinned'] is True


def test_delete_active_project_removes_only_its_history_and_keeps_files(tmp_path):
    manager, store, config, workspace = make_manager(tmp_path)
    first = manager.snapshot()
    first_id, first_chat = first['active_project_id'], first['active_conversation_id']
    second = tmp_path / 'another-project'
    second.mkdir()
    marker = second / 'keep.txt'
    marker.write_text('preserve', encoding='utf-8')
    state = manager.create_project({'name': '删除目标', 'workspace': str(second)})
    target, target_chat = state['active_project_id'], state['active_conversation_id']
    manager.edit_conversation(target_chat, 'archive', True)
    state = manager.edit_project(target, 'delete')
    assert state['active_project_id'] == first_id
    assert state['active_conversation_id'] == first_chat
    assert manager.session.load_config().workspace == workspace.resolve()
    assert marker.read_text(encoding='utf-8') == 'preserve'
    assert all(chat['project_id'] != target for chat in store.load()['conversations'])
    assert target not in {item['id'] for item in store.load()['projects']}


def test_last_project_archive_keeps_empty_state_and_restore_recovers_history(tmp_path):
    manager, store, config, workspace = make_manager(tmp_path)
    first = manager.snapshot()
    first_id, chat = first['active_project_id'], first['active_conversation_id']
    manager.send("归档后仍应保留的历史")
    wait_idle(manager.session)
    state = manager.edit_project(first_id, 'archive', True)
    assert state['projects'] == [] and state['conversations'] == []
    assert state['active_project_id'] is None and state['active_conversation_id'] is None
    assert state['active_project'] is None and state['active_conversation'] is None
    assert state['events'] == [] and not state['lesson']['steps']
    assert state['config'].get('workspace') in (None, '')
    assert state['archived_projects'][0]['id'] == first_id
    assert len(store.load()['projects']) == 1
    restarted = WorkspaceManager(config, store, AgentSession(config))
    assert restarted.snapshot()['projects'] == []
    state = restarted.edit_project(first_id, 'archive', False)
    assert state['active_project_id'] == first_id and state['active_conversation_id'] == chat
    assert any(e['text'] == "归档后仍应保留的历史" for e in state['events'])
    assert restarted.session.load_config().workspace == workspace.resolve()


def test_last_project_delete_stays_empty_after_polling_and_restart(tmp_path):
    manager, store, config, workspace = make_manager(tmp_path)
    first_id = manager.snapshot()['active_project_id']
    marker = workspace / 'keep.txt'
    marker.write_text('workspace files stay', encoding='utf-8')
    manager.send('删除后不应泄漏到空白界面的消息')
    wait_idle(manager.session)
    state = manager.edit_project(first_id, 'delete')
    for current in (state, manager.snapshot(), WorkspaceManager(config, store, AgentSession(config)).snapshot()):
        assert current['projects'] == [] and current['conversations'] == []
        assert current['active_project_id'] is None and current['active_conversation_id'] is None
        assert current['events'] == [] and not current['lesson']['steps']
        assert current['config'].get('workspace') in (None, '')
    assert store.load()['projects'] == [] and store.load()['conversations'] == []
    assert marker.read_text(encoding='utf-8') == 'workspace files stay'


@pytest.mark.parametrize('operation', ['send', 'new_conversation', 'teaching_action', 'submit_attempt'])
def test_no_project_blocks_chat_and_learning_without_creating_fallback(tmp_path, operation):
    manager, store, config, workspace = make_manager(tmp_path, create_initial=False)
    def forbidden(_settings):
        pytest.fail('No-project operations must never start the model')
    manager.session.client_factory = forbidden
    with pytest.raises(ValueError, match='请先创建或选择项目'):
        if operation == 'new_conversation':
            manager.new_conversation()
        else:
            getattr(manager, operation)('测试内容')
    assert manager.snapshot()['projects'] == []
    assert store.load()['conversations'] == []


def test_settings_remain_usable_without_project_or_stale_workspace(tmp_path):
    manager, store, config, old_workspace = make_manager(tmp_path, create_initial=False)
    state = manager.save_config({'model': 'deepseek-chat', 'reasoning_level': 'high', 'permission_mode': 'request'})
    assert state['active_project'] is None and state['projects'] == []
    assert state['config']['model'] == 'deepseek-chat'
    assert state['config']['reasoning_level'] == 'high'
    assert state['config']['permission_mode'] == 'request'
    assert state['config']['api_key_configured']
    assert state['config'].get('workspace') in (None, '')
    assert config.load().api_key == 'test-key'


def test_project_save_failure_rolls_back_active_workspace(tmp_path, monkeypatch):
    manager, store, config, workspace = make_manager(tmp_path)
    first = manager.snapshot()
    second = tmp_path / 'second-failure'
    second.mkdir()
    active = manager.create_project({'name': 'second', 'workspace': str(second)})
    original = store.path.read_bytes()
    def fail_save(_document):
        raise ConversationStoreError('模拟保存失败')
    monkeypatch.setattr(store, 'save', fail_save)
    with pytest.raises(ConversationStoreError):
        manager.edit_project(active['active_project_id'], 'archive', True)
    assert manager.session.load_config().workspace == second.resolve()
    assert store.path.read_bytes() == original
    assert manager._loaded_conversation_id == active['active_conversation_id']


def test_project_flags_validate_and_old_records_default_false(tmp_path):
    manager, store, config, workspace = make_manager(tmp_path)
    manager.snapshot()
    document = store.load()
    project = document['projects'][0]
    del project['pinned']
    del project['archived']
    normalized = store.normalize(document)
    assert normalized['projects'][0]['pinned'] is False
    assert normalized['projects'][0]['archived'] is False
    project['archived'] = 'true'
    with pytest.raises(ConversationStoreError):
        store.normalize(document)


def test_moved_workspace_keeps_history_settings_and_recovery_available(tmp_path):
    manager, store, config, workspace = make_manager(tmp_path)
    manager.send("保留这段聊天")
    wait_idle(manager.session)
    first = manager.snapshot()
    second_path = tmp_path / "second"
    second_path.mkdir()
    second = manager.create_project({"workspace": str(second_path)})
    manager.select_project(first["active_project_id"])
    relocated = tmp_path / "moved-workspace"
    workspace.rename(relocated)

    state = manager.snapshot()
    assert state["workspace_error"] and state["config"]["api_key_configured"]
    assert any(e["text"] == "保留这段聊天" for e in state["events"])
    state = manager.save_config({"model": "changed-model"})
    assert state["config"]["model"] == "changed-model"
    with pytest.raises(AgentConfigError, match="工作区"):
        manager.send("不能调用模型或重新创建旧目录")
    assert not workspace.exists()
    assert manager.session.worker is None

    assert manager.select_project(second["active_project_id"])["workspace_error"] is None
    manager.select_project(first["active_project_id"])
    state = manager.edit_project(first["active_project_id"], "workspace", str(relocated))
    assert state["workspace_error"] is None
    assert manager.session.load_config().workspace == relocated
    assert state["active_conversation_id"] == first["active_conversation_id"]
    assert any(e["text"] == "保留这段聊天" for e in state["events"])
    restarted = WorkspaceManager(config, store, AgentSession(config)).snapshot()
    assert restarted["active_project"]["workspace"] == str(relocated)


def test_missing_active_project_can_be_deleted(tmp_path):
    manager, store, config, workspace = make_manager(tmp_path)
    project_id = manager.snapshot()["active_project_id"]
    workspace.rmdir()
    assert manager.edit_project(project_id, "delete")["active_project_id"] is None
    assert store.load()["projects"] == []


@pytest.mark.parametrize("operation,value", [("send", "消息"), ("submit_attempt", "作答"), ("teaching_action", "hint")])
def test_delayed_chat_actions_cannot_target_another_conversation(tmp_path, operation, value):
    manager, store, config, workspace = make_manager(tmp_path)
    first = manager.snapshot()["active_conversation_id"]
    current = manager.new_conversation()["active_conversation_id"]
    original = store.path.read_bytes()
    with pytest.raises(RuntimeError, match="对话已切换"):
        getattr(manager, operation)(value, conversation_id=first)
    assert manager.session.worker is None
    assert manager.snapshot()["active_conversation_id"] == current
    assert store.path.read_bytes() == original


def test_legacy_and_project_sessions_keep_their_execution_roots(tmp_path):
    manager, store, config, project_root = make_manager(tmp_path)
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (project_root / "marker.txt").write_text("project", encoding="utf-8")
    (legacy_root / "marker.txt").write_text("legacy", encoding="utf-8")

    class FileClient:
        def __init__(self):
            self.first = True

        def complete_tool_call(self, messages, **kwargs):
            if not self.first:
                return SimpleNamespace(content="已检查。", tool_calls=None)
            self.first = False
            calls = [
                {"id": "read", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "marker.txt"})}},
                {"id": "write", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "result.txt", "content": "checked"})}},
            ]
            return SimpleNamespace(content=None, tool_calls=calls)

    legacy = AgentSession(config, client_factory=lambda _: FileClient())
    manager.session.client_factory = lambda _: FileClient()
    legacy.save_config({"workspace": str(legacy_root), "permission_mode": "auto"})
    before_config = config.path.read_bytes()
    for session, expected in [(legacy, "legacy"), (manager.session, "project")]:
        manager.snapshot()  # Refreshing the workbench must not redirect the old page.
        session.send("查看并写入检查结果")
        wait_idle(session)
        result = next(m for m in reversed(session.messages) if m.get("tool_call_id") == "read")
        assert json.loads(result["content"])["content"] == expected
    assert (project_root / "result.txt").read_text(encoding="utf-8") == "checked"
    assert (legacy_root / "result.txt").read_text(encoding="utf-8") == "checked"

    other = tmp_path / "other"
    other.mkdir()
    manager.create_project({"workspace": str(other)})
    assert config.path.read_bytes() == before_config
    manager.save_config({"model": "another-model"})
    assert legacy.load_config().workspace == legacy_root
    assert manager.session.load_config().workspace == other
    assert config.load().workspace == legacy_root
    assert legacy.load_config().model == "another-model"


def test_relocation_does_not_need_to_save_shared_config(tmp_path, monkeypatch):
    manager, store, config, workspace = make_manager(tmp_path)
    before = manager.snapshot()
    config_bytes = config.path.read_bytes()
    relocated = tmp_path / "relocated"
    workspace.rename(relocated)

    def forbidden(_):
        raise AgentConfigError("模拟配置无法保存")

    monkeypatch.setattr(config, "save", forbidden)
    state = manager.edit_project(before["active_project_id"], "workspace", str(relocated))
    assert state["config"]["workspace"] == str(relocated)
    assert state["active_conversation_id"] == before["active_conversation_id"]
    assert manager.session.load_config().workspace == relocated
    assert store.load()["projects"][0]["workspace"] == str(relocated)
    assert config.path.read_bytes() == config_bytes
    restarted = WorkspaceManager(config, store, AgentSession(config))
    assert restarted.snapshot()["config"]["workspace"] == str(relocated)
    assert manager.edit_project(before["active_project_id"], "delete")["active_project_id"] is None


def test_relocation_save_failure_preserves_live_and_persisted_root(tmp_path, monkeypatch):
    manager, store, config, workspace = make_manager(tmp_path)
    before = manager.snapshot()
    original = store.path.read_bytes()
    config_bytes = config.path.read_bytes()
    other = tmp_path / "new-root"
    other.mkdir()

    def fail(_):
        raise ConversationStoreError("模拟项目保存失败")

    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(ConversationStoreError):
        manager.edit_project(before["active_project_id"], "workspace", str(other))
    assert manager.session.load_config().workspace == workspace
    assert store.path.read_bytes() == original
    assert config.path.read_bytes() == config_bytes
    assert manager.snapshot()["version"] == before["version"]


def test_failed_settings_save_keeps_permission_and_version(tmp_path, monkeypatch):
    manager, store, config, _ = make_manager(tmp_path)
    before = manager.snapshot()
    original = config.path.read_bytes()

    def fail(_):
        raise AgentConfigError("模拟配置保存失败")

    monkeypatch.setattr(config, "save", fail)
    with pytest.raises(AgentConfigError):
        manager.save_config({"permission_mode": "full"})
    after = manager.snapshot()
    assert after["version"] == before["version"]
    assert after["config"] == before["config"]
    assert config.path.read_bytes() == original
