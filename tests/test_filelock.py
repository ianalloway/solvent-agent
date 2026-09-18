"""Tests for the portable advisory lock.

Regression guard: `treasury` and `notifications` used to import `fcntl` directly,
which is Unix-only and made the whole package unimportable on Windows. These
tests assert the portable path works and that a second acquirer is actually
excluded on this platform.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from solvent import _filelock


class TestFileLock(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.lock_path = Path(self._tmp.name) / "thing.lock"
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_acquire_and_release_file_object(self) -> None:
        with open(self.lock_path, "w") as handle:
            _filelock.acquire(handle)
            _filelock.release(handle)

    def test_acquire_and_release_raw_fd(self) -> None:
        import os

        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)
        try:
            _filelock.acquire(fd)
            _filelock.release(fd)
        finally:
            os.close(fd)

    def test_non_blocking_acquire_raises_when_held(self) -> None:
        with open(self.lock_path, "w") as first:
            _filelock.acquire(first)
            with open(self.lock_path, "w") as second:
                with self.assertRaises(BlockingIOError):
                    _filelock.acquire(second, blocking=False)
            _filelock.release(first)

    def test_release_is_idempotent(self) -> None:
        with open(self.lock_path, "w") as handle:
            _filelock.acquire(handle)
            _filelock.release(handle)
            _filelock.release(handle)  # must not raise

    def test_lock_is_reacquirable_after_release(self) -> None:
        with open(self.lock_path, "w") as handle:
            _filelock.acquire(handle)
            _filelock.release(handle)
            _filelock.acquire(handle, blocking=False)
            _filelock.release(handle)

    def test_timeout_raises_when_lock_held(self) -> None:
        with open(self.lock_path, "w") as first:
            _filelock.acquire(first)
            with open(self.lock_path, "w") as second:
                with self.assertRaises(BlockingIOError):
                    _filelock.acquire(second, timeout=0.1)
            _filelock.release(first)



if __name__ == "__main__":
    unittest.main()
