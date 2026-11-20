"""Capture learner-selected text artifacts and disposable practice copies."""

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4

from agent.tools import ToolRuntime, is_sensitive_path
from learning.guidance import artifact_snapshots


def _write_text(path, content):
    """Replace one practice file atomically while retaining exact line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="wb") as output:
            temporary = Path(output.name)
            output.write(content.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _reject_link(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("练习目录含链接，已停止清理或还原。")


def _practice_folder(workspace, data_directory):
    root = Path(data_directory).expanduser().resolve()
    parent = root / "practice"
    raw = Path(workspace).expanduser().absolute()
    if (raw.name != "workspace" or raw.parent.parent != parent
            or re.fullmatch(r"[0-9a-f]{32}", raw.parent.name) is None):
        raise ValueError("练习目录路径无效。")
    for path in (parent, raw.parent, raw):
        _reject_link(path)
    resolved = raw.resolve()
    folder = resolved.parent
    if (resolved != raw or folder.parent != parent.resolve()
            or not folder.is_relative_to(root)):
        raise ValueError("练习目录超出本机数据目录。")
    return folder, resolved


def discard_practice(workspace, data_directory):
    """Remove only one generated practice UUID directory, never linked targets."""
    folder, workspace = _practice_folder(workspace, data_directory)
    if not folder.exists():
        return
    for current, directories, filenames in os.walk(folder, followlinks=False):
        for name in directories + filenames:
            _reject_link(Path(current) / name)
    # Both the resolved boundary and every directory entry were checked above.
    shutil.rmtree(folder)


def capture_artifacts(config, paths):
    if paths is None:
        return []
    if not isinstance(paths, list) or len(paths) > 3:
        raise ValueError("一次最多提交 3 个文本文件。")
    runtime = ToolRuntime(config)
    captured = []
    seen = set()
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("作品路径不能为空。")
        path = (config.workspace / raw).resolve()
        if not path.is_relative_to(config.workspace.resolve()):
            raise ValueError("请选择当前工作区内的作品文件。")
        if path in seen:
            continue
        if path.suffix.casefold() == ".pdf":
            raise ValueError("作品提交目前支持 UTF-8 文本文件。")
        result = json.loads(runtime.execute("read_file", {"path": str(path), "max_chars": 20000}))
        if not result.get("ok"):
            raise ValueError(result.get("error", "作品无法读取。"))
        if result.get("truncated"):
            raise ValueError("单个作品最多 20,000 个字符，请提交较小的文本文件。")
        content = result["content"]
        captured.append({
            "path": path.relative_to(config.workspace.resolve()).as_posix(),
            "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content": content,
            "size": len(content.encode("utf-8")),
            "checks": [{"name": "文件可读取且快照完整", "passed": True},
                       {"name": "文件包含文本", "passed": bool(content.strip())}],
        })
        seen.add(path)
    return captured


def search_attachment_paths(config, query):
    """List matching workspace paths without loading their contents."""
    if not config.allow_read:
        raise ValueError("当前权限不允许读取工作区文件。")
    query = query[:300].replace("\\", "/").casefold()
    root = config.workspace.resolve()
    matches = []
    scanned = 0
    for current, directories, files in os.walk(root):
        folder = Path(current)
        directories[:] = sorted(name for name in directories
                                if not name.startswith(".") and name not in {"node_modules", "__pycache__", "venv"}
                                and (folder / name).resolve() == folder / name)
        scanned += len(directories) + len(files)
        for name in sorted(files):
            path = folder / name
            relative = path.relative_to(root).as_posix()
            if query not in relative.casefold() or name.startswith(".") or path.suffix.casefold() == ".pdf":
                continue
            try:
                if path.resolve() == path and path.is_file() and not is_sensitive_path(path) and path.stat().st_size <= 80_000:
                    matches.append(relative)
            except OSError:
                continue
            if len(matches) >= 30:
                return matches
        if scanned >= 10_000:
            break
    return matches


def capture_chat_attachments(config, attachments):
    """Resolve workspace references and validate text explicitly selected by the user."""
    if not isinstance(attachments, list) or len(attachments) > 3:
        raise ValueError("每条消息最多附加 3 个文本文件。")
    paths = []
    captured = []
    root = config.workspace.resolve()
    for item in attachments:
        if not isinstance(item, dict):
            raise ValueError("附件格式无效。")
        if set(item) == {"path"}:
            raw = item["path"]
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096 or "\0" in raw:
                raise ValueError("附件路径无效。")
            path = root / raw
            if not path.resolve().is_relative_to(root):
                raise ValueError("请选择当前工作区内的文件。")
            if is_sensitive_path(path):
                raise ValueError("不能附加凭据文件。")
            paths.append(raw)
        elif set(item) == {"name", "content"}:
            name, content = item["name"], item["content"]
            if (not isinstance(name, str) or not name.strip() or len(name) > 255
                    or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF or char in "/\\:" for char in name)):
                raise ValueError("附件文件名无效。")
            if name.casefold() == ".env.example" or is_sensitive_path(Path(name)):
                raise ValueError("不能附加凭据文件。")
            captured.append({"name": name, "content": content})
        else:
            raise ValueError("附件格式无效。")
    captured.extend({"name": item["path"], "content": item["content"]}
                    for item in capture_artifacts(config, paths))
    for item in captured:
        content = item["content"]
        if (not isinstance(content, str) or "\0" in content
                or any(0xD800 <= ord(char) <= 0xDFFF for char in content)):
            raise ValueError("附件仅支持 UTF-8 文本文件。")
        if len(content) > 20_000:
            raise ValueError("单个附件最多 20,000 个字符。")
    return captured


def create_practice(config, paths, data_directory):
    artifacts = capture_artifacts(config, paths)
    if not artifacts:
        raise ValueError("请先选择需要练习的文本文件。")
    parent = Path(data_directory).expanduser().resolve() / "practice"
    _reject_link(parent)
    parent.mkdir(parents=True, exist_ok=True)
    folder = parent / uuid4().hex
    folder.mkdir()  # Exclusive ownership is required before failure cleanup.
    workspace = folder / "workspace"
    try:
        workspace.mkdir()
        for artifact in artifacts:
            target = workspace / artifact["path"]
            _write_text(target, artifact["content"])
        _write_text(folder / "originals.json", json.dumps(artifacts, ensure_ascii=False))
    except Exception as creation_error:
        try:
            discard_practice(workspace, data_directory)
        except (OSError, ValueError) as cleanup_error:
            raise OSError(f"创建练习失败：{creation_error}。临时目录未能清理，请保留并检查 {folder}：{cleanup_error}") from creation_error
        raise
    return workspace


def practice_state(workspace, data_directory, *, reset=False):
    try:
        folder, workspace = _practice_folder(workspace, data_directory)
    except ValueError:
        return {"available": False, "diffs": []}
    originals = folder / "originals.json"
    _reject_link(originals)
    if not originals.is_file():
        return {"available": False, "diffs": []}
    if originals.stat().st_size > 1_000_000:
        raise ValueError("练习初始记录过大，无法读取。")
    artifacts = artifact_snapshots(json.loads(originals.read_text(encoding="utf-8")))
    diffs = []
    changes = []
    for artifact in artifacts:
        raw_target = workspace / artifact["path"]
        for candidate in (raw_target, *raw_target.parents):
            if candidate == workspace:
                break
            _reject_link(candidate)
        target = raw_target.resolve()
        if not target.is_relative_to(workspace):
            raise ValueError("练习文件路径无效。")
        if target.is_file() and target.stat().st_size > 200000:
            raise ValueError("练习文件过大，请将文件控制在 200KB 内。")
        existed = target.is_file()
        content = target.read_bytes().decode("utf-8") if existed else ""
        diff = "".join(difflib.unified_diff(artifact["content"].splitlines(True), content.splitlines(True),
                                            fromfile="练习初始内容", tofile="当前内容"))
        diffs.append({"path": artifact["path"], "diff": diff[:60000]})
        changes.append((target, content, artifact["content"], existed))
    if reset:
        recovery = folder / f"reset-recovery-{uuid4().hex}.json"
        backup = [{"path": target.relative_to(workspace).as_posix(), "content": previous, "existed": existed}
                  for target, previous, original, existed in changes]
        _write_text(recovery, json.dumps({"schema_version": 1, "files": backup}, ensure_ascii=False))
        written = []
        try:
            for target, previous, original, existed in changes:
                _write_text(target, original)
                written.append((target, previous, existed))
        except OSError as reset_error:
            rollback_errors = []
            for target, previous, existed in reversed(written):
                try:
                    if existed:
                        _write_text(target, previous)
                    else:
                        target.unlink(missing_ok=True)
                except OSError:
                    rollback_errors.append(target.name)
            if rollback_errors:
                raise OSError(f"还原练习失败：{reset_error}。部分文件未能回退，修改前内容保存在 {recovery}。") from reset_error
            try:
                recovery.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        try:
            recovery.unlink(missing_ok=True)
        except OSError:
            return {"available": True, "diffs": diffs, "recovery_backup": str(recovery)}
    return {"available": True, "diffs": diffs}
