"""Tests for delivery tokens and outbox email."""

import tempfile
import unittest
from pathlib import Path

from solvent.delivery import (
    make_delivery_token,
    verify_delivery_token,
    send_brief_email,
    hosted_brief_url,
)


class TestDelivery(unittest.TestCase):
    def test_token_roundtrip(self):
        token = make_delivery_token("J1")
        self.assertTrue(verify_delivery_token("J1", token))
        self.assertFalse(verify_delivery_token("J2", token))

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
