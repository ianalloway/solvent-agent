"""The __main__ subcommand router must dispatch, not fall through to the demo.

Regression: a stray `from .cli import main` used to shadow the router so every
documented subcommand (serve/worker/doctor/...) silently ran the demo CLI.
"""

import json
import os
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

import solvent.__main__ as entry


class TestCliRouting(unittest.TestCase):
    def _routes_to(self, argv, target_module, target_attr="main"):
        with (
            patch.object(entry.sys, "argv", argv),
            patch(f"solvent.{target_module}.{target_attr}") as mock_target,
            patch("solvent.cli.main") as mock_demo,
        ):
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
        with (
            patch.object(entry.sys, "argv", ["solvent"]),
            patch("solvent.cli.main") as mock_demo,
        ):
            entry.main()
        mock_demo.assert_called_once()

    def test_version_prints_and_does_not_run_demo(self):
        from solvent import __version__

        for argv in (
            ["solvent", "version"],
            ["solvent", "--version"],
            ["solvent", "-V"],
        ):
            buf = StringIO()
            with (
                patch.object(entry.sys, "argv", argv),
                patch("solvent.cli.main") as mock_demo,
                patch("sys.stdout", buf),
            ):
                entry.main()
            self.assertIn(__version__, buf.getvalue())
            mock_demo.assert_not_called()

    def test_help_lists_subcommands(self):
        buf = StringIO()
        with (
            patch.object(entry.sys, "argv", ["solvent", "help"]),
            patch("solvent.cli.main") as mock_demo,
            patch("sys.stdout", buf),
        ):
            entry.main()
        out = buf.getvalue()
        self.assertIn("Commands:", out)
        self.assertIn("finance", out)
        self.assertIn("serve", out)
        mock_demo.assert_not_called()

    # ── additional subcommand routes ──────────────────────────────────────

    def test_init_routes_to_init(self):
        target, demo = self._routes_to(["solvent", "init"], "init")
        target.assert_called_once()
        demo.assert_not_called()

    def test_status_routes_to_status(self):
        target, demo = self._routes_to(["solvent", "status"], "status")
        target.assert_called_once()
        demo.assert_not_called()

    def test_jobs_routes_to_job_cmd(self):
        target, demo = self._routes_to(["solvent", "jobs"], "job_cmd")
        target.assert_called_once()
        demo.assert_not_called()

    def test_upgrade_routes_to_upgrade(self):
        target, demo = self._routes_to(["solvent", "upgrade"], "upgrade")
        target.assert_called_once()
        demo.assert_not_called()

    def test_logs_routes_to_logs(self):
        target, demo = self._routes_to(["solvent", "logs"], "logs")
        target.assert_called_once()
        demo.assert_not_called()

    def test_config_routes_to_config_cmd(self):
        target, demo = self._routes_to(["solvent", "config"], "config_cmd")
        target.assert_called_once()
        demo.assert_not_called()

    def test_serve_routes_to_server(self):
        target, demo = self._routes_to(["solvent", "serve"], "server")
        target.assert_called_once()
        demo.assert_not_called()

    def test_worker_routes_to_worker(self):
        target, demo = self._routes_to(["solvent", "worker"], "worker")
        target.assert_called_once()
        demo.assert_not_called()

    def test_reconcile_routes_to_reconcile(self):
        target, demo = self._routes_to(["solvent", "reconcile"], "reconcile")
        target.assert_called_once()
        demo.assert_not_called()

    def test_telegram_routes_to_telegram_channel(self):
        target, demo = self._routes_to(
            ["solvent", "telegram"], "channels.telegram"
        )
        target.assert_called_once()
        demo.assert_not_called()

    def test_pairing_routes_to_pairing(self):
        target, demo = self._routes_to(["solvent", "pairing"], "pairing")
        target.assert_called_once()
        demo.assert_not_called()

    def test_workspace_routes_to_workspace(self):
        target, demo = self._routes_to(["solvent", "workspace"], "workspace")
        target.assert_called_once()
        demo.assert_not_called()

    # ── retry subcommand (special: inline logic, no main dispatch) ────────

    def test_retry_no_job_id_prints_usage_and_exits(self):
        buf = StringIO()
        with (
            patch.object(entry.sys, "argv", ["solvent", "retry"]),
            patch("solvent.cli.main") as mock_demo,
            patch("sys.stdout", buf),
            self.assertRaises(SystemExit) as ctx,
        ):
            entry.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Usage", buf.getvalue())
        mock_demo.assert_not_called()

    def test_retry_with_job_id_dispatches_and_prints_json(self):
        fake_result = {"job_id": "abc123", "status": "retried"}
        buf = StringIO()
        with (
            patch.object(entry.sys, "argv", ["solvent", "retry", "abc123"]),
            patch("solvent.cli.main") as mock_demo,
            patch("solvent.guardrails.Guardrails"),
            patch("solvent.stages.StageRunner") as MockRunner,
            patch("solvent.stripe_client.StripeClient"),
            patch("solvent.treasury.Treasury"),
            patch("sys.stdout", buf),
        ):
            mock_runner = MockRunner.return_value
            mock_runner.retry_job.return_value = fake_result
            entry.main()
        mock_demo.assert_not_called()
        out = buf.getvalue()
        self.assertIn("abc123", out)
        self.assertIn("retried", out)
        MockRunner.assert_called_once()

    # ── webhooks subcommand (inline dispatch on sub-subcommand) ───────────

    def test_webhooks_stats(self):
        buf = StringIO()
        with (
            patch.object(entry.sys, "argv", ["solvent", "webhooks", "stats"]),
            patch("solvent.cli.main") as mock_demo,
            patch("solvent.webhook_log.WebhookLog") as MockWL,
            patch("sys.stdout", buf),
        ):
            mock_wl = MockWL.return_value
            mock_wl.stats.return_value = {"total": 42, "failed": 3}
            entry.main()
        mock_demo.assert_not_called()
        MockWL.assert_called_once()
        mock_wl.stats.assert_called_once()
        self.assertIn("42", buf.getvalue())

    def test_webhooks_list(self):
        buf = StringIO()
        with (
            patch.object(entry.sys, "argv", ["solvent", "webhooks", "list"]),
            patch("solvent.cli.main") as mock_demo,
            patch("solvent.webhook_log.WebhookLog") as MockWL,
            patch("sys.stdout", buf),
        ):
            mock_wl = MockWL.return_value
            mock_wl.list_recent.return_value = [
                {
                    "received_at_fmt": "2026-08-03T12:00:00",
                    "status": "ok",
                    "event_type": "payment_intent.succeeded",
                    "event_id": "evt_0123456789abcdef",
                }
            ]
            entry.main()
        mock_demo.assert_not_called()
        MockWL.assert_called_once()
        mock_wl.list_recent.assert_called_once_with(20)

    def test_webhooks_failed(self):
        buf = StringIO()
        with (
            patch.object(entry.sys, "argv", ["solvent", "webhooks", "failed"]),
            patch("solvent.cli.main") as mock_demo,
            patch("solvent.webhook_log.WebhookLog") as MockWL,
            patch("sys.stdout", buf),
        ):
            mock_wl = MockWL.return_value
            mock_wl.list_failed.return_value = [
                {
                    "event_id": "evt_deadbeef",
                    "event_type": "payment_failed",
                    "error": "stripe_network_error",
                }
            ]
            entry.main()
        mock_demo.assert_not_called()
        MockWL.assert_called_once()
        mock_wl.list_failed.assert_called_once()

    # ── update-check env var path ─────────────────────────────────────────

    def test_update_check_env_var_triggers_background_hint(self):
        buf = StringIO()
        with (
            patch.dict(os.environ, {"SOLVENT_UPDATE_CHECK": "1"}),
            patch.object(entry.sys, "argv", ["solvent"]),
            patch("solvent.cli.main") as mock_demo,
            patch("solvent.upgrade.background_update_hint") as mock_hint,
            patch("sys.stdout", buf),
        ):
            entry.main()
        mock_demo.assert_called_once()
        mock_hint.assert_called_once()


if __name__ == "__main__":
    unittest.main()
