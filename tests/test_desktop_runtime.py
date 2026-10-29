from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent import desktop_runtime as runtime


def test_existing_runtime_never_prompts_or_downloads(monkeypatch):
    monkeypatch.setattr(runtime, "runtime_installed", lambda: True)
    setup = Mock(side_effect=AssertionError("must not install"))
    monkeypatch.setattr(runtime, "_install_with_progress", setup)
    assert runtime.ensure_webview2() is True
    setup.assert_not_called()


def test_declining_install_exits_without_downloading(monkeypatch):
    monkeypatch.setattr(runtime, "runtime_installed", lambda: False)
    monkeypatch.setattr(runtime.ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(MessageBoxW=lambda *_: 7)), raising=False)
    setup = Mock()
    monkeypatch.setattr(runtime, "_install_with_progress", setup)
    assert runtime.ensure_webview2() is False
    setup.assert_not_called()


def test_registry_finds_machine_wide_32bit_view(monkeypatch):
    import winreg
    seen = []
    class Key:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    def open_key(hive, path, _, access):
        seen.append((hive, path, access))
        if hive == winreg.HKEY_LOCAL_MACHINE and access & winreg.KEY_WOW64_32KEY:
            return Key()
        raise FileNotFoundError
    monkeypatch.setattr(winreg, "OpenKey", open_key)
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *_: ("152.0.0.1", 1))
    assert runtime.runtime_installed()
    assert any(hive == winreg.HKEY_CURRENT_USER for hive, *_ in seen)


def test_invalid_registry_versions_are_not_installed(monkeypatch):
    import winreg
    class Key:
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(winreg, "OpenKey", lambda *_: Key())
    for value in ("0.0.0.0", "", "nonsense", None):
        monkeypatch.setattr(winreg, "QueryValueEx", lambda *_, value=value: (value, 1))
        assert runtime.runtime_installed() is False


def test_invalid_signature_stops_before_execution(monkeypatch):
    monkeypatch.setattr(runtime, "download_installer", lambda path: path.write_bytes(b"MZtest"))
    monkeypatch.setattr(runtime, "verify_installer", Mock(side_effect=RuntimeError("signature invalid")))
    launch = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", launch)
    with pytest.raises(RuntimeError, match="signature invalid"):
        runtime.install_runtime(lambda *_: None)
    launch.assert_not_called()


def test_success_requires_runtime_detection_after_setup(monkeypatch):
    monkeypatch.setattr(runtime, "download_installer", lambda path: path.write_bytes(b"MZtest"))
    monkeypatch.setattr(runtime, "verify_installer", lambda _: None)
    monkeypatch.setattr(runtime.subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=0))
    monkeypatch.setattr(runtime, "runtime_installed", lambda: False)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="0x00000000"):
        runtime.install_runtime(lambda *_: None)


def test_successful_setup_continues(monkeypatch):
    monkeypatch.setattr(runtime, "download_installer", lambda path: path.write_bytes(b"MZtest"))
    monkeypatch.setattr(runtime, "verify_installer", lambda _: None)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(runtime, "runtime_installed", lambda: True)
    runtime.install_runtime(lambda *_: None)
    assert run.call_args.args[0][1:] == ["/silent", "/install"]


def test_signature_check_uses_system_powershell_modules(monkeypatch, tmp_path):
    monkeypatch.setenv("PSModulePath", "foreign-powershell-modules")
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(runtime.subprocess, "run", run)
    runtime.verify_installer(tmp_path / "setup.exe")
    assert run.call_args.kwargs["env"]["PSModulePath"].endswith(r"WindowsPowerShell\v1.0\Modules")
    assert run.call_args.kwargs["env"]["MTD_WEBVIEW_INSTALLER"] == str(tmp_path / "setup.exe")
