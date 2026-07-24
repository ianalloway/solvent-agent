"""Tests for background_update_hint() in solvent/upgrade.py."""

from __future__ import annotations

import threading
import time
from io import StringIO
from pathlib import Path
from unittest import mock

from solvent.upgrade import background_update_hint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _join_threads(timeout: float = 2.0) -> None:
    """Wait for all non-main daemon threads to finish."""
    for t in threading.enumerate():
        if t is not threading.main_thread():
            t.join(timeout=timeout)


# ---------------------------------------------------------------------------
# Env-var guard
# ---------------------------------------------------------------------------

def test_env_var_suppresses_hint(tmp_path: Path) -> None:
    """SOLVENT_NO_UPDATE_CHECK=1 must prevent any thread from starting."""
    with mock.patch.dict("os.environ", {"SOLVENT_NO_UPDATE_CHECK": "1"}):
        with mock.patch("solvent.upgrade.latest_pypi_version") as mock_pypi:
            background_update_hint()
            _join_threads()
            mock_pypi.assert_not_called()


# ---------------------------------------------------------------------------
# Happy-path: outdated version prints to stderr
# ---------------------------------------------------------------------------

def test_prints_hint_when_outdated(tmp_path: Path) -> None:
    with mock.patch.dict(
        "os.environ",
        {"SOLVENT_NO_UPDATE_CHECK": "", "SOLVENT_HOME": str(tmp_path)},
        clear=False,
    ), mock.patch("solvent.upgrade.latest_pypi_version", return_value="999.0.0"):
        with mock.patch("solvent.upgrade.current_version", return_value="0.1.0"):
            with mock.patch("solvent.paths.data_dir", return_value=tmp_path):
                stderr = StringIO()
                with mock.patch("sys.stderr", stderr):
                    background_update_hint()
                    _join_threads()
                output = stderr.getvalue()
    assert "999.0.0" in output
    assert "0.1.0" in output
    assert "pip install" in output


# ---------------------------------------------------------------------------
# Up-to-date: no output
# ---------------------------------------------------------------------------

def test_no_hint_when_up_to_date(tmp_path: Path) -> None:
    with mock.patch.dict("os.environ", {"SOLVENT_NO_UPDATE_CHECK": ""}, clear=False):
        with mock.patch("solvent.upgrade.latest_pypi_version", return_value="0.1.0"):
            with mock.patch("solvent.upgrade.current_version", return_value="0.1.0"):
                with mock.patch("solvent.paths.data_dir", return_value=tmp_path):
                    stderr = StringIO()
                    with mock.patch("sys.stderr", stderr):
                        background_update_hint()
                        _join_threads()
                    output = stderr.getvalue()
    assert output == ""


# ---------------------------------------------------------------------------
# PyPI unreachable: no crash, no output
# ---------------------------------------------------------------------------

def test_no_hint_when_pypi_unreachable(tmp_path: Path) -> None:
    with mock.patch.dict("os.environ", {"SOLVENT_NO_UPDATE_CHECK": ""}, clear=False):
        with mock.patch("solvent.upgrade.latest_pypi_version", return_value=None):
            with mock.patch("solvent.paths.data_dir", return_value=tmp_path):
                stderr = StringIO()
                with mock.patch("sys.stderr", stderr):
                    background_update_hint()
                    _join_threads()
                output = stderr.getvalue()
    assert output == ""


# ---------------------------------------------------------------------------
# Rate-limiting: second call within interval is suppressed
# ---------------------------------------------------------------------------

def test_rate_limit_suppresses_second_call(tmp_path: Path) -> None:
    stamp = tmp_path / ".upgrade_check"
    stamp.write_text(str(time.time()))  # pretend we checked just now

    with mock.patch.dict("os.environ", {"SOLVENT_NO_UPDATE_CHECK": ""}, clear=False):
        with mock.patch("solvent.upgrade.latest_pypi_version") as mock_pypi:
            with mock.patch("solvent.paths.data_dir", return_value=tmp_path):
                background_update_hint()
                _join_threads()
                mock_pypi.assert_not_called()


def test_rate_limit_allows_call_after_interval(tmp_path: Path) -> None:
    stamp = tmp_path / ".upgrade_check"
    old_ts = time.time() - 90000  # 25 hours ago
    stamp.write_text(str(old_ts))

    with mock.patch.dict("os.environ", {"SOLVENT_NO_UPDATE_CHECK": ""}, clear=False):
        with mock.patch("solvent.upgrade.latest_pypi_version", return_value="999.0.0"):
            with mock.patch("solvent.upgrade.current_version", return_value="0.1.0"):
                with mock.patch("solvent.paths.data_dir", return_value=tmp_path):
                    stderr = StringIO()
                    with mock.patch("sys.stderr", stderr):
                        background_update_hint()
                        _join_threads()
                    output = stderr.getvalue()
    assert "999.0.0" in output


# ---------------------------------------------------------------------------
# Stamp file is written after a successful check
# ---------------------------------------------------------------------------

def test_stamp_written_after_check(tmp_path: Path) -> None:
    stamp = tmp_path / ".upgrade_check"
    assert not stamp.exists()

    with mock.patch.dict("os.environ", {"SOLVENT_NO_UPDATE_CHECK": ""}, clear=False):
        with mock.patch("solvent.upgrade.latest_pypi_version", return_value="0.1.0"):
            with mock.patch("solvent.upgrade.current_version", return_value="0.1.0"):
                with mock.patch("solvent.paths.data_dir", return_value=tmp_path):
                    background_update_hint()
                    _join_threads()

    assert stamp.exists()
    ts = float(stamp.read_text())
    assert abs(ts - time.time()) < 5


# ---------------------------------------------------------------------------
# Exceptions inside thread must not propagate
# ---------------------------------------------------------------------------

def test_exception_in_thread_does_not_raise(tmp_path: Path) -> None:
    with mock.patch.dict("os.environ", {"SOLVENT_NO_UPDATE_CHECK": ""}, clear=False):
        with mock.patch("solvent.paths.data_dir", side_effect=RuntimeError("boom")):
            background_update_hint()
            _join_threads()  # no exception should surface here
