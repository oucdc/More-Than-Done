"""Versioned JSON persistence for the current learning state."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

from learning.state import LearningState


SCHEMA_VERSION = 4
APPLICATION_DIRECTORY = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[1]
)
DEFAULT_STATE_PATH = APPLICATION_DIRECTORY / "data" / "learning_state.json"


class LearningStoreError(RuntimeError):
    """Base class for local learning-data storage failures."""


class LearningDataFormatError(LearningStoreError):
    """The saved file is corrupt, incomplete, or invalid for this schema."""


class UnsupportedSchemaVersionError(LearningStoreError):
    """The saved file uses a schema version this application cannot read."""


class LearningStoreReadError(LearningStoreError):
    """The learning-data file exists but could not be read."""


class LearningStoreWriteError(LearningStoreError):
    """The learning state could not be saved."""


class JsonLearningStore:
    """Save or restore one immutable learning state using an atomic JSON file."""

    def __init__(self, path: str | Path = DEFAULT_STATE_PATH) -> None:
        self.path = Path(path)

    def save(self, state: LearningState) -> None:
        """Atomically replace the saved state after writing a complete JSON file."""
        if not isinstance(state, LearningState):
            raise TypeError("state must be a LearningState value.")

        document = {
            "schema_version": SCHEMA_VERSION,
            "state": state.to_dict(),
        }
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
        except OSError as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise LearningStoreWriteError(
                f"无法保存学习数据到 {self.path}。请检查目录是否可写。"
            ) from exc

    def load(self) -> LearningState | None:
        """Return None for a missing file; reject corrupt or incomplete data."""
        try:
            content = self.path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError as exc:
            raise LearningDataFormatError("学习数据不是有效 UTF-8 文本。原文件已保留。") from exc
        except OSError as exc:
            raise LearningStoreReadError(
                f"无法读取学习数据文件 {self.path}。原文件已保留。"
            ) from exc

        try:
            document = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LearningDataFormatError(
                f"学习数据不是有效 JSON（第 {exc.lineno} 行，第 {exc.colno} 列）。原文件已保留。"
            ) from exc

        if not isinstance(document, dict):
            raise LearningDataFormatError("学习数据顶层必须是 JSON 对象。原文件已保留。")
        if "schema_version" not in document:
            raise LearningDataFormatError("学习数据缺少 schema_version。原文件已保留。")

        version = document["schema_version"]
        if type(version) is not int:
            raise LearningDataFormatError("schema_version 必须是整数。原文件已保留。")
        if version not in {1, 2, 3, SCHEMA_VERSION}:
            raise UnsupportedSchemaVersionError(
                f"学习数据版本 {version} 不受支持；当前支持版本为 1、2、3 和 {SCHEMA_VERSION}。原文件已保留。"
            )

        if set(document) != {"schema_version", "state"}:
            raise LearningDataFormatError(
                "学习数据缺少 state 或包含不支持的顶层字段。原文件已保留。"
            )

        state_payload = document["state"]
        if version in {1, 2, 3}:
            if not isinstance(state_payload, dict):
                raise LearningDataFormatError("保存的学习状态必须是 JSON 对象。原文件已保留。")
            try:
                state_payload = self._migrate_legacy_state(version, state_payload)
            except (TypeError, ValueError) as exc:
                raise LearningDataFormatError(
                    f"保存的学习状态无法迁移：{exc} 原文件已保留。"
                ) from exc

        try:
            return LearningState.from_dict(state_payload)
        except (TypeError, ValueError) as exc:
            raise LearningDataFormatError(
                f"保存的学习状态不完整或无效：{exc} 原文件已保留。"
            ) from exc

    def load_or_start(self) -> LearningState:
        """Return the saved state, or a fresh state when no file exists."""
        state = self.load()
        return state if state is not None else LearningState()

    def archive(self) -> Path | None:
        """Copy the current save into archive/ using a collision-safe UTC name."""
        try:
            content = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise LearningStoreWriteError(
                f"无法读取学习数据以创建归档：{self.path}。"
            ) from exc

        archive_directory = self.path.parent / "archive"
        temporary_path: Path | None = None
        try:
            archive_directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            base_name = f"{self.path.stem}-{timestamp}"
            target_path = archive_directory / f"{base_name}.json"
            suffix = 1
            while target_path.exists():
                target_path = archive_directory / f"{base_name}-{suffix}.json"
                suffix += 1

            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{base_name}.",
                suffix=".tmp",
                dir=archive_directory,
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, target_path)
            return target_path
        except OSError as exc:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise LearningStoreWriteError(
                f"无法创建学习记录归档到 {archive_directory}。"
            ) from exc

    @staticmethod
    def _migrate_legacy_state(version: int, legacy: dict[str, object]) -> dict[str, object]:
        """Upgrade recognized historical layouts in memory without rewriting the file."""
        payload = dict(legacy)
        legacy_answer_flags = (
            "answer_viewed_in_independent",
            "answer_viewed_in_mastery",
            "answer_viewed_in_guided",
        )
        viewed = payload.pop("answer_viewed_for_current_exercise", False)
        legacy_viewed_values = [payload.pop(field_name, False) for field_name in legacy_answer_flags]
        if type(viewed) is not bool or any(type(value) is not bool for value in legacy_viewed_values):
            raise ValueError("legacy answer-viewed fields must be booleans.")
        viewed = viewed or any(legacy_viewed_values)

        # Stage 5 persisted the core event and answer fields; stage 6 added these.
        # Some deployed legacy files can also be partial, so apply the safe defaults
        # to every recognized legacy layout.
        payload.setdefault("independent_practice_skipped", False)
        payload.setdefault("practice_attempts", [])
        # Some deployed v2 files have active_exercise while the earlier v2 layout does not.
        payload.setdefault("active_exercise", None)

        recent = payload.pop("recent_exercises", [])
        if version == 2 and not recent and payload.get("active_exercise"):
            recent = [payload["active_exercise"]]
        if version == 3:
            recent = [payload["active_exercise"]] if payload.get("active_exercise") else []
        payload["recent_exercises"] = recent
        payload.update({
            "answer_viewed_for_current_exercise": viewed,
            "mastery_exercise_repeated": payload.pop("mastery_exercise_repeated", False),
            "active_success_criteria": payload.pop("active_success_criteria", None),
            "active_reference_answer": payload.pop("active_reference_answer", None),
            "baseline_result": payload.pop("baseline_result", None),
            "learner_level": payload.pop("learner_level", None),
        })
        return payload
