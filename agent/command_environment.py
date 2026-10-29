"""Bounded, non-executing discovery for the local command environment."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import shutil
from typing import Mapping

_DESCRIPTIONS: dict[tuple, tuple[float, dict[str, object]]] = {}


def _store_alias(path: Path) -> bool:
    return "windowsapps" in {part.casefold() for part in path.parts}


def _registered_pythons() -> list[Path]:
    if os.name != "nt":
        return []
    import winreg
    found = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"Software\Python\PythonCore", 0, winreg.KEY_READ | view) as key:
                    for index in range(min(winreg.QueryInfoKey(key)[0], 30)):
                        tag = winreg.EnumKey(key, index)
                        try:
                            with winreg.OpenKey(key, tag + r"\InstallPath") as install:
                                try:
                                    value = winreg.QueryValueEx(install, "ExecutablePath")[0]
                                    found.append(Path(value))
                                except OSError:
                                    found.append(Path(winreg.QueryValue(install, None)) / "python.exe")
                        except OSError:
                            continue
            except OSError:
                continue
    return found


def discover_python(workspace: Path, environ: Mapping[str, str] | None = None) -> tuple[str | None, str]:
    """Look only in configured/known locations; never run candidate executables."""
    env = os.environ if environ is None else environ
    candidates: list[tuple[Path, str]] = []
    executable = "python.exe" if os.name == "nt" else "python"
    scripts = "Scripts" if os.name == "nt" else "bin"
    for name in (".venv", "venv"):
        candidates.append((workspace / name / scripts / executable, "workspace"))
    for name in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        if env.get(name):
            prefix = Path(env[name])
            candidates.append((prefix / (executable if os.name == "nt" and name == "CONDA_PREFIX" else scripts + "/" + executable), "active_environment"))
    for directory in env.get("PATH", "").split(os.pathsep):
        # Empty/relative PATH entries are not used for discovery.
        path = Path(directory.strip('"'))
        if directory and path.is_absolute():
            for name in (("python.exe",) if os.name == "nt" else ("python3", "python")):
                candidates.append((path / name, "path"))
    if os.name == "nt":
        candidates.extend((path, "registry") for path in _registered_pythons())
        for base in (env.get("USERPROFILE"), env.get("PROGRAMDATA"), env.get("LOCALAPPDATA")):
            if not base:
                continue
            for name in ("miniconda3", "anaconda3", "Miniforge3", "miniforge3"):
                candidates.append((Path(base) / name / "python.exe", "known_installation"))
        local = env.get("LOCALAPPDATA")
        if local:
            # A bounded list of standard installer folders, not a recursive search.
            base = Path(local) / "Programs" / "Python"
            try:
                for directory in sorted(base.iterdir(), reverse=True)[:30]:
                    candidates.append((directory / "python.exe", "known_installation"))
            except OSError:
                pass
    if not getattr(sys, "frozen", False):
        candidates.append((Path(sys.executable), "application_runtime"))
    seen = set()
    for path, source in candidates:
        key = str(path).casefold()
        if key in seen or _store_alias(path):
            continue
        seen.add(key)
        try:
            if path.is_file() and (os.name == "nt" or os.access(path, os.X_OK)):
                return str(path.absolute()), source
        except OSError:
            continue
    return None, "not_found"


def describe_environment(workspace: Path) -> dict[str, object]:
    # SSE snapshots can be frequent. Cache only briefly so newly installed tools
    # become visible without retaining a stale path for the whole app session.
    key = (str(workspace), os.environ.get("PATH"), os.environ.get("VIRTUAL_ENV"),
           os.environ.get("CONDA_PREFIX"), getattr(sys, "frozen", False))
    previous = _DESCRIPTIONS.get(key)
    if previous and time.monotonic() - previous[0] < 3:
        return dict(previous[1])
    python, source = discover_python(workspace)
    launcher = shutil.which("py") if os.name == "nt" else None
    if launcher and _store_alias(Path(launcher)):
        launcher = None
    description = {
        "platform": sys.platform,
        "shell": "cmd.exe" if os.name == "nt" else "/bin/sh",
        "python": python,
        "python_found": python is not None,
        "python_source": source,
        "python_launcher": launcher,
        "command_syntax": (
            "CMD 语法：串联命令用 && 或 &，丢弃错误用 2>nul。不要使用 PowerShell 的分号、$null、Get-ChildItem、ls；"
            "需要 PowerShell 时显式调用 powershell -NoProfile -Command。使用 python 调用已识别的解释器。"
            if os.name == "nt" else "POSIX sh 语法。"
        ),
    }
    if len(_DESCRIPTIONS) > 50:
        _DESCRIPTIONS.clear()
    _DESCRIPTIONS[key] = (time.monotonic(), description)
    return description


def command_environment(workspace: Path) -> tuple[dict[str, str], dict[str, object]]:
    """Prepare a child-only PATH; caller must obtain command approval first."""
    env = os.environ.copy()
    for name in list(env):
        upper = name.upper()
        if upper.startswith(("DEEPSEEK_", "OPENAI_", "ANTHROPIC_")) or upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            env.pop(name, None)
    info = describe_environment(workspace)
    if info["python"]:
        prefix = Path(str(info["python"])).parent
        additions = [str(prefix)]
        # Conda DLL search and command-line entry points need these locations.
        for suffix in ("Scripts", "Library/bin"):
            if (prefix / suffix).is_dir():
                additions.append(str(prefix / suffix))
        env["PATH"] = os.pathsep.join([*additions, env.get("PATH", "")])
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env, info
