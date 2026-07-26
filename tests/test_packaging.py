"""Sanity checks for the installable package metadata."""

import sys
import unittest
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    tomllib = None

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"


class TestPackaging(unittest.TestCase):
    def test_pyproject_exists(self):
        self.assertTrue(PYPROJECT.is_file())

    def test_version_attr_present(self):
        import solvent

        self.assertRegex(solvent.__version__, r"^\d+\.\d+\.\d+")

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_console_script_and_extras_declared(self):
        data = tomllib.loads(PYPROJECT.read_text())
        scripts = data["project"]["scripts"]
        self.assertEqual(scripts["solvent"], "solvent.__main__:main")
        # core has no hard deps; functionality is opt-in via extras
        self.assertEqual(data["project"]["dependencies"], [])
        extras = data["project"]["optional-dependencies"]
        for name in ("serve", "telegram", "stripe", "qr", "dev", "all"):
            self.assertIn(name, extras)

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_templates_shipped_as_package_data(self):
        data = tomllib.loads(PYPROJECT.read_text())
        pkg_data = data["tool"]["setuptools"]["package-data"]
        self.assertIn("solvent", pkg_data)
        self.assertTrue(any("templates" in p for p in pkg_data["solvent"]))

    def test_main_entry_point_importable(self):
        from solvent.__main__ import main

        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
