"""Codex-style projects, conversations and active workspace state."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading
from typing import Any

from agent.agent_config import AgentConfigError, AgentConfigStore
from agent.command_environment import describe_environment
from agent.conversations import ConversationStore, ConversationStoreError
from agent.local_agent import AgentSession
from config import PROJECT_ROOT


MODEL_OPTIONS = [
    {"id": "deepseek-flash", "label": "DeepSeek Flash", "note": "默认，支持工具调用"},
    {"id": "deepseek-chat", "label": "DeepSeek Chat", "note": "通用对话模型"},
    {
        "id": "deepseek-reasoner",
        "label": "DeepSeek Reasoner",
        "note": "推理模型；工具调用兼容性取决于服务端",
    },
]

REASONING_LEVELS = [
    {"id": "low", "label": "低", "note": "快速、简洁"},
    {"id": "medium", "label": "中", "note": "平衡（默认）"},
    {"id": "high", "label": "高", "note": "更深入，输出预算更大"},
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class WorkspaceManager:
    """Persist Codex-like projects and switch the one active Agent conversation."""

    def __init__(
        self,
        config_store: AgentConfigStore,
        conversation_store: ConversationStore,
        session: AgentSession,
    ) -> None:
        self.config_store = config_store
        self.conversation_store = conversation_store
        self.session = session
        self.lock = threading.RLock()
        self._loaded_conversation_id: str | None = None
        self.revision = 0
        self.session.on_update = self._persist_session

    def _persist_session(self) -> None:
        with self.lock:
            document = self.conversation_store.load()
            self._persist_locked(document)

    def _active_project_locked(self, document: dict[str, Any]) -> dict[str, Any] | None:
        active_id = document.get("active_project_id")
        return next(
            (project for project in document["projects"] if project["id"] == active_id),
            None,
        )

    def _active_conversation_locked(self, document: dict[str, Any]) -> dict[str, Any] | None:
        active_id = document.get("active_conversation_id")
        return next(
            (conversation for conversation in document["conversations"] if conversation["id"] == active_id),
            None,
        )

    def _assert_idle_locked(self) -> None:
        if self.session.worker is not None and self.session.worker.is_alive():
            raise RuntimeError("当前 Agent 仍在运行，请先取消或等待完成。")

    def _sync_workspace_locked(self, workspace: str) -> None:
        config = self.config_store.load()
        if config.workspace != Path(workspace):
            self.config_store.update({"workspace": workspace})

    def _load_active_locked(self, conversation: dict[str, Any]) -> None:
        if self._loaded_conversation_id == conversation["id"]:
            return
        self.session.load_conversation(
            conversation.get("messages", []),
            conversation.get("events", []),
            conversation.get("lesson"),
        )
        self._loaded_conversation_id = conversation["id"]

    def _clear_active_locked(self) -> None:
        if self._loaded_conversation_id is not None or self.session.events or self.session.messages or self.session.lesson.public()["steps"]:
            self.session.load_conversation([], [], None)
        self._loaded_conversation_id = None

    def _require_project_locked(self) -> dict[str, Any]:
        document = self._ensure_locked()
        if self._active_project_locked(document) is None:
            raise ValueError("请先创建或选择项目。")
        return document

    def _ensure_locked(self) -> dict[str, Any]:
        document = self.conversation_store.load()
        changed = False

        project = self._active_project_locked(document)
        if project is None:
            # No visible project is a valid durable state; never recreate one
            # from the last command workspace merely to fill the sidebar.
            document["active_project_id"] = None
            document["active_conversation_id"] = None
            self._clear_active_locked()
            return document

        project_conversations = [
            conversation
            for conversation in document["conversations"]
            if conversation["project_id"] == project["id"] and not conversation.get("archived", False)
        ]
        conversation = self._active_conversation_locked(document)
        if conversation is None or conversation["project_id"] != project["id"] or conversation.get("archived", False):
            if project_conversations:
                conversation = max(project_conversations, key=lambda item: item["updated_at"])
            else:
                conversation = ConversationStore.make_conversation(project["id"])
                document["conversations"].append(conversation)
            document["active_conversation_id"] = conversation["id"]
            changed = True

        if changed:
            self.conversation_store.save(document)
        self._sync_workspace_locked(project["workspace"])
        self._load_active_locked(conversation)
        return document

    def _persist_locked(self, document: dict[str, Any]) -> None:
        conversation = self._active_conversation_locked(document)
        project = self._active_project_locked(document)
        if conversation is None or project is None:
            return
        messages, events = self.session.export_conversation()
        lesson = self.session.lesson.export()
        if conversation.get("messages") == messages and conversation.get("events") == events and conversation.get("lesson") == lesson:
            return
        conversation["messages"] = messages
        conversation["events"] = events
        conversation["lesson"] = lesson
        if not conversation.get("title_custom", False) and conversation.get("title") in (None, "", "新对话"):
            for message in messages:
                if message.get("role") == "user" and isinstance(message.get("content"), str):
                    title = message["content"].strip().replace("\n", " ")
                    if title:
                        conversation["title"] = title[:48]
                    break
        conversation["updated_at"] = _now()
        document["active_project_id"] = project["id"]
        document["active_conversation_id"] = conversation["id"]
        self.conversation_store.save(document)

    def _state_locked(self) -> dict[str, Any]:
        document = self._ensure_locked()
        if self.session.worker is None or not self.session.worker.is_alive():
            self._persist_locked(document)
        state = self.session.snapshot()
        project = self._active_project_locked(document)
        conversation = self._active_conversation_locked(document)
        if project is None:
            state["config"] = {**state.get("config", {}), "workspace": ""}
        projects = []
        for item in document["projects"]:
            projects.append({
                **item,
                "total_conversation_count": sum(1 for chat in document["conversations"] if chat["project_id"] == item["id"]),
                "conversation_count": sum(
                    1 for chat in document["conversations"] if chat["project_id"] == item["id"] and not chat.get("archived", False)
                ),
            })
        conversations = sorted(
            document["conversations"],
            key=lambda item: item["updated_at"],
            reverse=True,
        )
        summaries = [
            {
                "id": item["id"],
                "project_id": item["project_id"],
                "title": item["title"],
                "updated_at": item["updated_at"],
                "message_count": len(item.get("messages", [])),
                "pinned": item.get("pinned", False),
                "archived": item.get("archived", False),
            }
            for item in conversations
        ]
        active_summary = next(
            (
                item for item in summaries
                if conversation is not None and item["id"] == conversation["id"]
            ),
            None,
        )
        return {
            **state,
            "projects": [item for item in projects if not item.get("archived", False)],
            "archived_projects": [item for item in projects if item.get("archived", False)],
            "conversations": [item for item in summaries if not item["archived"] and item["project_id"] in {p["id"] for p in projects if not p.get("archived", False)}],
            "archived_conversations": [item for item in summaries if item["archived"] and item["project_id"] in {p["id"] for p in projects if not p.get("archived", False)}],
            "active_project_id": project["id"] if project else None,
            "active_conversation_id": conversation["id"] if conversation else None,
            "active_project": project,
            "active_conversation": active_summary,
            "environment": describe_environment(Path(project["workspace"])) if project else {},
            "models": MODEL_OPTIONS,
            "reasoning_levels": REASONING_LEVELS,
            "workspace_version": self.revision,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return self._state_locked()

    def create_project(self, payload: object) -> dict[str, Any]:
        with self.lock:
            self._ensure_locked()
            self._assert_idle_locked()
            if not isinstance(payload, dict):
                raise ValueError("项目参数必须是 JSON 对象。")
            project = ConversationStore.make_project(payload.get("name"), payload.get("workspace"))
            conversation = ConversationStore.make_conversation(project["id"])
            document = self.conversation_store.load()
            self._persist_locked(document)
            document["projects"].append(project)
            document["conversations"].append(conversation)
            document["active_project_id"] = project["id"]
            document["active_conversation_id"] = conversation["id"]
            self.conversation_store.save(document)
            self._loaded_conversation_id = None
            self._sync_workspace_locked(project["workspace"])
            self._load_active_locked(conversation)
            return self._state_locked()

    def select_project(self, project_id: object) -> dict[str, Any]:
        with self.lock:
            document = self._ensure_locked()
            self._assert_idle_locked()
            if not isinstance(project_id, str):
                raise ValueError("项目 ID 无效。")
            project = next(
                (item for item in document["projects"] if item["id"] == project_id),
                None,
            )
            if project is None or project.get("archived", False):
                raise ValueError("项目不存在或已归档。")
            conversations = [
                item for item in document["conversations"]
                if item["project_id"] == project["id"] and not item.get("archived", False)
            ]
            self._persist_locked(document)
            if conversations:
                conversation = max(conversations, key=lambda item: item["updated_at"])
            else:
                conversation = ConversationStore.make_conversation(project["id"])
                document["conversations"].append(conversation)
            document["active_project_id"] = project["id"]
            document["active_conversation_id"] = conversation["id"]
            self.conversation_store.save(document)
            self._loaded_conversation_id = None
            self._sync_workspace_locked(project["workspace"])
            self._load_active_locked(conversation)
            return self._state_locked()

    def new_conversation(self, project_id: object = None) -> dict[str, Any]:
        with self.lock:
            document = self._require_project_locked()
            self._assert_idle_locked()
            active_project = self._active_project_locked(document)
            target_project_id = project_id if isinstance(project_id, str) else (
                active_project["id"] if active_project else None
            )
            if target_project_id is None:
                raise ValueError("没有可用项目。")
            project = next(
                (item for item in document["projects"] if item["id"] == target_project_id),
                None,
            )
            if project is None or project.get("archived", False):
                raise ValueError("项目不存在或已归档。")
            conversation = ConversationStore.make_conversation(project["id"])
            self._persist_locked(document)
            document["conversations"].append(conversation)
            document["active_project_id"] = project["id"]
            document["active_conversation_id"] = conversation["id"]
            self.conversation_store.save(document)
            self._loaded_conversation_id = None
            self._sync_workspace_locked(project["workspace"])
            self._load_active_locked(conversation)
            return self._state_locked()

    def select_conversation(self, conversation_id: object) -> dict[str, Any]:
        with self.lock:
            document = self._ensure_locked()
            self._assert_idle_locked()
            if not isinstance(conversation_id, str):
                raise ValueError("对话 ID 无效。")
            conversation = next(
                (item for item in document["conversations"] if item["id"] == conversation_id),
                None,
            )
            if conversation is None:
                raise ValueError("对话不存在。")
            if conversation.get("archived", False):
                raise ValueError("请先恢复已归档的对话。")
            project = next(
                (item for item in document["projects"] if item["id"] == conversation["project_id"]),
                None,
            )
            if project is None or project.get("archived", False):
                raise ValueError("对话所属项目不存在或已归档。")
            self._persist_locked(document)
            document["active_project_id"] = project["id"]
            document["active_conversation_id"] = conversation["id"]
            self.conversation_store.save(document)
            self._loaded_conversation_id = None
            self._sync_workspace_locked(project["workspace"])
            self._load_active_locked(conversation)
            return self._state_locked()

    def edit_project(self, project_id: object, action: str, value: object = None) -> dict[str, Any]:
        """Update project metadata or remove the project and its conversations."""
        with self.lock:
            document = self._ensure_locked()
            self._assert_idle_locked()
            if not isinstance(project_id, str) or not project_id:
                raise ValueError("项目 ID 无效。")
            target = next((item for item in document["projects"] if item["id"] == project_id), None)
            if target is None:
                raise ValueError("项目不存在或已被删除。")
            if action == "rename":
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
                    raise ValueError("项目名称须为 1 到 120 个字符。")
            elif action in {"pin", "archive"}:
                if type(value) is not bool:
                    raise ValueError("操作参数无效。")
                if action == "pin" and target.get("archived", False):
                    raise ValueError("请先恢复已归档的项目。")
            elif action != "delete":
                raise ValueError("不支持此项目操作。")

            self._persist_locked(document)
            active = document["active_project_id"] == project_id
            if action == "rename":
                target["name"] = value.strip()
            elif action == "pin":
                target["pinned"] = value
            elif action == "archive":
                target["archived"] = value
            else:
                document["projects"] = [item for item in document["projects"] if item["id"] != project_id]
                document["conversations"] = [item for item in document["conversations"] if item["project_id"] != project_id]

            replacement = None
            old_workspace = None
            removing_active = active and (action == "delete" or (action == "archive" and value))
            restoring_empty = action == "archive" and value is False and document["active_project_id"] is None
            if removing_active or restoring_empty:
                project = target if restoring_empty else next((item for item in document["projects"] if not item.get("archived", False)), None)
                old_workspace = self.config_store.load().workspace
                if project is None:
                    document["active_project_id"] = None
                    document["active_conversation_id"] = None
                    self._sync_workspace_locked(str(PROJECT_ROOT))
                else:
                    choices = [item for item in document["conversations"] if item["project_id"] == project["id"] and not item.get("archived", False)]
                    replacement = max(choices, key=lambda item: item["updated_at"]) if choices else ConversationStore.make_conversation(project["id"])
                    if not choices:
                        document["conversations"].append(replacement)
                    document["active_project_id"] = project["id"]
                    document["active_conversation_id"] = replacement["id"]
                    self._sync_workspace_locked(project["workspace"])
            try:
                self.conversation_store.save(document)
            except Exception:
                if old_workspace is not None:
                    self._sync_workspace_locked(str(old_workspace))
                raise
            if replacement is not None:
                self._loaded_conversation_id = None
                self._load_active_locked(replacement)
            if removing_active and replacement is None:
                self._clear_active_locked()
            self.revision += 1
            self.session.notify_state_change()
            return self._state_locked()

    def delete_conversation(self, conversation_id: object) -> dict[str, Any]:
        """Delete a single chat, retaining a valid active chat in its project."""
        with self.lock:
            document = self._ensure_locked()
            self._assert_idle_locked()
            if not isinstance(conversation_id, str) or not conversation_id:
                raise ValueError("对话 ID 无效。")
            target = next((item for item in document["conversations"] if item["id"] == conversation_id), None)
            if target is None:
                raise ValueError("对话不存在或已被删除。")
            self._persist_locked(document)
            was_active = document["active_conversation_id"] == conversation_id
            document["conversations"] = [item for item in document["conversations"] if item["id"] != conversation_id]
            replacement = None
            if was_active:
                remaining = [item for item in document["conversations"] if item["project_id"] == target["project_id"] and not item.get("archived", False)]
                if remaining:
                    replacement = max(remaining, key=lambda item: item["updated_at"])
                else:
                    replacement = ConversationStore.make_conversation(target["project_id"])
                    document["conversations"].append(replacement)
                document["active_conversation_id"] = replacement["id"]
            # Save before replacing the in-memory chat, so a failed write keeps it intact.
            self.conversation_store.save(document)
            if replacement is not None:
                self._loaded_conversation_id = None
                self._load_active_locked(replacement)
            self.revision += 1
            self.session.notify_state_change()
            return self._state_locked()

    def edit_conversation(self, conversation_id: object, action: str, value: object = None) -> dict[str, Any]:
        """Rename, pin, archive, restore, or move one persisted conversation."""
        with self.lock:
            document = self._ensure_locked()
            self._assert_idle_locked()
            if not isinstance(conversation_id, str) or not conversation_id:
                raise ValueError("对话 ID 无效。")
            target = next((item for item in document["conversations"] if item["id"] == conversation_id), None)
            if target is None:
                raise ValueError("对话不存在或已被删除。")
            if action == "rename":
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
                    raise ValueError("对话名称须为 1 到 80 个字符。")
            elif action in {"pin", "archive"}:
                if type(value) is not bool:
                    raise ValueError("操作参数无效。")
                if action == "pin" and target.get("archived", False):
                    raise ValueError("请先恢复已归档的对话。")
            elif action == "move":
                if target.get("archived", False):
                    raise ValueError("请先恢复已归档的对话。")
                project = next((item for item in document["projects"] if item["id"] == value), None)
                if project is None or project.get("archived", False):
                    raise ValueError("目标项目不存在或已归档。")
                if not Path(project["workspace"]).is_dir():
                    raise ValueError("目标项目的工作区不存在。")
            else:
                raise ValueError("不支持此对话操作。")

            self._persist_locked(document)
            active = document["active_conversation_id"] == conversation_id
            replacement = None
            old_workspace = None
            if action == "rename":
                target["title"] = value.strip()
                target["title_custom"] = True
            elif action == "pin":
                target["pinned"] = value
            elif action == "archive":
                target["archived"] = value
                if value and active:
                    choices = [item for item in document["conversations"] if item["project_id"] == target["project_id"] and not item.get("archived", False)]
                    replacement = max(choices, key=lambda item: item["updated_at"]) if choices else ConversationStore.make_conversation(target["project_id"])
                    if not choices:
                        document["conversations"].append(replacement)
                    document["active_conversation_id"] = replacement["id"]
            elif action == "move":
                if active:
                    old_workspace = self.config_store.load().workspace
                    self._sync_workspace_locked(project["workspace"])
                    document["active_project_id"] = project["id"]
                target["project_id"] = project["id"]

            try:
                self.conversation_store.save(document)
            except Exception:
                if old_workspace is not None:
                    self._sync_workspace_locked(str(old_workspace))
                raise
            if replacement is not None:
                self._loaded_conversation_id = None
                self._load_active_locked(replacement)
            elif action == "move" and active:
                self.session.invalidate_system_prompt()
            self.revision += 1
            self.session.notify_state_change()
            return self._state_locked()

    def save_config(self, payload: object) -> dict[str, Any]:
        with self.lock:
            document = self._ensure_locked()
            self._assert_idle_locked()
            if not isinstance(payload, dict):
                raise ValueError("配置内容必须是 JSON 对象。")
            project = self._active_project_locked(document)
            values = dict(payload)
            values["workspace"] = project["workspace"] if project else str(PROJECT_ROOT)
            self.config_store.update(values)
            self.session.invalidate_system_prompt()
            return self._state_locked()

    def send(self, message: object) -> dict[str, Any]:
        with self.lock:
            self._require_project_locked()
            self.session.send(message)
            return self._state_locked()

    def approve(self, approval_id: object, allow: object) -> dict[str, Any]:
        with self.lock:
            self._ensure_locked()
            self.session.approve(approval_id, allow)
            return self._state_locked()

    def cancel(self) -> dict[str, Any]:
        with self.lock:
            self._ensure_locked()
            self.session.cancel()
            return self._state_locked()

    def teaching_action(self, action: object) -> dict[str, Any]:
        with self.lock:
            self._require_project_locked()
            self.session.teaching_action(action)
            return self._state_locked()

    def submit_attempt(self, answer: object) -> dict[str, Any]:
        with self.lock:
            self._require_project_locked()
            self.session.send(answer, intent="attempt")
            return self._state_locked()
