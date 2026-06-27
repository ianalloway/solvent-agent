"""The __main__ subcommand router must dispatch, not fall through to the demo.

Regression: a stray `from .cli import main` used to shadow the router so every
documented subcommand (serve/worker/doctor/...) silently ran the demo CLI.
"""

import unittest
from unittest.mock import patch

import solvent.__main__ as entry


class TestCliRouting(unittest.TestCase):
    def _routes_to(self, argv, target_module, target_attr="main"):
        with patch.object(entry.sys, "argv", argv), \
             patch(f"solvent.{target_module}.{target_attr}") as mock_target, \
             patch("solvent.cli.main") as mock_demo:
            entry.main()
        return mock_target, mock_demo

    def test_finance_routes_to_finance(self):
        target, demo = self._routes_to(["solvent", "finance"], "finance")
        target.assert_called_once()
        demo.assert_not_called()

    def test_report_alias_routes_to_finance(self):
        target, demo = self._routes_to(["solvent", "report"], "finance")
        target.assert_called_once()
        demo.assert_not_called()

    def test_doctor_routes_to_doctor(self):
        target, demo = self._routes_to(["solvent", "doctor"], "doctor")
        target.assert_called_once()
        demo.assert_not_called()

    def test_no_subcommand_runs_demo(self):
        with patch.object(entry.sys, "argv", ["solvent"]), \
             patch("solvent.cli.main") as mock_demo:
            entry.main()
        mock_demo.assert_called_once()

    def test_version_prints_and_does_not_run_demo(self):
        import io
        from contextlib import redirect_stdout
        from solvent import __version__

        for argv in (["solvent", "version"], ["solvent", "--version"], ["solvent", "-V"]):
            buf = io.StringIO()
            with patch.object(entry.sys, "argv", argv), \
                 patch("solvent.cli.main") as mock_demo, redirect_stdout(buf):
                entry.main()
            self.assertIn(__version__, buf.getvalue())
            mock_demo.assert_not_called()

    def test_help_lists_subcommands(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with patch.object(entry.sys, "argv", ["solvent", "help"]), \
             patch("solvent.cli.main") as mock_demo, redirect_stdout(buf):
            entry.main()
        out = buf.getvalue()
        self.assertIn("Commands:", out)
        self.assertIn("finance", out)
        self.assertIn("serve", out)
        mock_demo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
