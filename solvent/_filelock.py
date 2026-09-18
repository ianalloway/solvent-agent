"""Portable advisory file locking.

`fcntl` is Unix-only. Importing it at module scope made the entire package
unimportable on Windows (``ModuleNotFoundError: No module named 'fcntl'``),
which took out the CLI and 20 of the 48 test modules with it.

This module is the single place that knows how to take an advisory exclusive
lock on every supported platform:

* POSIX -- ``fcntl.flock``
* Windows -- ``msvcrt.locking`` over a one-byte range, with a poll loop that
  reproduces ``flock``'s blocking semantics (``LK_LOCK`` gives up after ~10s
  and raises, which is not what callers here expect).

Locks are advisory. The treasury additionally relies on SQLite transactions, so
a failure to lock is a concurrency-quality problem, never a correctness one.
"""

from __future__ import annotations

import os
import time
from typing import IO, Union

if os.name == "nt":  # pragma: no cover - exercised on Windows CI only
    import msvcrt
else:
    import fcntl

FileOrFd = Union[int, IO[str], IO[bytes]]

#: How long to wait between attempts when blocking on Windows.
_POLL_SECONDS = 0.05

#: Byte range locked on Windows. ``msvcrt.locking`` takes the lock at the
#: current file offset for a given length; every caller locks the same
#: single byte so they contend with each other.
_LOCK_BYTES = 1


def _fd_of(handle: FileOrFd) -> int:
    """Return the file descriptor for a file object or a raw fd."""
    return handle if isinstance(handle, int) else handle.fileno()


def _rewind(handle: FileOrFd, fd: int) -> None:
    """Seek to the start so Windows locks the same byte range every time."""
    if isinstance(handle, int):
        os.lseek(fd, 0, os.SEEK_SET)
    else:
        try:
            handle.seek(0)
        except (OSError, ValueError):  # non-seekable handle
            pass


def acquire(
    handle: FileOrFd, *, blocking: bool = True, timeout: float | None = None
) -> None:
    """Take an exclusive advisory lock on ``handle``.

    Args:
        handle: An open file object or a raw file descriptor.
        blocking: Wait for the lock (default, matching ``flock``) or raise
            ``BlockingIOError`` immediately when it is already held.
        timeout: Optional seconds to wait when ``blocking`` is True. ``None``
            waits indefinitely (the historical behaviour). On Windows the
            lock path is a poll loop, so without a timeout a stuck peer could
            hang a caller forever; pass a finite value to bound that wait.

    Raises:
        BlockingIOError: If ``blocking`` is False and the lock is held, or if
            ``timeout`` expires before the lock is acquired.
        OSError: On unrecoverable locking errors (e.g. a vanished fd).
    """
    fd = _fd_of(handle)
    deadline = None if timeout is None else time.monotonic() + timeout

    def _timed_out() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    if os.name != "nt":
        # Fast path: unbounded blocking flock matches historical semantics.
        if blocking and timeout is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as exc:
                if not blocking or _timed_out():
                    raise BlockingIOError(str(exc)) from exc
                time.sleep(_POLL_SECONDS)

    _rewind(handle, fd)
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, _LOCK_BYTES)
            return
        except OSError as exc:
            if not blocking or _timed_out():
                raise BlockingIOError(str(exc)) from exc
            # Held by someone else: wait and try again.
            time.sleep(_POLL_SECONDS)


def release(handle: FileOrFd) -> None:
    """Release a lock previously taken by :func:`acquire`.

    Releasing an unlocked handle is a no-op rather than an error, so cleanup
    paths can call this unconditionally.
    """
    fd = _fd_of(handle)

    if os.name != "nt":
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return

    _rewind(handle, fd)
    try:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_BYTES)
    except OSError:
        pass


__all__ = ["FileOrFd", "acquire", "release"]
