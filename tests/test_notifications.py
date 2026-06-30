"""Tests for cross-process chat outbox."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solvent import notifications as n


class TestChatOutbox(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        base = Path(self._tmpdir.name)
        self.outbox = base / "chat_outbox.jsonl"
        self.lock = base / "chat_outbox.lock"
        self._patch_outbox = mock.patch.object(n, "_OUTBOX", self.outbox)
        self._patch_lock = mock.patch.object(n, "_LOCK", self.lock)
        self._patch_outbox.start()
        self._patch_lock.start()

    def tearDown(self):
        self._patch_lock.stop()
        self._patch_outbox.stop()
        self._tmpdir.cleanup()

    def test_enqueue_and_drain(self):
        n.enqueue_chat("dashboard", "sess-1", "Job J1 fulfilled.")
        n.enqueue_chat("telegram", "12345", "Payment received.")
        rows = n.drain_chat_outbox()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["channel"], "dashboard")
        self.assertEqual(rows[0]["external_id"], "sess-1")
        self.assertEqual(rows[1]["text"], "Payment received.")
        self.assertEqual(n.drain_chat_outbox(), [])

    def test_drain_empty(self):
        self.assertEqual(n.drain_chat_outbox(), [])

    def test_skips_bad_lines(self):
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        self.outbox.write_text('not json\n{"channel":"c","external_id":"1","text":"ok","ts":1}\n', encoding="utf-8")
        rows = n.drain_chat_outbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "ok")
