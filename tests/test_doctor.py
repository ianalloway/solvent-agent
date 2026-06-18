import os
import tempfile
import unittest

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
