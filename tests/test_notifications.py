"""Tests for cross-process chat notification outbox."""

import os
import tempfile
import unittest

from solvent.notifications import drain_chat_outbox, enqueue_chat


class TestNotifications(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = os.environ.get("SOLVENT_HOME")
        os.environ["SOLVENT_HOME"] = self._tmp
        # Guarantee a clean state regardless of prior test pollution
        drain_chat_outbox()

    def tearDown(self):
        drain_chat_outbox()
        if self._orig is None:
            os.environ.pop("SOLVENT_HOME", None)
        else:
            os.environ["SOLVENT_HOME"] = self._orig

    def test_enqueue_then_drain_roundtrip(self):
        enqueue_chat("telegram", "123", "hello")
        rows = drain_chat_outbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["channel"], "telegram")
        self.assertEqual(rows[0]["external_id"], "123")
        self.assertEqual(rows[0]["text"], "hello")
        self.assertIn("ts", rows[0])

    def test_drain_clears_outbox(self):
        enqueue_chat("tg", "x", "msg1")
        first = drain_chat_outbox()
        self.assertEqual(len(first), 1)
        self.assertEqual(drain_chat_outbox(), [])

    def test_drain_skips_malformed_lines(self):
        outbox_path = (
            __import__("solvent.paths", fromlist=["data_dir"]).data_dir()
            / "chat_outbox.jsonl"
        )
        outbox_path.parent.mkdir(parents=True, exist_ok=True)
        outbox_path.write_text("not-json-here\n", encoding="utf-8")
        self.assertEqual(drain_chat_outbox(), [])

    def test_drain_on_missing_outbox(self):
        self.assertEqual(drain_chat_outbox(), [])


if __name__ == "__main__":
    unittest.main()
