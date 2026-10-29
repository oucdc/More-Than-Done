"""Persistent projects and conversations for the Agent workspace."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import uuid

from config import PROJECT_ROOT
from learning.guidance import GuidanceNotebook


class ConversationStoreError(RuntimeError):
    """The project/conversation store is invalid or cannot be written."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: object, name: str, *, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConversationStoreError(f"{name} 不能为空。")
    return value.strip()[:max_length]


class ConversationStore:
    """Use one atomic JSON document for projects, conversations and their history."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else PROJECT_ROOT / "data" / "conversations.json"

    @staticmethod
    def empty() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "projects": [],
            "conversations": [],
            "active_project_id": None,
            "active_conversation_id": None,
        }

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.empty()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ConversationStoreError("Agent 对话记录不是 UTF-8 文本。原文件已保留。") from exc
        except json.JSONDecodeError as exc:
            raise ConversationStoreError(
                f"Agent 对话记录不是有效 JSON（第 {exc.lineno} 行，第 {exc.colno} 列）。"
            ) from exc
        except OSError as exc:
            raise ConversationStoreError("无法读取 Agent 对话记录。原文件已保留。") from exc
        return self.normalize(document)

    def normalize(self, document: object) -> dict[str, Any]:
        if not isinstance(document, dict):
            raise ConversationStoreError("Agent 对话记录顶层必须是 JSON 对象。")
        if document.get("schema_version") != 1:
            raise ConversationStoreError("Agent 对话记录版本不受支持。")

        projects: list[dict[str, Any]] = []
        project_ids: set[str] = set()
        for raw in document.get("projects", []):
            if not isinstance(raw, dict):
                continue
            project_id = raw.get("id")
            workspace = raw.get("workspace")
            if not isinstance(project_id, str) or not project_id:
                continue
            if project_id in project_ids:
                continue
            if not isinstance(workspace, str) or not workspace.strip():
                continue
            name = raw.get("name")
            if not isinstance(name, str) or not name.strip():
                name = Path(workspace).name or "项目"
            for flag in ("pinned", "archived"):
                if type(raw.get(flag, False)) is not bool:
                    raise ConversationStoreError(f"项目中的 {flag} 字段无效，原文件已保留。")
            projects.append({
                "id": project_id,
                "name": name.strip()[:120],
                "pinned": raw.get("pinned", False),
                "archived": raw.get("archived", False),
                "workspace": str(Path(workspace).expanduser().resolve()),
                "created_at": str(raw.get("created_at") or _now()),
            })
            project_ids.add(project_id)

        conversations: list[dict[str, Any]] = []
        conversation_ids: set[str] = set()
        for raw in document.get("conversations", []):
            if not isinstance(raw, dict):
                continue
            conversation_id = raw.get("id")
            project_id = raw.get("project_id")
            if not isinstance(conversation_id, str) or not conversation_id:
                continue
            if conversation_id in conversation_ids or project_id not in project_ids:
                continue
            messages = raw.get("messages", [])
            events = raw.get("events", [])
            if not isinstance(messages, list):
                messages = []
            if not isinstance(events, list):
                events = []
            title = raw.get("title")
            if not isinstance(title, str) or not title.strip():
                title = "新对话"
            for flag in ("pinned", "archived", "title_custom"):
                if type(raw.get(flag, False)) is not bool:
                    raise ConversationStoreError(f"对话中的 {flag} 字段无效，原文件已保留。")
            try:
                lesson = GuidanceNotebook(raw.get("lesson")).export()
            except (ValueError, TypeError) as exc:
                raise ConversationStoreError("对话中的教学记录无效，原文件已保留。") from exc
            conversations.append({
                "id": conversation_id,
                "project_id": project_id,
                "title": title.strip()[:80],
                "title_custom": raw.get("title_custom", False),
                "pinned": raw.get("pinned", False),
                "archived": raw.get("archived", False),
                "created_at": str(raw.get("created_at") or _now()),
                "updated_at": str(raw.get("updated_at") or _now()),
                "messages": [item for item in messages if isinstance(item, dict)][-200:],
                "events": [item for item in events if isinstance(item, dict)][-500:],
                "lesson": lesson,
            })
            conversation_ids.add(conversation_id)

        active_project_id = document.get("active_project_id")
        visible_projects = [project for project in projects if not project["archived"]]
        if active_project_id not in {project["id"] for project in visible_projects}:
            active_project_id = visible_projects[0]["id"] if visible_projects else None
        active_conversation_id = document.get("active_conversation_id")
        active_project_conversations = [
            conversation for conversation in conversations
            if conversation["project_id"] == active_project_id and not conversation["archived"]
        ]
        if active_conversation_id not in {item["id"] for item in active_project_conversations}:
            active_conversation_id = (
                max(active_project_conversations, key=lambda item: item["updated_at"])["id"]
                if active_project_conversations else None
            )

        return {
            "schema_version": 1,
            "projects": projects,
            "conversations": conversations,
            "active_project_id": active_project_id,
            "active_conversation_id": active_conversation_id,
        }

    def save(self, document: dict[str, Any]) -> None:
        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(encoded)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise ConversationStoreError("无法保存 Agent 对话记录。请检查 data 目录是否可写。") from exc

    @staticmethod
    def make_project(name: object, workspace: object) -> dict[str, Any]:
        if not isinstance(workspace, str) or not workspace.strip():
            raise ConversationStoreError("项目工作区不能为空。")
        path = Path(workspace).expanduser().resolve()
        if not path.is_dir():
            raise ConversationStoreError(f"项目工作区不存在或不是目录：{path}")
        if name is None or (isinstance(name, str) and not name.strip()):
            project_name = path.name or "项目"
        elif isinstance(name, str):
            project_name = name.strip()
        else:
            raise ConversationStoreError("项目名称必须是文本。")
        return {
            "id": uuid.uuid4().hex,
            "name": project_name[:120],
            "pinned": False,
            "archived": False,
            "workspace": str(path),
            "created_at": _now(),
        }

    @staticmethod
    def make_conversation(project_id: str, title: str = "新对话") -> dict[str, Any]:
        timestamp = _now()
        return {
            "id": uuid.uuid4().hex,
            "project_id": project_id,
            "title": _text(title, "对话标题", max_length=80),
            "title_custom": False,
            "pinned": False,
            "archived": False,
            "created_at": timestamp,
            "updated_at": timestamp,
            "messages": [],
            "events": [],
            "lesson": GuidanceNotebook.empty(),
        }
