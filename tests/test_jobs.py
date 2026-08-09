"""Tests for the sample job fixtures used by the demo pipeline."""

import unittest

from solvent.jobs import SAMPLE_JOBS
from solvent.pricing import PricingPolicy, quote

REQUIRED_KEYS = {
    "id",
    "topic",
    "context",
    "customer_email",
    "budget_cents",
    "est_tokens",
    "market_data_calls",
    "web_search_calls",
}

INT_KEYS = ("budget_cents", "est_tokens", "market_data_calls", "web_search_calls")
TEXT_KEYS = ("id", "topic", "context", "customer_email")


class TestSampleJobs(unittest.TestCase):
    """Guard the shape and the demo contract of SAMPLE_JOBS."""

    def test_sample_jobs_is_non_empty_list(self) -> None:
        self.assertIsInstance(SAMPLE_JOBS, list)
        self.assertTrue(SAMPLE_JOBS, "SAMPLE_JOBS must not be empty")

    def test_every_job_has_required_keys(self) -> None:
        for job in SAMPLE_JOBS:
            with self.subTest(job=job.get("id")):
                self.assertEqual(REQUIRED_KEYS, set(job))

    def test_job_ids_are_unique(self) -> None:
        ids = [job["id"] for job in SAMPLE_JOBS]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate job ids in {ids}")

    def test_text_fields_are_non_empty_strings(self) -> None:
        for job in SAMPLE_JOBS:
            for key in TEXT_KEYS:
                with self.subTest(job=job["id"], key=key):
                    self.assertIsInstance(job[key], str)
                    self.assertTrue(job[key].strip())

    def test_numeric_fields_are_positive_ints(self) -> None:
        for job in SAMPLE_JOBS:
            for key in INT_KEYS:
                with self.subTest(job=job["id"], key=key):
                    value = job[key]
                    self.assertIsInstance(value, int)
                    self.assertNotIsInstance(value, bool)
                    self.assertGreater(value, 0)

    def test_customer_emails_use_reserved_example_domain(self) -> None:
        """Fixtures must never carry a routable address."""
        for job in SAMPLE_JOBS:
            with self.subTest(job=job["id"]):
                local, sep, domain = job["customer_email"].partition("@")
                self.assertTrue(local, "email is missing a local part")
                self.assertEqual(sep, "@")
                self.assertTrue(
                    domain.endswith(".example"),
                    f"{job['customer_email']} is not on the reserved .example domain",
                )

    def test_margin_gate_declines_only_the_unprofitable_job(self) -> None:
        """The module docstring promises J3 is the single declined demo job."""
        decisions = {job["id"]: quote(job, PricingPolicy()) for job in SAMPLE_JOBS}
        declined = sorted(jid for jid, q in decisions.items() if not q.accept)
        self.assertEqual(["J3"], declined)

    def test_accepted_jobs_clear_the_margin_floor(self) -> None:
        policy = PricingPolicy()
        for job in SAMPLE_JOBS:
            result = quote(job, policy)
            if result.accept:
                with self.subTest(job=job["id"]):
                    self.assertGreaterEqual(result.margin_pct, policy.margin_floor_pct)
                    self.assertEqual(result.price_cents, job["budget_cents"])
                    self.assertGreater(result.margin_cents, 0)


if __name__ == "__main__":
    unittest.main()
