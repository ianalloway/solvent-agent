"""Tests for solvent.worker — the async job runner (run_worker + main)."""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from solvent.worker import run_worker


class TestRunWorker(unittest.TestCase):
    """run_worker must drive the claim → advance → release loop correctly."""

    def _make_mock_agent(self):
        agent = mock.MagicMock()
        agent.t = mock.MagicMock()
        return agent

    @mock.patch("solvent.worker.list_claimable", return_value=[])
    @mock.patch("solvent.worker.resume_incomplete_jobs", return_value=[])
    @mock.patch("solvent.worker.Solvent")
    def test_no_resumed_no_claimable_once(
        self, mock_solvent_cls, mock_resume, mock_list
    ):
        agent = self._make_mock_agent()
        mock_solvent_cls.return_value = agent

        run_worker(once=True)

        mock_solvent_cls.assert_called_once_with(
            seed_cents=10_000, fresh=False, sync_payment=False
        )
        agent.advance_job.assert_not_called()
        agent.t.release_job.assert_not_called()
        mock_list.assert_called_once_with(agent.t)

    @mock.patch("solvent.worker.list_claimable", return_value=[])
    @mock.patch("solvent.worker.resume_incomplete_jobs")
    @mock.patch("solvent.worker.Solvent")
    def test_resumed_jobs_are_advanced(self, mock_solvent_cls, mock_resume, mock_list):
        agent = self._make_mock_agent()
        mock_solvent_cls.return_value = agent
        mock_resume.return_value = ["J-resume-1", "J-resume-2"]

        run_worker(once=True)

        mock_resume.assert_called_once_with(agent.t)
        assert agent.advance_job.call_args_list == [
            mock.call("J-resume-1"),
            mock.call("J-resume-2"),
        ]

    @mock.patch("solvent.worker.list_claimable")
    @mock.patch("solvent.worker.resume_incomplete_jobs", return_value=[])
    @mock.patch("solvent.worker.Solvent")
    def test_claimable_jobs_claimed_advanced_and_released(
        self, mock_solvent_cls, mock_resume, mock_list
    ):
        agent = self._make_mock_agent()
        mock_solvent_cls.return_value = agent
        mock_list.return_value = [
            {"id": "J-1"},
            {"id": "J-2"},
        ]
        agent.t.claim_job.return_value = True

        run_worker(once=True)

        assert agent.t.claim_job.call_args_list == [
            mock.call("J-1"),
            mock.call("J-2"),
        ]
        assert agent.advance_job.call_args_list == [
            mock.call("J-1"),
            mock.call("J-2"),
        ]
        assert agent.t.release_job.call_args_list == [
            mock.call("J-1"),
            mock.call("J-2"),
        ]

    @mock.patch("solvent.worker.list_claimable")
    @mock.patch("solvent.worker.resume_incomplete_jobs", return_value=[])
    @mock.patch("solvent.worker.Solvent")
    def test_unclaimed_job_is_skipped(self, mock_solvent_cls, mock_resume, mock_list):
        agent = self._make_mock_agent()
        mock_solvent_cls.return_value = agent
        mock_list.return_value = [{"id": "J-locked"}]
        agent.t.claim_job.return_value = False

        run_worker(once=True)

        agent.t.claim_job.assert_called_once_with("J-locked")
        agent.advance_job.assert_not_called()
        agent.t.release_job.assert_not_called()

    @mock.patch("solvent.worker.list_claimable")
    @mock.patch("solvent.worker.resume_incomplete_jobs", return_value=[])
    @mock.patch("solvent.worker.Solvent")
    def test_release_called_even_when_advance_raises(
        self, mock_solvent_cls, mock_resume, mock_list
    ):
        agent = self._make_mock_agent()
        mock_solvent_cls.return_value = agent
        mock_list.return_value = [{"id": "J-boom"}]
        agent.t.claim_job.return_value = True
        agent.advance_job.side_effect = RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            run_worker(once=True)

        agent.advance_job.assert_called_once_with("J-boom")
        agent.t.release_job.assert_called_once_with("J-boom")

    @mock.patch("solvent.worker.list_claimable", return_value=[])
    @mock.patch("solvent.worker.resume_incomplete_jobs", return_value=[])
    @mock.patch("solvent.worker.Solvent")
    def test_fresh_and_seed_cents_forwarded(
        self, mock_solvent_cls, mock_resume, mock_list
    ):
        agent = self._make_mock_agent()
        mock_solvent_cls.return_value = agent

        run_worker(once=True, seed_cents=50_000, fresh=True)

        mock_solvent_cls.assert_called_once_with(
            seed_cents=50_000, fresh=True, sync_payment=False
        )

    @mock.patch("solvent.worker.time.sleep")
    @mock.patch("solvent.worker.list_claimable", return_value=[])
    @mock.patch("solvent.worker.resume_incomplete_jobs", return_value=[])
    @mock.patch("solvent.worker.Solvent")
    def test_poll_interval_loops_until_stopped(
        self, mock_solvent_cls, mock_resume, mock_list, mock_sleep
    ):
        agent = self._make_mock_agent()
        mock_solvent_cls.return_value = agent

        # Stop after 2 sleep calls to avoid infinite loop
        stop_after = [2]

        def _stop(*_a, **_kw):
            stop_after[0] -= 1
            if stop_after[0] <= 0:
                sys.exit(0)

        mock_sleep.side_effect = _stop

        with self.assertRaises(SystemExit):
            run_worker(once=False, poll_interval=1.0)

        assert mock_sleep.call_count >= 1
        # list_claimable should be called on every pass
        assert mock_list.call_count >= 1


class TestWorkerMain(unittest.TestCase):
    """main() should parse args and forward them to run_worker."""

    @mock.patch("solvent.worker.run_worker")
    def test_main_forwards_args(self, mock_run_worker):
        """main() parses sys.argv and maps --seed/--keep-balance correctly."""
        from solvent import worker as worker_mod

        test_args = [
            "solvent-worker",
            "--once",
            "--poll-interval",
            "3.0",
            "--seed",
            "200.0",
            "--keep-balance",
        ]

        with mock.patch.object(sys, "argv", test_args):
            worker_mod.main()

        mock_run_worker.assert_called_once_with(
            once=True,
            poll_interval=3.0,
            seed_cents=20_000,  # 200.0 * 100
            fresh=False,  # --keep-balance → fresh=False
        )


if __name__ == "__main__":
    unittest.main()
