"""First-run Windows WebView2 setup, before the web-based window is created.

Deployment guidance: https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser

BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
DOWNLOAD_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/#download-section"
RUNTIME_KEY = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def runtime_installed() -> bool:
    """Check both registry views and both per-user/per-machine installations."""
    if sys.platform != "win32":
        return True
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, RUNTIME_KEY, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "pv")
                if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+){3}", value) and int(value.split(".")[0]) >= 86:
                    return True
            except OSError:
                continue
    return False


def download_installer(destination: Path) -> None:
    """Use Microsoft's evergreen bootstrapper; cap downloads and validate PE header."""
    with urllib.request.urlopen(BOOTSTRAPPER_URL, timeout=30) as response, destination.open("wb") as output:
        size = 0
        while chunk := response.read(64 * 1024):
            size += len(chunk)
            if size > 10 * 1024 * 1024:
                raise RuntimeError("安装程序下载内容异常，请改用微软官方下载页。")
            output.write(chunk)
    with destination.open("rb") as source:
        if source.read(2) != b"MZ":
            raise RuntimeError("下载内容不是有效的安装程序，请检查网络。")


def verify_installer(installer: Path) -> None:
    """Require a valid Microsoft Authenticode signature before executing a download."""
    environment = os.environ.copy()
    environment["MTD_WEBVIEW_INSTALLER"] = str(installer.resolve())
    script = (
        "$ErrorActionPreference='Stop'; "
        "$s=Get-AuthenticodeSignature -LiteralPath $env:MTD_WEBVIEW_INSTALLER; "
        "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match "
        "'(^|,\\s*)O=Microsoft Corporation(,|$)') {exit 0} else {exit 1}"
    )
    system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system / "System32/WindowsPowerShell/v1.0/powershell.exe"
    environment["PSModulePath"] = str(powershell.parent / "Modules")
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                            env=environment, capture_output=True, timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("无法验证安装程序的微软数字签名，已停止安装。可使用微软官方下载页重试。")


def install_runtime(status) -> None:
    with tempfile.TemporaryDirectory(prefix="more-than-done-webview-") as temporary:
        installer = Path(temporary) / "MicrosoftEdgeWebview2Setup.exe"
        status("正在从微软下载界面运行组件……")
        download_installer(installer)
        status("正在验证微软数字签名……")
        verify_installer(installer)
        status("正在安装 WebView2，请保持联网；这可能需要几分钟……")
        result = subprocess.run([str(installer), "/silent", "/install"], timeout=480,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # Another setup process can finish the installation even after a nonzero exit.
        for _ in range(15):
            if runtime_installed():
                return
            time.sleep(1)
        code = result.returncode & 0xFFFFFFFF
        raise RuntimeError(f"组件尚未安装成功（代码 0x{code:08X}）。请检查网络和系统安装权限，或使用微软官方下载页。")


def _install_with_progress() -> bool:
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title("More Than Done · 首次启动准备")
    root.geometry("510x230")
    root.resizable(False, False)
    label = tk.StringVar(value="正在准备安装 WebView2……")
    ttk.Label(root, text="准备界面运行组件", font=("Microsoft YaHei UI", 14, "bold")).pack(pady=(20, 12))
    ttk.Label(root, textvariable=label, wraplength=465).pack(padx=20)
    bar = ttk.Progressbar(root, mode="indeterminate", length=460)
    bar.pack(pady=18)
    bar.start()
    events = queue.Queue()
    installed = False
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    def worker():
        try:
            install_runtime(lambda message: events.put(("status", message)))
        except RuntimeError as error:
            events.put(("error", str(error)))
        except (OSError, subprocess.SubprocessError):
            events.put(("error", "自动安装未完成。请检查联网状态，或打开微软下载页，安装 Evergreen Standalone Installer 后重新启动软件。"))
        else:
            events.put(("done", ""))

    def poll():
        nonlocal installed
        while not events.empty():
            kind, message = events.get_nowait()
            if kind == "done":
                installed = True
                root.destroy()
                return
            if kind == "error":
                bar.stop()
                label.set(message)
                ttk.Button(root, text="打开微软下载页", command=lambda: webbrowser.open(DOWNLOAD_PAGE)).pack(side="left", padx=50)
                ttk.Button(root, text="关闭", command=root.destroy).pack(side="right", padx=50)
                root.protocol("WM_DELETE_WINDOW", root.destroy)
                return
            label.set(message)
        root.after(100, poll)

    threading.Thread(target=worker, daemon=True).start()
    root.after(100, poll)
    root.mainloop()
    return installed


def ensure_webview2() -> bool:
    if runtime_installed():
        return True
    answer = ctypes.windll.user32.MessageBoxW(
        None,
        "首次启动需要安装微软 WebView2 界面运行组件。\n\n选择“是”将联网下载并安装，完成后自动打开软件。\n这不会上传你的 API Key、项目或聊天记录。\n\n现在安装吗？",
        "More Than Done · 首次启动", 0x24,
    )
    return _install_with_progress() if answer == 6 else False
