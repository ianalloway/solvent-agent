"""Tests for the durable webhook log and its CLI."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock

from solvent.webhook_log import WebhookLog, main


class TestWebhookLog(unittest.TestCase):
    def setUp(self):
        self.log = WebhookLog(db_path=":memory:")

    def test_record_appears_in_list_recent(self):
        self.log.record("evt_001", "payment_intent.created", b'{"id":"evt_001"}', "received")
        rows = self.log.list_recent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "evt_001")
        self.assertEqual(rows[0]["event_type"], "payment_intent.created")
        self.assertEqual(rows[0]["status"], "received")

    def test_list_recent_sorted_by_received_at_desc(self):
        self.log.record("evt_A", "charge.succeeded", b"a", "received")
        time.sleep(0.01)
        self.log.record("evt_B", "charge.failed", b"b", "received")
        rows = self.log.list_recent()
        self.assertEqual([row["event_id"] for row in rows], ["evt_B", "evt_A"])

    def test_list_recent_respects_limit(self):
        for index in range(10):
            self.log.record(f"evt_{index:03}", "ping", b"x", "received")
        self.assertEqual(len(self.log.list_recent(limit=3)), 3)

    def test_received_at_fmt_populated(self):
        self.log.record("evt_fmt", "foo", b"y", "received")
        self.assertTrue(self.log.list_recent()[0]["received_at_fmt"])

    def test_record_is_idempotent(self):
        self.log.record("evt_dup", "ping", b"first", "received")
        self.log.record("evt_dup", "ping", b"second", "processed")
        rows = self.log.list_recent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "processed")

    def test_mark_processed_changes_status_and_clears_error(self):
        self.log.record("evt_p", "charge.captured", b"p", "error", error="boom")
        self.log.mark_processed("evt_p")
        row = self.log.list_recent()[0]
        self.assertEqual(row["status"], "processed")
        self.assertEqual(row["error"], "")

    def test_mark_error_stores_message(self):
        self.log.record("evt_e", "payment_intent.failed", b"e", "received")
        self.log.mark_error("evt_e", "Signature mismatch")
        row = self.log.list_recent()[0]
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error"], "Signature mismatch")

    def test_list_failed_only_returns_errors(self):
        self.log.record("evt_ok", "ping", b"ok", "processed")
        self.log.record("evt_bad", "ping", b"bad", "error", error="oops")
        self.log.record("evt_skip", "ping", b"skip", "skipped")
        rows = self.log.list_failed()
        self.assertEqual([row["event_id"] for row in rows], ["evt_bad"])

    def test_stats_returns_correct_counts(self):
        self.log.record("s1", "t", b"", "processed")
        self.log.record("s2", "t", b"", "processed")
        self.log.record("s3", "t", b"", "error")
        self.log.record("s4", "t", b"", "skipped")
        self.log.record("s5", "t", b"", "received")
        stats = self.log.stats()
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["processed"], 2)
        self.assertEqual(stats["error"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["last_24h"], 5)

    def test_stats_on_empty_db(self):
        self.assertEqual(
            self.log.stats(),
            {"total": 0, "processed": 0, "error": 0, "skipped": 0, "last_24h": 0},
        )

    def test_get_payload(self):
        raw = b'{"id": "evt_raw"}'
        self.log.record("evt_raw", "charge.succeeded", raw, "received")
        self.assertEqual(self.log.get_payload("evt_raw"), raw)
        self.assertIsNone(self.log.get_payload("missing"))

    def test_default_database_honors_solvent_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"SOLVENT_HOME": tmp}, clear=False):
                log = WebhookLog()
                self.assertEqual(Path(log._db_path), Path(tmp).resolve() / "data" / "webhooks.db")


class TestWebhookLogCLI(unittest.TestCase):
    def setUp(self):
        self.log = WebhookLog(db_path=":memory:")

    def _run(self, *args):
        buffer = io.StringIO()
        with mock.patch.object(sys, "argv", ["solvent-webhooks", *args]), redirect_stdout(buffer):
            main(self.log)
        return buffer.getvalue()

    def test_default_command_prints_stats_json(self):
        data = json.loads(self._run())
        self.assertEqual(data["total"], 0)

    def test_list_prints_recent_events(self):
        self.log.record("evt_list", "checkout.session.completed", b"x", "processed")
        output = self._run("list")
        self.assertIn("evt_list", output)
        self.assertIn("processed", output)

    def test_failed_prints_error_events(self):
        self.log.record("evt_failed", "checkout.session.completed", b"x", "error", "boom")
        output = self._run("failed")
        self.assertIn("evt_failed", output)
        self.assertIn("boom", output)

    def test_limit_applies(self):
        for index in range(3):
            self.log.record(f"evt_{index}", "ping", b"x", "processed")
        output = self._run("list", "--limit", "1")
        self.assertEqual(output.count("evt_"), 1)


if __name__ == "__main__":
    unittest.main()
