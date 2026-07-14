"""Tests for solvent/treasury.py — the agent's balance sheet."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from solvent.treasury import Treasury, LedgerEntry


def _tmp() -> Treasury:
    d = tempfile.mkdtemp()
    return Treasury(path=Path(d) / "test.db")


class TestLedgerEntry(unittest.TestCase):
    def test_signed_cents_revenue(self):
        e = LedgerEntry(kind="revenue", amount_cents=1000, memo="sale")
        self.assertEqual(e.signed_cents(), 1000)

    def test_signed_cents_expense(self):
        e = LedgerEntry(kind="expense", amount_cents=300, memo="cost")
        self.assertEqual(e.signed_cents(), -300)

    def test_signed_cents_capital(self):
        e = LedgerEntry(kind="capital", amount_cents=5000, memo="seed")
        self.assertEqual(e.signed_cents(), 5000)

    def test_id_auto_generated(self):
        e = LedgerEntry(kind="revenue", amount_cents=100, memo="m")
        self.assertTrue(e.id.startswith("le_"))
        self.assertGreater(len(e.id), 4)

    def test_ts_auto_generated(self):
        before = time.time()
        e = LedgerEntry(kind="revenue", amount_cents=100, memo="m")
        self.assertGreaterEqual(e.ts, before)


class TestTreasuryLedger(unittest.TestCase):
    def setUp(self):
        self.t = _tmp()

    def test_initial_balance_zero(self):
        self.assertEqual(self.t.balance_cents(), 0)

    def test_seed_increases_balance(self):
        self.t.seed(10000)
        self.assertEqual(self.t.balance_cents(), 10000)

    def test_earn_increases_balance(self):
        self.t.earn(500, "sale", job_id="j1")
        self.assertEqual(self.t.balance_cents(), 500)

    def test_spend_decreases_balance(self):
        self.t.seed(1000)
        self.t.spend(200, "vendor fee")
        self.assertEqual(self.t.balance_cents(), 800)

    def test_balance_across_multiple_entries(self):
        self.t.seed(10000)
        self.t.earn(2000, "job1 revenue", job_id="j1")
        self.t.spend(500, "inference cost", job_id="j1")
        self.assertEqual(self.t.balance_cents(), 11500)

    def test_record_revenue(self):
        entry = self.t.record("revenue", 1000, "payment")
        self.assertIsInstance(entry, LedgerEntry)
        self.assertEqual(entry.kind, "revenue")
        self.assertEqual(entry.amount_cents, 1000)

    def test_record_expense(self):
        entry = self.t.record("expense", 300, "cost")
        self.assertEqual(entry.kind, "expense")

    def test_record_with_job_id(self):
        entry = self.t.record("revenue", 500, "job payment", job_id="j99")
        self.assertEqual(entry.job_id, "j99")

    def test_entries_persisted(self):
        self.t.earn(1000, "rev")
        entries = self.t.entries
        self.assertGreater(len(entries), 0)

    def test_revenue_cents(self):
        self.t.earn(1000, "r1")
        self.t.earn(500, "r2")
        self.assertEqual(self.t.revenue_cents(), 1500)

    def test_expense_cents(self):
        self.t.spend(200, "e1")
        self.t.spend(100, "e2")
        self.assertEqual(self.t.expense_cents(), 300)

    def test_capital_cents(self):
        self.t.seed(5000)
        self.assertEqual(self.t.capital_cents(), 5000)

    def test_net_profit_cents(self):
        self.t.earn(2000, "revenue")
        self.t.spend(500, "cost")
        self.assertEqual(self.t.net_profit_cents(), 1500)

    def test_margin_pct_zero_with_no_revenue(self):
        self.assertEqual(self.t.margin_pct(), 0.0)

    def test_margin_pct_calculated(self):
        self.t.earn(1000, "rev")
        self.t.spend(400, "cost")
        pct = self.t.margin_pct()
        self.assertAlmostEqual(pct, 60.0, places=0)

    def test_job_pnl_cents(self):
        self.t.earn(2000, "job rev", job_id="j1")
        self.t.spend(800, "job cost", job_id="j1")
        self.assertEqual(self.t.job_pnl_cents("j1"), 1200)

    def test_job_pnl_zero_no_entries(self):
        self.assertEqual(self.t.job_pnl_cents("nonexistent"), 0)

    def test_snapshot_keys(self):
        self.t.earn(1000, "rev")
        snap = self.t.snapshot()
        self.assertIn("balance_cents", snap)
        self.assertIn("revenue_cents", snap)
        self.assertIn("expense_cents", snap)


class TestTreasuryJobQueue(unittest.TestCase):
    def setUp(self):
        self.t = _tmp()

    def test_upsert_and_get_job(self):
        self.t.upsert_job("j1", "pending_quote", topic="AI chips")
        job = self.t.get_job("j1")
        self.assertIsNotNone(job)
        self.assertEqual(job["id"], "j1")
        self.assertEqual(job["status"], "pending_quote")

    def test_upsert_updates_existing_job(self):
        self.t.upsert_job("j1", "pending_quote")
        self.t.upsert_job("j1", "in_progress")
        job = self.t.get_job("j1")
        self.assertEqual(job["status"], "in_progress")

    def test_list_jobs_empty(self):
        self.assertEqual(self.t.list_jobs(), [])

    def test_list_jobs_returns_all(self):
        self.t.upsert_job("j1", "pending_quote")
        self.t.upsert_job("j2", "in_progress")
        jobs = self.t.list_jobs()
        ids = {j["id"] for j in jobs}
        self.assertIn("j1", ids)
        self.assertIn("j2", ids)

    def test_list_jobs_by_status(self):
        self.t.upsert_job("j1", "pending_quote")
        self.t.upsert_job("j2", "in_progress")
        self.t.upsert_job("j3", "completed")
        active = self.t.list_jobs_by_status(["pending_quote", "in_progress"])
        ids = {j["id"] for j in active}
        self.assertIn("j1", ids)
        self.assertIn("j2", ids)
        self.assertNotIn("j3", ids)

    def test_get_job_nonexistent_returns_none(self):
        self.assertIsNone(self.t.get_job("nope"))

    def test_claim_job_succeeds(self):
        self.t.upsert_job("j1", "pending_quote")
        claimed = self.t.claim_job("j1")
        self.assertTrue(claimed)

    def test_claim_job_twice_fails(self):
        self.t.upsert_job("j1", "pending_quote")
        self.t.claim_job("j1")
        claimed_again = self.t.claim_job("j1", lease_seconds=3600)
        self.assertFalse(claimed_again)

    def test_release_job_allows_reclaim(self):
        self.t.upsert_job("j1", "pending_quote")
        self.t.claim_job("j1")
        self.t.release_job("j1")
        claimed = self.t.claim_job("j1")
        self.assertTrue(claimed)

    def test_job_has_revenue_false_initially(self):
        self.t.upsert_job("j1", "pending_quote")
        self.assertFalse(self.t.job_has_revenue("j1"))

    def test_job_has_revenue_true_after_earn(self):
        self.t.upsert_job("j1", "pending_quote")
        self.t.earn(1000, "payment", job_id="j1")
        self.assertTrue(self.t.job_has_revenue("j1"))


class TestTreasuryStages(unittest.TestCase):
    def setUp(self):
        self.t = _tmp()

    def test_stage_not_completed_initially(self):
        self.assertFalse(self.t.stage_completed("j1", "fulfill"))

    def test_complete_stage_marks_done(self):
        self.t.complete_stage("j1", "fulfill", "ik_j1_fulfill", result={"ok": True})
        self.assertTrue(self.t.stage_completed("j1", "fulfill"))

    def test_get_stage_returns_result(self):
        self.t.complete_stage("j1", "quote", "ik_j1_quote", result={"price": 4900})
        stage = self.t.get_stage("ik_j1_quote")
        self.assertIsNotNone(stage)
        self.assertEqual(stage["job_id"], "j1")
        self.assertEqual(stage["stage"], "quote")

    def test_get_stage_nonexistent_returns_none(self):
        self.assertIsNone(self.t.get_stage("no_such_key"))

    def test_complete_stage_idempotent(self):
        self.t.complete_stage("j1", "fulfill", "ik_j1_fulfill", result={"ok": True})
        self.t.complete_stage("j1", "fulfill", "ik_j1_fulfill", result={"ok": True})
        self.assertTrue(self.t.stage_completed("j1", "fulfill"))


class TestTreasuryEvents(unittest.TestCase):
    def setUp(self):
        self.t = _tmp()

    def test_record_event_and_list(self):
        self.t.record_event("j1", "fulfill", {"status": "ok"})
        events = self.t.list_events(job_id="j1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["job_id"], "j1")
        self.assertEqual(events[0]["stage"], "fulfill")

    def test_list_events_all(self):
        self.t.record_event("j1", "quote", {})
        self.t.record_event("j2", "fulfill", {})
        events = self.t.list_events()
        self.assertGreaterEqual(len(events), 2)

    def test_list_events_limit(self):
        for i in range(10):
            self.t.record_event(f"j{i}", "stage", {})
        events = self.t.list_events(limit=3)
        self.assertEqual(len(events), 3)

    def test_list_events_filtered_by_job(self):
        self.t.record_event("j1", "quote", {})
        self.t.record_event("j2", "fulfill", {})
        events = self.t.list_events(job_id="j1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["job_id"], "j1")


class TestTreasuryMetrics(unittest.TestCase):
    def setUp(self):
        self.t = _tmp()

    def test_upsert_and_get_metrics(self):
        self.t.upsert_metrics("j1", est_cost_cents=300, actual_cost_cents=280)
        m = self.t.get_metrics("j1")
        self.assertIsNotNone(m)
        self.assertEqual(m["est_cost_cents"], 300)
        self.assertEqual(m["actual_cost_cents"], 280)

    def test_get_metrics_nonexistent_returns_none(self):
        self.assertIsNone(self.t.get_metrics("nope"))

    def test_list_metrics_empty(self):
        self.assertEqual(self.t.list_metrics(), [])

    def test_list_metrics_returns_all(self):
        self.t.upsert_metrics("j1", actual_cost_cents=100)
        self.t.upsert_metrics("j2", actual_cost_cents=200)
        metrics = self.t.list_metrics()
        ids = {m["job_id"] for m in metrics}
        self.assertIn("j1", ids)
        self.assertIn("j2", ids)

    def test_upsert_metrics_updates_existing(self):
        self.t.upsert_metrics("j1", actual_cost_cents=100)
        self.t.upsert_metrics("j1", actual_cost_cents=150)
        m = self.t.get_metrics("j1")
        self.assertEqual(m["actual_cost_cents"], 150)

    def test_increment_retry_count(self):
        self.t.upsert_job("j1", "in_progress")
        count = self.t.increment_retry_count("j1")
        self.assertEqual(count, 1)
        count2 = self.t.increment_retry_count("j1")
        self.assertEqual(count2, 2)


class TestTreasuryCheckout(unittest.TestCase):
    def setUp(self):
        self.t = _tmp()

    def test_upsert_and_get_checkout(self):
        self.t.upsert_checkout("j1", "cs_abc123", "https://pay.example", "open")
        co = self.t.get_checkout("j1")
        self.assertIsNotNone(co)
        self.assertEqual(co["session_id"], "cs_abc123")

    def test_get_checkout_nonexistent_returns_none(self):
        self.assertIsNone(self.t.get_checkout("no_job"))


if __name__ == "__main__":
    unittest.main()
