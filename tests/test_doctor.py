import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from solvent import doctor
from solvent.doctor import run_checks


class TestDoctor(unittest.TestCase):
    def test_run_checks_structure(self):
        checks = run_checks()
        self.assertGreater(len(checks), 0)
        names = {c["name"] for c in checks}
        self.assertIn("sqlite_connect", names)
        self.assertIn("treasury_balance", names)

    def test_all_have_ok_flag(self):
        for c in run_checks():
            self.assertIn("ok", c)
            self.assertIn("name", c)

    def test_install_checks_present(self):
        names = {c["name"] for c in run_checks()}
        for name in ("python_version", "install_mode", "data_home", "optional_extras"):
            self.assertIn(name, names)

    def test_python_version_passes_on_supported_runtime(self):
        check = next(c for c in run_checks() if c["name"] == "python_version")
        self.assertTrue(check["ok"])  # test runner is >= 3.10

    def test_data_home_reflects_solvent_home(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"SOLVENT_HOME": d}):
                check = next(c for c in run_checks() if c["name"] == "data_home")
                self.assertTrue(check["ok"])
                self.assertIn(d, check["detail"])

    def test_optional_extras_never_fails(self):
        check = next(c for c in run_checks() if c["name"] == "optional_extras")
        self.assertTrue(check["ok"])  # extras are optional; reporting only

    def test_main_prints_quickstart(self):
        buf = io.StringIO()
        # run_checks may fail on a check -> main() exits; tolerate SystemExit
        try:
            with redirect_stdout(buf):
                doctor.main()
        except SystemExit:
            pass
        out = buf.getvalue()
        self.assertIn("Quickstart", out)
        self.assertIn("solvent finance", out)
