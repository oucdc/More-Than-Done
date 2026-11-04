from pathlib import Path
import subprocess
import sys

import pytest

from agent.data_lock import DataDirectoryInUseError, DataDirectoryLock, DataDirectoryLockError


PYTHON = getattr(sys, "_base_executable", sys.executable)


def test_lock_excludes_another_owner_and_can_be_reopened(tmp_path):
    lock = DataDirectoryLock(tmp_path)
    assert lock.acquire() is lock
    assert lock.acquire() is lock
    with pytest.raises(DataDirectoryInUseError, match="另一个"):
        DataDirectoryLock(tmp_path).acquire()
    lock.release()
    lock.release()
    with DataDirectoryLock(tmp_path):
        pass
    assert lock.path.read_bytes() == b"0"
    assert lock.path.exists()


def test_exception_releases_lock_and_different_directories_are_independent(tmp_path):
    with pytest.raises(ValueError):
        with DataDirectoryLock(tmp_path):
            with DataDirectoryLock(tmp_path / "other"):
                raise ValueError("test failure")
    with DataDirectoryLock(tmp_path):
        pass


def test_lock_rejects_a_second_process(tmp_path):
    script = """
import sys
from agent.data_lock import DataDirectoryLock, DataDirectoryInUseError
try:
    DataDirectoryLock(sys.argv[1]).acquire()
except DataDirectoryInUseError:
    print('busy')
else:
    raise SystemExit('second process unexpectedly acquired the lock')
"""
    with DataDirectoryLock(tmp_path):
        result = subprocess.run(
            [PYTHON, "-c", script, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, encoding="utf-8", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "busy"


def test_process_exit_does_not_leave_a_stale_lock(tmp_path):
    script = """
import sys
from agent.data_lock import DataDirectoryLock
lock = DataDirectoryLock(sys.argv[1]).acquire()
print('ready', flush=True)
sys.stdin.readline()
"""
    process = subprocess.Popen(
        [PYTHON, "-u", "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(DataDirectoryInUseError):
            DataDirectoryLock(tmp_path).acquire()
        process.terminate()
        process.wait(timeout=10)
        with DataDirectoryLock(tmp_path):
            pass
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        for handle in (process.stdin, process.stdout, process.stderr):
            handle.close()


def test_unwritable_directory_reports_readable_error(tmp_path):
    file = tmp_path / "file"
    file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(DataDirectoryLockError, match="数据目录"):
        DataDirectoryLock(file).acquire()
