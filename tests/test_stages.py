"""Tests for idempotent job stages."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solvent.agent import Solvent
from solvent.treasury import Treasury


class TestStagesIdempotency(unittest.TestCase):
    def setUp(self):
        self._old_secret = os.environ.get("SOLVENT_DELIVERY_SECRET")
        os.environ["SOLVENT_DELIVERY_SECRET"] = "test-delivery-secret-with-more-than-32-bytes"

    def tearDown(self):
        if self._old_secret is None:
            os.environ.pop("SOLVENT_DELIVERY_SECRET", None)
        else:
            os.environ["SOLVENT_DELIVERY_SECRET"] = self._old_secret

    def test_double_paid_stage_does_not_double_earn(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            agent = Solvent(seed_cents=10_000, fresh=True)
            agent.t.path = db
            agent.t.lock_path = db.with_suffix(".lock")
            agent.t._init_db()

            job = {
                "id": "J-idem",
                "topic": "Idempotency test",
                "budget_cents": 5000,
                "est_tokens": 1000,
                "market_data_calls": 1,
                "web_search_calls": 1,
            }
            link = {"id": "cs_sim", "url": "http://x", "amount_cents": 5000, "simulated": True, "job_id": "J-idem"}
            payment = {
                "paid": True,
                "stripe_ref": "pi_idem",
                "checkout_session_id": "cs_idem",
                "amount_cents": 5000,
            }
            fulfill = {
                "deliverable_path": str(Path(tmp) / "J-idem.md"),
                "tokens": 50,
                "resources_used": [],
                "actual_cost_cents": 0,
                "fulfillment_seconds": 0.5,
                "tool_ctx": type("C", (), {"total_calls": 0, "market_data_calls": 0, "web_search_calls": 0})(),
                "usage": {"total_tokens": 50},
            }
            Path(fulfill["deliverable_path"]).write_text("# brief")

            with mock.patch.object(agent.stripe, "create_checkout_session", return_value=link):
                with mock.patch.object(agent.stripe, "confirm_payment", return_value=payment):
                    with mock.patch("solvent.service.fulfill", return_value=fulfill):
                        with mock.patch("solvent.delivery.send_brief_email", return_value={"simulated": True}):
                            agent.handle_job(job)
                            rev1 = len([e for e in agent.t.entries if e.kind == "revenue" and e.job_id == "J-idem"])
                            agent._runner._stage_paid(job, link, agent._runner._stage_quote(job).get("_quote") or mock.Mock(price_cents=5000, est_cost_cents=100, margin_cents=4900, margin_pct=90))
                            rev2 = len([e for e in agent.t.entries if e.kind == "revenue" and e.job_id == "J-idem"])
            self.assertEqual(rev1, 1)
            self.assertEqual(rev2, 1)


if __name__ == "__main__":
    unittest.main()
