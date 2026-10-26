"""Process-wide filesystem leader lock for singleton background loops."""
from __future__ import annotations

import fcntl
import os
import weakref
from pathlib import Path

_locks: weakref.WeakSet = weakref.WeakSet()


def _after_fork() -> None:
    # Ein Kind darf die offene flock-Description weder halten noch entsperren.
    for lock in list(_locks):
        if lock._fd is not None:
            os.close(lock._fd)
            lock._fd = None


os.register_at_fork(after_in_child=_after_fork)


class LeaderLock:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._fd: int | None = None
        _locks.add(self)

    def try_acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if isinstance(exc, BlockingIOError):
                return False
            raise
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
