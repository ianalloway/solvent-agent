import time
import unittest
from unittest.mock import MagicMock

from solvent.guardrails import Decision, GuardrailError, Guardrails, SpendPolicy
from solvent.treasury import LedgerEntry


class TestGuardrails(unittest.TestCase):
    """Unit tests for the NemoClaw-style spend safety guardrails of SOLVENT."""

    def setUp(self) -> None:
        # Create a mock Treasury object
        self.mock_treasury = MagicMock()
        # Set a default balance of $100.00 (10000 cents)
        self.mock_treasury.balance_cents.return_value = 10000
        # No entries by default
        self.mock_treasury.entries = []

        # Standard guardrails instance with default policy
        self.guard = Guardrails(self.mock_treasury)

    def test_default_policy(self) -> None:
        """Test that default spend policy boundaries are set correctly."""
        policy = SpendPolicy()
        self.assertEqual(policy.max_txn_cents, 5000)
        self.assertEqual(policy.daily_budget_cents, 25000)
        self.assertEqual(policy.min_reserve_cents, 2000)
        self.assertIn("nvidia-nemotron", policy.vendor_allowlist)
        self.assertIn("market-data-api", policy.vendor_allowlist)

    def test_vendor_allowlist(self) -> None:
        """Test vendor allowlist restrictions."""
        # Allowed vendor should pass
        self.assertTrue(self.guard.approve(100, "nvidia-nemotron"))

        # Disallowed vendor should raise error and return False in approve
        with self.assertRaises(GuardrailError) as ctx:
            self.guard.check_spend(100, "unapproved-vendor")
        self.assertIn("not on allowlist", str(ctx.exception))

        self.assertFalse(self.guard.approve(100, "unapproved-vendor"))

    def test_max_transaction_cap(self) -> None:
        """Test per-transaction spending limit."""
        # Max transaction is 5000 cents ($50).
        # $49.99 should pass
        self.assertTrue(self.guard.approve(4999, "nvidia-nemotron"))
        # $50.00 should pass
        self.assertTrue(self.guard.approve(5000, "nvidia-nemotron"))

        # $50.01 should fail
        with self.assertRaises(GuardrailError) as ctx:
            self.guard.check_spend(5001, "nvidia-nemotron")
        self.assertIn("exceeds per-transaction cap", str(ctx.exception))

        self.assertFalse(self.guard.approve(5001, "nvidia-nemotron"))

    def test_daily_spend_budget(self) -> None:
        """Test daily rolling spend budget (rolling 24h window)."""
        # Ensure we have plenty of cash balance to avoid cash reserve trigger
        self.mock_treasury.balance_cents.return_value = 50000
        now = time.time()
        # Create mock entries totaling 22,000 cents of expense in the last 24h
        self.mock_treasury.entries = [
            LedgerEntry(kind="expense", amount_cents=12000, memo="recent 1", ts=now - 3600),
            LedgerEntry(kind="expense", amount_cents=10000, memo="recent 2", ts=now - 7200),
            LedgerEntry(kind="expense", amount_cents=20000, memo="old expense", ts=now - 90000),
            LedgerEntry(kind="revenue", amount_cents=30000, memo="recent revenue", ts=now - 1000),
        ]

        # Spent in last 24h: 12000 + 10000 = 22000 cents ($220)
        self.assertEqual(self.guard._spent_last_24h(), 22000)

        # Daily budget is 25000 cents ($250).
        # We can spend 3000 more cents ($30).
        # Spending $29.99 should pass
        self.assertTrue(self.guard.approve(2999, "nvidia-nemotron"))
        # Spending $30.00 should pass
        self.assertTrue(self.guard.approve(3000, "nvidia-nemotron"))

        # Spending $30.01 should exceed daily budget
        with self.assertRaises(GuardrailError) as ctx:
            self.guard.check_spend(3001, "nvidia-nemotron")
        self.assertIn("would exceed 24h spend budget", str(ctx.exception))

        self.assertFalse(self.guard.approve(3001, "nvidia-nemotron"))

    def test_solvency_cash_reserve(self) -> None:
        """Test treasury minimum cash reserve rule."""
        # Daily budget has plenty of room, but treasury balance is low.
        self.mock_treasury.balance_cents.return_value = 2500  # $25.00
        # Min reserve is 2000 cents ($20.00)
        # Spending 500 cents ($5.00) leaves exactly $20.00, should pass.
        self.assertTrue(self.guard.approve(500, "nvidia-nemotron"))

        # Spending 501 cents ($5.01) leaves $19.99, which breaches the reserve.
        with self.assertRaises(GuardrailError) as ctx:
            self.guard.check_spend(501, "nvidia-nemotron")
        self.assertIn("would breach minimum cash reserve", str(ctx.exception))

        self.assertFalse(self.guard.approve(501, "nvidia-nemotron"))

    def test_projected_roi_margin(self) -> None:
        """Test that spending on jobs with negative or zero projected margins is blocked."""
        # Case 1: Job margin is positive -> approved
        self.assertTrue(self.guard.approve(500, "nvidia-nemotron", projected_job_margin_cents=100))

        # Case 2: Job margin is zero -> blocked
        with self.assertRaises(GuardrailError) as ctx:
            self.guard.check_spend(500, "nvidia-nemotron", projected_job_margin_cents=0)
        self.assertIn("job projected to be unprofitable", str(ctx.exception))
        self.assertFalse(self.guard.approve(500, "nvidia-nemotron", projected_job_margin_cents=0))

        # Case 3: Job margin is negative -> blocked
        with self.assertRaises(GuardrailError) as ctx:
            self.guard.check_spend(500, "nvidia-nemotron", projected_job_margin_cents=-50)
        self.assertIn("job projected to be unprofitable", str(ctx.exception))
        self.assertFalse(self.guard.approve(500, "nvidia-nemotron", projected_job_margin_cents=-50))

        # Case 4: Projected margin is not provided (None) -> approved
        self.assertTrue(self.guard.approve(500, "nvidia-nemotron", projected_job_margin_cents=None))


class TestGuardrailDecision(unittest.TestCase):
    """The structured Decision API used for auditable spend screening."""

    def setUp(self) -> None:
        self.t = MagicMock()
        self.t.balance_cents.return_value = 10000
        self.t.entries = []
        self.guard = Guardrails(self.t)

    def test_approved_decision_carries_context(self) -> None:
        d = self.guard.evaluate(500, "nvidia-nemotron", projected_job_margin_cents=100)
        self.assertIsInstance(d, Decision)
        self.assertTrue(d.allowed)
        self.assertIsNone(d.rule)
        self.assertEqual(d.reason, "approved")
        self.assertEqual(d.amount_cents, 500)
        self.assertEqual(d.vendor, "nvidia-nemotron")
        self.assertEqual(d.balance_cents, 10000)

    def test_denied_decision_names_the_rule(self) -> None:
        cases = [
            ({"amount_cents": 100, "vendor": "rogue-vendor"}, "vendor_allowlist"),
            ({"amount_cents": 9999, "vendor": "nvidia-nemotron"}, "max_txn_cap"),
        ]
        for kwargs, rule in cases:
            d = self.guard.evaluate(**kwargs)
            self.assertFalse(d.allowed)
            self.assertEqual(d.rule, rule)
            self.assertTrue(d.reason)

    def test_reserve_rule_id(self) -> None:
        self.t.balance_cents.return_value = 2500
        d = self.guard.evaluate(501, "nvidia-nemotron")
        self.assertFalse(d.allowed)
        self.assertEqual(d.rule, "min_reserve")

    def test_roi_rule_id(self) -> None:
        d = self.guard.evaluate(500, "nvidia-nemotron", projected_job_margin_cents=-1)
        self.assertFalse(d.allowed)
        self.assertEqual(d.rule, "roi")

    def test_first_violation_wins_priority(self) -> None:
        # Both an unlisted vendor AND an over-cap amount: allowlist is checked first.
        d = self.guard.evaluate(9999, "rogue-vendor")
        self.assertEqual(d.rule, "vendor_allowlist")

    def test_as_dict_is_serialisable(self) -> None:
        import json

        d = self.guard.evaluate(100, "rogue-vendor")
        payload = json.dumps(d.as_dict())
        self.assertIn("vendor_allowlist", payload)

    def test_evaluate_and_approve_agree(self) -> None:
        for amount, vendor in [
            (500, "nvidia-nemotron"),
            (9999, "nvidia-nemotron"),
            (100, "nope"),
        ]:
            self.assertEqual(
                self.guard.evaluate(amount, vendor).allowed,
                self.guard.approve(amount, vendor),
            )


if __name__ == "__main__":
    unittest.main()
