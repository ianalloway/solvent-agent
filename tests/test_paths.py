"""Tests for runtime data-path resolution."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solvent import paths


class TestPaths(unittest.TestCase):
    def test_solvent_home_override(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"SOLVENT_HOME": d}):
                base = Path(d).resolve()
                self.assertEqual(paths.base_dir(), base)
                self.assertEqual(paths.data_dir(), base / "data")
                self.assertEqual(paths.config_dir(), base / ".solvent")
                self.assertEqual(
                    paths.config_path("spend_policy.json"), base / ".solvent" / "spend_policy.json"
                )
                self.assertEqual(paths.db_path(), base / "data" / "solvent.db")
                self.assertEqual(paths.reports_dir(), base / "data" / "reports")
                self.assertEqual(paths.dashboard_html(), base / "treasury_dashboard.html")
                self.assertTrue(paths.data_dir().is_dir())

    def test_source_checkout_uses_repo_root(self):
        env = {k: v for k, v in os.environ.items() if k != "SOLVENT_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(paths._is_source_checkout())  # repo has pyproject.toml
            self.assertEqual(paths.base_dir(), paths._REPO_ROOT)
            self.assertEqual(paths.config_dir(), paths._REPO_ROOT / ".solvent")

    def test_installed_fallback_to_home(self):
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if k != "SOLVENT_HOME"}
            env["HOME"] = home
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(paths, "_is_source_checkout", return_value=False),
            ):
                self.assertEqual(paths.base_dir(), Path(home) / ".solvent")
                self.assertEqual(paths.config_dir(), Path(home) / ".solvent" / ".solvent")
                self.assertTrue((Path(home) / ".solvent").is_dir())


    def test_msys_to_windows_drive_prefix(self):
        """MSYS ``/c/Users/...`` → ``C:/Users/...`` without touching Path/os.name."""
        self.assertEqual(
            paths._msys_to_windows("/c/Users/demo/.solvent-home"),
            "C:/Users/demo/.solvent-home",
        )
        self.assertEqual(paths._msys_to_windows("/d/tmp"), "D:/tmp")
        self.assertIsNone(paths._msys_to_windows("/home/demo"))
        self.assertIsNone(paths._msys_to_windows("C:/Users/demo"))

    def test_solvent_home_msys_drive_prefix(self):
        """On POSIX hosts MSYS paths stay literal; on Windows they translate.

        Do not patch ``os.name``: that mutates the shared ``os`` module and
        makes ``Path(\"C:/...\")`` raise ``NotImplementedError`` (WindowsPath)
        on Linux CI.
        """
        if paths.os.name == "nt":  # pragma: no cover - Windows hosts only
            normalised = paths._normalise_home("/c/Users/demo/.solvent-home")
            self.assertEqual(str(normalised), str(Path("C:/Users/demo/.solvent-home")))
        else:
            normalised = paths._normalise_home("/c/Users/demo/.solvent-home")
            self.assertEqual(normalised, Path("/c/Users/demo/.solvent-home"))
            # Windows branch wiring: call the translator, then Path(translated).
            # Patch Path so we never construct a WindowsPath under a flipped os.name.
            sentinel = object()
            with (
                mock.patch.object(paths.os, "name", "nt"),
                mock.patch.object(
                    paths, "_msys_to_windows", return_value="C:/Users/demo/.solvent-home"
                ) as conv,
                mock.patch.object(paths, "Path", return_value=sentinel) as path_cls,
            ):
                result = paths._normalise_home("/c/Users/demo/.solvent-home")
            conv.assert_called_once_with("/c/Users/demo/.solvent-home")
            path_cls.assert_called_once_with("C:/Users/demo/.solvent-home")
            self.assertIs(result, sentinel)

    def test_solvent_home_override_goes_through_normalise_home(self):
        """base_dir must not bypass _normalise_home for SOLVENT_HOME."""
        with tempfile.TemporaryDirectory() as d:
            with (
                mock.patch.dict(os.environ, {"SOLVENT_HOME": d}),
                mock.patch.object(paths, "_normalise_home", wraps=paths._normalise_home) as norm,
            ):
                paths.base_dir()
                norm.assert_called()
                self.assertEqual(norm.call_args.args[0], d)


class TestTreasuryHonorsHome(unittest.TestCase):
    def test_treasury_default_path_follows_solvent_home(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"SOLVENT_HOME": d}):
                # Treasury() with no explicit path should write under SOLVENT_HOME,
                # not into the package/repo directory.
                from solvent.paths import db_path

                expected = Path(d).resolve() / "data" / "solvent.db"
                self.assertEqual(db_path(), expected)
                from solvent.treasury import Treasury

                t = Treasury(path=db_path())
                t.reset()
                t.seed(5000)
                self.assertEqual(t.balance_cents(), 5000)
                self.assertTrue(expected.exists())


if __name__ == "__main__":
    unittest.main()
