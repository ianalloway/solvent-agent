"""Tests for delivery tokens and outbox email."""

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from solvent.delivery import (
    make_delivery_token,
    verify_delivery_token,
    send_brief_email,
    hosted_brief_url,
    is_safe_job_id,
)


class TestDelivery(unittest.TestCase):
    def test_token_roundtrip(self):
        with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}):
            token = make_delivery_token("J1")
            self.assertTrue(verify_delivery_token("J1", token))
            self.assertFalse(verify_delivery_token("J2", token))

    def test_unconfigured_or_insecure_secret_fails_closed(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(verify_delivery_token("J1", "1.bad"))
            with self.assertRaises(RuntimeError):
                make_delivery_token("J1", ts=1)

        for secret in ("short", "solvent-dev-secret-change-me", "change-me-in-production"):
            with self.subTest(secret=secret):
                with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": secret}):
                    self.assertFalse(verify_delivery_token("J1", "1.bad"))
                    with self.assertRaises(RuntimeError):
                        make_delivery_token("J1", ts=1)

    def test_job_id_must_be_safe_for_hosted_delivery(self):
        self.assertTrue(is_safe_job_id("J1.ok-123"))
        for job_id in ("", "../J1", "/J1", "J1/other", "J" * 129):
            with self.subTest(job_id=job_id):
                self.assertFalse(is_safe_job_id(job_id))
                self.assertFalse(verify_delivery_token(job_id, "1.bad"))

    def test_hosted_url(self):
        with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 32}):
            url = hosted_brief_url("http://localhost:8787", "J1")
        self.assertIn("/briefs/J1", url)
        self.assertIn("token=", url)

    def test_outbox_email_without_smtp(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / "J1.md"
            brief.write_text("# Test brief")
            result = send_brief_email("user@test.com", "J1", brief, "http://localhost/briefs/J1")
            self.assertTrue(result["simulated"])
            self.assertTrue(Path(result["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
