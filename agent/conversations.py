"""Persistent projects and conversations for the Agent workspace."""

from __future__ import annotations

from datetime import datetime
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
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
        self._cache_lock = threading.RLock()
        self._cache_signature: tuple[int, int, int, int] | None = None
        self._cache_document: dict[str, Any] | None = None

    def _file_signature(self) -> tuple[int, int, int, int]:
        stat = self.path.stat()
        return stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, stat.st_ino

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
        with self._cache_lock:
            return self._load_locked()

    def _load_locked(self) -> dict[str, Any]:
        try:
            signature = self._file_signature()
            if signature == self._cache_signature and self._cache_document is not None:
                return deepcopy(self._cache_document)
            document = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            self._cache_signature = None
            self._cache_document = None
            return self.empty()
        except UnicodeDecodeError as exc:
            raise ConversationStoreError("Agent 对话记录不是 UTF-8 文本。原文件已保留。") from exc
        except json.JSONDecodeError as exc:
            raise ConversationStoreError(
                f"Agent 对话记录不是有效 JSON（第 {exc.lineno} 行，第 {exc.colno} 列）。"
            ) from exc
        except OSError as exc:
            raise ConversationStoreError("无法读取 Agent 对话记录。原文件已保留。") from exc
        normalized = self.normalize(document)
        # An external atomic replacement during this read must be loaded again
        # on the next request, rather than cached under the newer file's stamp.
        try:
            unchanged = self._file_signature() == signature
        except OSError:
            unchanged = False
        self._cache_signature = signature if unchanged else None
        self._cache_document = deepcopy(normalized) if unchanged else None
        return normalized

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
                "messages": [item for item in messages if isinstance(item, dict)],
                "events": [item for item in events if isinstance(item, dict)],
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
        with self._cache_lock:
            self._save_locked(document)

    def _save_locked(self, document: dict[str, Any]) -> None:
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
            self._cache_signature = None
            self._cache_document = None
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
