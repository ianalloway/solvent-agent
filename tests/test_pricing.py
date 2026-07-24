import unittest

from solvent.pricing import RESOURCE_COSTS_CENTS, PricingPolicy, estimate_cost, quote


class TestPricing(unittest.TestCase):
    """Unit tests for the pricing logic and margin gate rules of SOLVENT."""

    def test_estimate_cost_defaults(self) -> None:
        """Test cost estimation with default values when job parameters are missing."""
        job = {}
        total_cost, breakdown = estimate_cost(job)

        # Defaults: 8000 tokens, 2 market data calls, 6 web search calls
        expected_nemotron = round((8000 / 1000) * RESOURCE_COSTS_CENTS["nemotron_tokens_per_1k"])
        expected_market = 2 * RESOURCE_COSTS_CENTS["market_data_call"]
        expected_search = 6 * RESOURCE_COSTS_CENTS["web_search_call"]
        expected_pdf = RESOURCE_COSTS_CENTS["pdf_render"]
        expected_email = RESOURCE_COSTS_CENTS["email_send"]

        self.assertEqual(breakdown["nemotron_inference"], expected_nemotron)
        self.assertEqual(breakdown["market_data"], expected_market)
        self.assertEqual(breakdown["web_search"], expected_search)
        self.assertEqual(breakdown["pdf_render"], expected_pdf)
        self.assertEqual(breakdown["email_send"], expected_email)

        expected_total = expected_nemotron + expected_market + expected_search + expected_pdf + expected_email
        self.assertEqual(total_cost, expected_total)

    def test_estimate_cost_custom(self) -> None:
        """Test cost estimation with custom parameters."""
        job = {
            "est_tokens": 12_500,
            "market_data_calls": 5,
            "web_search_calls": 10
        }
        total_cost, breakdown = estimate_cost(job)

        # Nemotron: 12.5 * 30 = 375 cents
        # Market data: 5 * 120 = 600 cents
        # Web search: 10 * 8 = 80 cents
        # PDF render: 40 cents
        # Email send: 5 cents
        self.assertEqual(breakdown["nemotron_inference"], 375)
        self.assertEqual(breakdown["market_data"], 600)
        self.assertEqual(breakdown["web_search"], 80)
        self.assertEqual(breakdown["pdf_render"], 40)
        self.assertEqual(breakdown["email_send"], 5)
        self.assertEqual(total_cost, 375 + 600 + 80 + 40 + 5)

    def test_quote_below_minimum_price(self) -> None:
        """Test that a quote is declined if the budget is below the policy minimum price."""
        policy = PricingPolicy(min_price_cents=1500)
        job = {
            "budget_cents": 1000,  # $10 budget, less than $15 minimum
            "est_tokens": 2000,
            "market_data_calls": 1,
            "web_search_calls": 2
        }
        q = quote(job, policy)
        self.assertFalse(q.accept)
        self.assertIn("below minimum order size", q.reason)

    def test_quote_below_fulfilment_cost(self) -> None:
        """Test that a quote is declined if the budget is below the estimated cost."""
        policy = PricingPolicy(min_price_cents=500)
        # Cost will be:
        # Nemotron: 8 * 30 = 240
        # Market data: 5 * 120 = 600
        # Web search: 10 * 8 = 80
        # PDF: 40
        # Email: 5
        # Total cost = 965 cents
        job = {
            "budget_cents": 900,  # $9 budget, but cost is 965
            "est_tokens": 8000,
            "market_data_calls": 5,
            "web_search_calls": 10
        }
        q = quote(job, policy)
        self.assertFalse(q.accept)
        self.assertEqual(q.reason, "customer budget below fulfilment cost")

    def test_quote_below_margin_floor(self) -> None:
        """Test that a quote is declined if the margin is below the floor percentage."""
        policy = PricingPolicy(min_price_cents=1000, margin_floor_pct=35.0)
        # Cost is 965 cents.
        # Budget is 1200 cents.
        # Margin is 1200 - 965 = 235 cents.
        # Margin % = 235 / 1200 * 100 = 19.58% -> 19.6%
        # 19.6% is less than 35.0% floor
        job = {
            "budget_cents": 1200,
            "est_tokens": 8000,
            "market_data_calls": 5,
            "web_search_calls": 10
        }
        q = quote(job, policy)
        self.assertFalse(q.accept)
        self.assertIn("below floor", q.reason)

    def test_quote_accepted(self) -> None:
        """Test a successful quote that meets all pricing gate criteria."""
        policy = PricingPolicy(min_price_cents=1500, margin_floor_pct=35.0)
        # Cost is 965 cents.
        # Budget is 2000 cents.
        # Margin is 2000 - 965 = 1035 cents.
        # Margin % = 1035 / 2000 * 100 = 51.75% -> 51.8%
        # 51.8% is >= 35.0% floor, budget 2000 >= min_price 1500, budget >= cost
        job = {
            "budget_cents": 2000,
            "est_tokens": 8000,
            "market_data_calls": 5,
            "web_search_calls": 10
        }
        q = quote(job, policy)
        self.assertTrue(q.accept)
        self.assertEqual(q.reason, "accepted")
        self.assertEqual(q.price_cents, 2000)
        self.assertEqual(q.est_cost_cents, 965)
        self.assertEqual(q.margin_cents, 1035)
        self.assertEqual(q.margin_pct, 51.8)

    def test_quote_zero_budget(self) -> None:
        """Test a job with zero budget."""
        job = {"budget_cents": 0}
        q = quote(job)
        self.assertFalse(q.accept)
        self.assertEqual(q.margin_pct, -100.0)


if __name__ == "__main__":
    unittest.main()
