"""Tests for demo CLI funding amount parsing."""

import argparse
import unittest

from run_demo import SQLITE_MAX_INT, parse_usd_cents, seed_usd_arg


class FundingAmountParsingTests(unittest.TestCase):
    def test_parse_usd_cents_accepts_positive_finite_values(self):
        self.assertEqual(parse_usd_cents("150.00"), 15_000)
        self.assertEqual(parse_usd_cents("0.01"), 1)
        self.assertEqual(parse_usd_cents("92233720368547758.07"), SQLITE_MAX_INT)

    def test_parse_usd_cents_rejects_non_finite_and_oversized_values(self):
        for value in ("nan", "inf", "-inf", "1e309", "1e20"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_usd_cents(value)

    def test_parse_usd_cents_rejects_non_positive_values(self):
        for value in ("0", "-1", "-0.01"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_usd_cents(value)

    def test_seed_usd_arg_raises_argparse_error(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            seed_usd_arg("nan")


if __name__ == "__main__":
    unittest.main()
