"""Workspace-scoped tools for the local More Than Done agent."""

from fnmatch import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import signal
from typing import Any, Callable

from agent.agent_config import AgentConfig
from agent.command_environment import command_environment
from agent.guidance_tools import GUIDANCE_TOOL_DEFINITIONS
from agent.permission_policy import read_only_check
from learning.guidance import GuidanceNotebook


class ToolPermissionError(RuntimeError):
    """Raised when a tool call is outside the user-granted permissions."""


ApprovalFn = Callable[[str, dict[str, Any]], bool]


def _decode_output(value: bytes | str | None) -> str:
    if isinstance(value, str):
        return value
    return (value or b"").decode("utf-8", errors="replace")


def run_local_command(command: str, cwd: Path, env: dict[str, str], timeout: int, cancel_event: threading.Event | None = None) -> dict[str, Any]:
    """Run one approved command and stop its process tree on timeout/cancel."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt":
        shell_path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
        invocation = f'"{shell_path}" /d /s /c "chcp 65001>nul & {command}"'
        options = {"creationflags": flags}
    else:
        invocation = ["/bin/sh", "-c", command]
        options = {"start_new_session": True}
    started = time.monotonic()
    process = subprocess.Popen(invocation, shell=False, cwd=str(cwd), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    timed_out = cancelled = False
    while True:
        try:
            stdout, stderr = process.communicate(timeout=.15)
            break
        except subprocess.TimeoutExpired:
            cancelled = bool(cancel_event and cancel_event.is_set())
            timed_out = not cancelled and time.monotonic() - started >= timeout
            if not (cancelled or timed_out):
                continue
            if os.name == "nt":
                taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
                try:
                    subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, creationflags=flags, timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                stdout, stderr = exc.stdout, exc.stderr
            break
    return {"returncode": process.returncode if not (timed_out or cancelled) else None,
            "timed_out": timed_out, "cancelled": cancelled,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "stdout": _decode_output(stdout), "stderr": _decode_output(stderr)}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录内容。相对路径基于工作区；外部路径按用户权限批准或拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认 ."},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件或 PDF。外部路径按用户权限批准或拒绝；大文件会截断。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，相对于工作区"},
                    "max_chars": {
                        "type": "integer",
                        "description": "最多返回的字符数，默认 20000，最大 200000",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入或覆盖文本文件。范围与批准要求由用户选定的权限模式决定。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，相对于工作区"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "按正则搜索文本，返回匹配的行；外部路径按用户权限批准或拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python 正则表达式"},
                    "path": {"type": "string", "description": "搜索目录，默认 ."},
                    "include": {"type": "string", "description": "文件名通配符，默认 *"},
                    "max_results": {"type": "integer", "description": "最多返回行数，默认 50"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "执行本机命令。Windows 默认使用 CMD；PowerShell 须显式调用 powershell -NoProfile -Command。目录与批准要求由用户权限模式决定。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令；Windows 使用 CMD 语法，需要 PowerShell 时显式调用 powershell -NoProfile -Command"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 120, "description": "最长执行秒数，默认 30；超时会停止并保留已有输出"},
                    "cwd": {"type": "string", "description": "执行目录，默认工作区；可指定经用户授权的外部路径。"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 30，范围 1 到 120"},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
] + GUIDANCE_TOOL_DEFINITIONS


class ToolRuntime:
    """Execute model tool calls inside one workspace with permission checks."""

    MAX_OUTPUT = 20_000
    SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "dist", "node_modules"}
    SENSITIVE_NAMES = {".env", "agent_config.json"}

    def __init__(
        self,
        config: AgentConfig,
        *,
        approval: ApprovalFn | None = None,
        notebook: GuidanceNotebook | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.root = config.workspace.resolve()
        self.approval = approval
        self.notebook = notebook if notebook is not None else GuidanceNotebook()
        self.cancel_event = cancel_event
        self._operation_approved = False
        self._active_tool = ""
        self._active_arguments: dict[str, Any] = {}

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        self._operation_approved = False
        self._active_tool = name
        self._active_arguments = arguments
        try:
            if not isinstance(arguments, dict):
                raise ToolPermissionError("工具参数必须是 JSON 对象。")
            if name == "plan_learning":
                return json.dumps({"ok": True, "lesson": self.notebook.plan(arguments)}, ensure_ascii=False)
            if name == "review_learning_attempt":
                return json.dumps(self.notebook.review(arguments), ensure_ascii=False)
            if name == "record_learning_help":
                return json.dumps({"ok": True, "lesson": self.notebook.help(arguments.get("kind"))}, ensure_ascii=False)
            handler = {
                "list_dir": self._list_dir,
                "read_file": self._read_file,
                "write_file": self._write_file,
                "grep": self._grep,
                "run_command": self._run_command,
            }.get(name)
            if handler is None:
                raise ToolPermissionError(f"未知工具：{name}")
            result = handler(arguments)
            if name == "read_file":
                self.notebook.observe({"type": "file_read", "path": result.get("path"), "size": result.get("size"), "truncated": result.get("truncated", False)})
            elif name in {"write_file", "run_command"}:
                self.notebook.record_agent_execution()
            return json.dumps({"ok": True, **result}, ensure_ascii=False)
        except ToolPermissionError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        except (OSError, ValueError, TypeError) as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        except Exception as exc:  # keep the agent loop alive on unexpected tool failures
            return json.dumps(
                {"ok": False, "error": f"工具执行失败：{type(exc).__name__}"},
                ensure_ascii=False,
            )

    def _path(self, raw: object, *, must_exist: bool = False) -> Path:
        if raw is None:
            raw = "."
        if not isinstance(raw, str) or not raw.strip():
            raise ToolPermissionError("路径不能为空。")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ToolPermissionError(f"无法解析路径：{raw}") from exc
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            if self.config.permission_mode == "custom":
                raise ToolPermissionError("路径超出用户授权的工作区。") from exc
            if self.config.permission_mode != "full" and not self._operation_approved:
                if not self._ask(self._active_tool, {**self._active_arguments, "resolved_path": str(resolved), "outside_workspace": True}):
                    raise ToolPermissionError("用户拒绝了这次工作区外访问。")
        if must_exist and not resolved.exists():
            raise ToolPermissionError(f"路径不存在：{raw}")
        return resolved

    def _require_read(self) -> None:
        if not self.config.allow_read:
            raise ToolPermissionError("用户没有授予读取权限。")

    def _require_write(self, args: dict[str, Any]) -> None:
        if not self.config.allow_write:
            raise ToolPermissionError("用户没有授予写入权限。")
        if not self._operation_approved and self.config.confirm_write and not self._ask("write_file", args):
            raise ToolPermissionError("用户拒绝了这次写入。")

    def _require_shell(self, args: dict[str, Any]) -> None:
        if not self.config.allow_shell:
            raise ToolPermissionError("用户没有授予命令执行权限。")
        if self.config.permission_mode == "auto" and read_only_check(args.get("command")):
            return
        if not self._operation_approved and self.config.confirm_shell and not self._ask("run_command", args):
            raise ToolPermissionError("用户拒绝了这次命令执行。")

    def _ask(self, tool: str, args: dict[str, Any]) -> bool:
        if self.approval is None:
            raise ToolPermissionError("当前没有可用的批准通道，操作被拒绝。")
        allowed = self.approval(tool, args)
        self._operation_approved = bool(allowed)
        return allowed

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root)) or "."
        except ValueError:
            return str(path)

    def _list_dir(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_read()
        path = self._path(args.get("path", "."), must_exist=True)
        if not path.is_dir():
            raise ToolPermissionError("目标不是目录。")
        entries = []
        for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
            try:
                stat = item.stat()
                entries.append({
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": stat.st_size,
                })
            except OSError:
                entries.append({"name": item.name, "type": "unknown", "size": None})
            if len(entries) >= 200:
                break
        return {"path": self._relative(path), "entries": entries, "truncated": len(entries) >= 200}

    def _forbid_sensitive(self, path: Path) -> None:
        if path.name.casefold() in self.SENSITIVE_NAMES:
            raise ToolPermissionError("该文件包含本地凭据，工具不能读取或写入。")

    def _read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_read()
        path = self._path(args.get("path"), must_exist=True)
        if not path.is_file():
            raise ToolPermissionError("目标不是文件。")
        self._forbid_sensitive(path)
        try:
            max_chars = int(args.get("max_chars", 20_000))
        except (TypeError, ValueError):
            max_chars = 20_000
        max_chars = max(200, min(max_chars, 200_000))
        if path.suffix.casefold() == ".pdf":
            return self._read_pdf(path, max_chars)

        size = path.stat().st_size
        if size > 2_000_000:
            raise ToolPermissionError("文件超过 2MB，无法直接读取。")
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ToolPermissionError("文件不是 UTF-8 文本（可能是二进制文件）。") from exc
        truncated = len(text) > max_chars
        return {
            "path": self._relative(path),
            "content": text[:max_chars],
            "truncated": truncated,
            "size": size,
        }

    def _read_pdf(self, path: Path, max_chars: int) -> dict[str, Any]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ToolPermissionError("读取 PDF 需要 pypdf，请先安装依赖。") from exc

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ToolPermissionError("PDF 已加密，无法读取。")
        chunks: list[str] = []
        total = 0
        pages_read = 0
        for index, page in enumerate(reader.pages, start=1):
            if pages_read >= 40 or total >= max_chars:
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            pages_read += 1
            if not text.strip():
                continue
            chunk = f"\n--- page {index} ---\n{text}"
            remaining = max_chars - total
            if len(chunk) > remaining:
                chunk = chunk[:remaining]
            chunks.append(chunk)
            total += len(chunk)
        content = "".join(chunks).strip()
        if not content:
            raise ToolPermissionError("PDF 未提取到文本；它可能是扫描件，需要 OCR。")
        return {
            "path": self._relative(path),
            "content": content,
            "truncated": pages_read < len(reader.pages) or total >= max_chars,
            "pages": len(reader.pages),
        }

    def _write_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._path(args.get("path"))
        self._forbid_sensitive(path)
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolPermissionError("content 必须是文本。")
        encoded = content.encode("utf-8")
        if len(encoded) > 2_000_000:
            raise ToolPermissionError("写入内容超过 2MB 限制。")
        self._require_write(args)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return {"path": self._relative(path), "bytes": len(encoded)}

    def _grep(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_read()
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ToolPermissionError("pattern 必须是非空文本。")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolPermissionError(f"正则表达式无效：{exc}") from exc
        base = self._path(args.get("path", "."), must_exist=True)
        include = args.get("include", "*")
        if not isinstance(include, str) or not include:
            include = "*"
        try:
            max_results = int(args.get("max_results", 50))
        except (TypeError, ValueError):
            max_results = 50
        max_results = max(1, min(max_results, 200))

        results: list[dict[str, Any]] = []
        for current_root, dirnames, filenames in os.walk(base):
            dirnames[:] = [name for name in dirnames if name not in self.SKIP_DIRS]
            for filename in filenames:
                if not fnmatch(filename, include):
                    continue
                if filename.casefold() in self.SENSITIVE_NAMES:
                    continue
                path = Path(current_root) / filename
                try:
                    resolved = path.resolve()
                    # Do not follow a file symlink outside an approved search root.
                    if not resolved.is_relative_to(base):
                        continue
                    if path.stat().st_size > 1_000_000:
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        results.append({
                            "path": self._relative(path),
                            "line": line_number,
                            "text": line[:300],
                        })
                        if len(results) >= max_results:
                            return {"matches": results, "truncated": True}
        return {"matches": results, "truncated": False}

    def _run_command(self, args: dict[str, Any]) -> dict[str, Any]:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolPermissionError("command 必须是非空文本。")
        if len(command) > 4_000:
            raise ToolPermissionError("命令过长。")
        cwd = self._path(args.get("cwd", "."), must_exist=True)
        if not cwd.is_dir():
            raise ToolPermissionError("cwd 不是目录。")
        self._require_shell(args)
        check = read_only_check(command) if self.config.permission_mode == "auto" else None
        if check:
            # Exact known checks use a read-only Python adapter, not arbitrary shell text.
            if check == "location":
                stdout = str(cwd)
            else:
                stdout = "\n".join(sorted(item.name for item in cwd.iterdir())[:200])
            return {"command": command, "cwd": self._relative(cwd), "returncode": 0,
                    "timed_out": False, "stdout": stdout, "stderr": "", "execution": "read_only_check"}
        try:
            timeout = int(args.get("timeout", 30))
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(1, min(timeout, 120))

        if self.cancel_event is not None and self.cancel_event.is_set():
            return {"ok": False, "command": command, "cwd": self._relative(cwd), "cancelled": True,
                    "timed_out": False, "returncode": None, "stdout": "", "stderr": "", "hint": "操作已取消，未启动命令。"}
        env, environment = command_environment(self.root)
        result = run_local_command(command, cwd, env, timeout, self.cancel_event)
        result.update({"command": command, "cwd": self._relative(cwd), "shell": environment["shell"],
                       "python": environment["python"], "timeout_seconds": timeout})
        result["ok"] = result["returncode"] == 0 and not result["timed_out"] and not result["cancelled"]
        result["stdout"] = result["stdout"][-self.MAX_OUTPUT:]
        result["stderr"] = result["stderr"][-self.MAX_OUTPUT:]
        if result["cancelled"]:
            result["hint"] = "命令已停止，已保留停止前的输出。请检查文件是否有部分修改。"
        elif result["timed_out"]:
            result["hint"] = f"命令超过 {timeout} 秒，已停止并保留已有输出。请缩小查询范围；不要搜索整个磁盘。"
        elif not result["ok"]:
            if os.name == "nt" and ("$null" in command or re.search(r";\s*(?:py|python|ls|dir|Get-)", command, re.I)):
                result["hint"] = "当前使用 CMD。此命令可能混入 PowerShell 语法；CMD 中丢弃错误用 2>nul，串联命令用 && 或 &。"
            elif result["returncode"] in (9009, 127) or "Python was not found" in result["stderr"]:
                result["hint"] = "命令或解释器未找到。请查看执行环境中已识别的 Python 路径，使用完整路径调用；不要据此判断软件未安装。"
            else:
                result["hint"] = f"命令退出码为 {result['returncode']}。请根据错误输出定位原因，再决定下一步。"
        return result
