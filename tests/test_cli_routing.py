"""The package entry point must dispatch documented commands exactly once."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import solvent.__main__ as entry


class TestCliRouting(unittest.TestCase):
    def _routes_to(self, argv, target_module, target_attr="main"):
        with (
            patch.object(entry.sys, "argv", argv),
            patch(f"solvent.{target_module}.{target_attr}") as target,
            patch("solvent.cli.main") as demo,
        ):
            entry.main()
        return target, demo

    def test_finance_routes_to_finance(self):
        target, demo = self._routes_to(["solvent", "finance"], "finance")
        target.assert_called_once_with()
        demo.assert_not_called()

    def test_report_alias_routes_to_finance(self):
        target, demo = self._routes_to(["solvent", "report"], "finance")
        target.assert_called_once_with()
        demo.assert_not_called()

    def test_doctor_routes_to_doctor(self):
        target, demo = self._routes_to(["solvent", "doctor"], "doctor")
        target.assert_called_once_with()
        demo.assert_not_called()

    def test_webhooks_routes_to_webhook_cli(self):
        target, demo = self._routes_to(["solvent", "webhooks", "stats"], "webhook_log")
        target.assert_called_once_with()
        demo.assert_not_called()

    def test_tui_routes_to_run(self):
        target, demo = self._routes_to(["solvent", "tui"], "tui", "run")
        target.assert_called_once_with()
        demo.assert_not_called()

    def test_retry_alias_delegates_to_jobs_cli(self):
        seen_argv = []

        def capture_argv():
            seen_argv.append(list(entry.sys.argv))

        with (
            patch.object(entry.sys, "argv", ["solvent", "retry", "J-123"]),
            patch("solvent.job_cmd.main", side_effect=capture_argv) as jobs,
            patch("solvent.cli.main") as demo,
        ):
            entry.main()

        jobs.assert_called_once_with()
        self.assertEqual(seen_argv, [["solvent", "retry", "J-123"]])
        demo.assert_not_called()

    def test_no_subcommand_runs_demo(self):
        with patch.object(entry.sys, "argv", ["solvent"]), patch("solvent.cli.main") as demo:
            entry.main()
        demo.assert_called_once_with()

    def test_demo_options_run_demo(self):
        with (
            patch.object(entry.sys, "argv", ["solvent", "--interactive"]),
            patch("solvent.cli.main") as demo,
        ):
            entry.main()
        demo.assert_called_once_with()

    def test_version_prints_and_does_not_run_demo(self):
        from solvent import __version__

        for argv in (
            ["solvent", "version"],
            ["solvent", "--version"],
            ["solvent", "-V"],
        ):
            buffer = io.StringIO()
            with (
                patch.object(entry.sys, "argv", argv),
                patch("solvent.cli.main") as demo,
                redirect_stdout(buffer),
            ):
                entry.main()
            self.assertIn(__version__, buffer.getvalue())
            demo.assert_not_called()

    def test_help_lists_subcommands(self):
        buffer = io.StringIO()
        with (
            patch.object(entry.sys, "argv", ["solvent", "help"]),
            patch("solvent.cli.main") as demo,
            redirect_stdout(buffer),
        ):
            entry.main()

        output = buffer.getvalue()
        self.assertIn("Commands:", output)
        self.assertIn("finance", output)
        self.assertIn("serve", output)
        self.assertIn("jobs", output)
        demo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
