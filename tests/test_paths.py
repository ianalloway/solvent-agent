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
                self.assertEqual(paths.db_path(), base / "data" / "solvent.db")
                self.assertEqual(paths.reports_dir(), base / "data" / "reports")
                self.assertEqual(
                    paths.dashboard_html(), base / "treasury_dashboard.html"
                )
                self.assertTrue(paths.data_dir().is_dir())

    def test_source_checkout_uses_repo_root(self):
        env = {k: v for k, v in os.environ.items() if k != "SOLVENT_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(paths._is_source_checkout())  # repo has pyproject.toml
            self.assertEqual(paths.base_dir(), paths._REPO_ROOT)

    def test_installed_fallback_to_home(self):
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if k != "SOLVENT_HOME"}
            env["HOME"] = home
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(paths, "_is_source_checkout", return_value=False),
            ):
                self.assertEqual(paths.base_dir(), Path(home) / ".solvent")
                self.assertTrue((Path(home) / ".solvent").is_dir())


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
