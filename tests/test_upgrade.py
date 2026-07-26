"""tests/test_upgrade.py — unit tests for the upgrade version checker."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from solvent.upgrade import _parse_version, check_upgrade, current_version


class TestParseVersion(unittest.TestCase):
    def test_simple_semver(self):
        self.assertEqual(_parse_version("1.2.3"), (1, 2, 3))

    def test_leading_v(self):
        self.assertEqual(_parse_version("v2.0.0"), (2, 0, 0))

    def test_two_part(self):
        self.assertEqual(_parse_version("1.0"), (1, 0))

    def test_non_numeric_segment(self):
        result = _parse_version("1.2.alpha")
        self.assertEqual(result[:2], (1, 2))

    def test_newer_is_greater(self):
        self.assertGreater(_parse_version("0.2.0"), _parse_version("0.1.9"))

    def test_same_version_equal(self):
        self.assertEqual(_parse_version("1.0.0"), _parse_version("1.0.0"))


class TestCurrentVersion(unittest.TestCase):
    def test_returns_string(self):
        v = current_version()
        self.assertIsInstance(v, str)
        self.assertGreater(len(v), 0)

    def test_looks_like_semver(self):
        parts = current_version().split(".")
        self.assertGreaterEqual(len(parts), 2)


class TestCheckUpgrade(unittest.TestCase):
    def _fake_pypi(self, version: str):
        """Return a mock that simulates PyPI returning the given version."""
        body = json.dumps({"info": {"version": version}}).encode()
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.__enter__ = mock.MagicMock(return_value=resp)
        resp.__exit__ = mock.MagicMock(return_value=False)
        return mock.patch("urllib.request.urlopen", return_value=resp)

    def test_up_to_date_when_same(self):
        current = current_version()
        with self._fake_pypi(current):
            result = check_upgrade(quiet=True)
        self.assertTrue(result["up_to_date"])
        self.assertFalse(result["error"])

    def test_outdated_when_newer_on_pypi(self):
        with self._fake_pypi("99.0.0"):
            result = check_upgrade(quiet=True)
        self.assertFalse(result["up_to_date"])
        self.assertEqual(result["latest"], "99.0.0")

    def test_up_to_date_when_local_is_newer(self):
        with self._fake_pypi("0.0.1"):
            result = check_upgrade(quiet=True)
        self.assertTrue(result["up_to_date"])

    def test_network_error_treated_as_up_to_date(self):
        import urllib.error

        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("offline")
        ):
            result = check_upgrade(quiet=True)
        self.assertTrue(result["up_to_date"])
        self.assertTrue(result["error"])
        self.assertIsNone(result["latest"])

    def test_prints_upgrade_hint_when_outdated(self):
        buf = io.StringIO()
        with self._fake_pypi("99.0.0"), redirect_stdout(buf):
            check_upgrade(quiet=False)
        out = buf.getvalue()
        self.assertIn("99.0.0", out)
        self.assertIn("pip install", out)

    def test_prints_up_to_date_message(self):
        buf = io.StringIO()
        current = current_version()
        with self._fake_pypi(current), redirect_stdout(buf):
            check_upgrade(quiet=False)
        self.assertIn("up to date", buf.getvalue())

    def test_result_contains_current(self):
        current = current_version()
        with self._fake_pypi(current):
            result = check_upgrade(quiet=True)
        self.assertEqual(result["current"], current)


class TestUpgradeCLI(unittest.TestCase):
    def _fake_pypi(self, version: str):
        body = json.dumps({"info": {"version": version}}).encode()
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.__enter__ = mock.MagicMock(return_value=resp)
        resp.__exit__ = mock.MagicMock(return_value=False)
        return mock.patch("urllib.request.urlopen", return_value=resp)

    def test_main_exits_zero_when_up_to_date(self):
        from solvent.upgrade import main

        current = current_version()
        with (
            self._fake_pypi(current),
            mock.patch.object(sys, "argv", ["solvent-upgrade"]),
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertEqual(ctx.exception.code, 0)

    def test_check_flag_exits_one_when_outdated(self):
        from solvent.upgrade import main

        with (
            self._fake_pypi("99.0.0"),
            mock.patch.object(sys, "argv", ["solvent-upgrade", "--check"]),
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertEqual(ctx.exception.code, 1)

    def test_check_flag_exits_zero_when_current(self):
        from solvent.upgrade import main

        current = current_version()
        with (
            self._fake_pypi(current),
            mock.patch.object(sys, "argv", ["solvent-upgrade", "--check"]),
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main()
        self.assertEqual(ctx.exception.code, 0)

    def test_json_flag_outputs_json(self):
        from solvent.upgrade import main

        current = current_version()
        buf = io.StringIO()
        with (
            self._fake_pypi(current),
            mock.patch.object(sys, "argv", ["solvent-upgrade", "--json"]),
            redirect_stdout(buf),
            self.assertRaises(SystemExit),
        ):
            main()
        data = json.loads(buf.getvalue())
        self.assertIn("current", data)
        self.assertIn("up_to_date", data)


if __name__ == "__main__":
    unittest.main()
