"""Hold an operating-system lock for the lifetime of one local data directory."""

from __future__ import annotations

from pathlib import Path
import os
import threading
from typing import BinaryIO


class DataDirectoryLockError(RuntimeError):
    """The application cannot obtain its data-directory lock."""


class DataDirectoryInUseError(DataDirectoryLockError):
    """Another application instance already owns this data directory."""


class DataDirectoryLock:
    """Keep a nonblocking file lock until release or process termination.

    The lock file is intentionally retained after release. Removing a lock file
    could let another process lock a different inode while an old owner runs.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.path = self.directory / ".more-than-done.lock"
        self._handle: BinaryIO | None = None
        self._guard = threading.Lock()

    def acquire(self) -> DataDirectoryLock:
        with self._guard:
            if self._handle is not None:
                return self
            handle = None
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                handle = self.path.open("a+b")
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
            except OSError as exc:
                if handle is not None:
                    handle.close()
                raise DataDirectoryLockError("无法打开数据目录，请检查文件夹是否可写。") from exc
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise DataDirectoryInUseError(
                    "这个数据目录已由另一个 More Than Done 窗口使用，请切回已打开的窗口，或使用其他数据目录。"
                ) from exc
            self._handle = handle
            return self

    def release(self) -> None:
        with self._guard:
            handle = self._handle
            if handle is None:
                return
            self._handle = None
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def __enter__(self) -> DataDirectoryLock:
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
