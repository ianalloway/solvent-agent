"""Tests for delivery tokens and outbox email."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solvent.delivery import (
    hosted_brief_url,
    make_delivery_token,
    send_brief_email,
    verify_delivery_token,
)


class TestDelivery(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"SOLVENT_DELIVERY_SECRET": "x" * 32}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_token_roundtrip(self):
        token = make_delivery_token("J1")
        self.assertTrue(verify_delivery_token("J1", token))
        self.assertFalse(verify_delivery_token("J2", token))

    def test_rejects_missing_or_placeholder_secret(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                make_delivery_token("J1")
            self.assertFalse(verify_delivery_token("J1", "123.bad"))

        with mock.patch.dict(os.environ, {"SOLVENT_DELIVERY_SECRET": "change-me-in-production"}, clear=True):
            with self.assertRaises(RuntimeError):
                make_delivery_token("J1")
            self.assertFalse(verify_delivery_token("J1", "123.bad"))

    def test_rejects_unsafe_job_id(self):
        with self.assertRaises(ValueError):
            make_delivery_token("../J1")
        self.assertFalse(verify_delivery_token("../J1", "123.bad"))

    def test_hosted_url(self):
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
