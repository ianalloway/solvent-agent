"""Tests for solvent.webhook_log — in-memory SQLite."""

from __future__ import annotations

import time
import unittest

from solvent.webhook_log import WebhookLog


class TestWebhookLog(unittest.TestCase):
    def setUp(self):
        self.wl = WebhookLog(db_path=":memory:")

    def test_record_appears_in_list_recent(self):
        self.wl.record("evt_001", "payment_intent.created", b'{"id":"evt_001"}', "received")
        rows = self.wl.list_recent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "evt_001")
        self.assertEqual(rows[0]["event_type"], "payment_intent.created")
        self.assertEqual(rows[0]["status"], "received")

    def test_list_recent_sorted_by_received_at_desc(self):
        self.wl.record("evt_A", "charge.succeeded", b"a", "received")
        time.sleep(0.01)
        self.wl.record("evt_B", "charge.failed", b"b", "received")
        rows = self.wl.list_recent()
        self.assertEqual(rows[0]["event_id"], "evt_B")
        self.assertEqual(rows[1]["event_id"], "evt_A")

    def test_list_recent_respects_limit(self):
        for i in range(10):
            self.wl.record(f"evt_{i:03}", "ping", b"x", "received")
        rows = self.wl.list_recent(limit=3)
        self.assertEqual(len(rows), 3)

    def test_received_at_fmt_populated(self):
        self.wl.record("evt_fmt", "foo", b"y", "received")
        rows = self.wl.list_recent()
        self.assertNotEqual(rows[0]["received_at_fmt"], "")

    def test_record_never_overwrites_existing_event_id(self):
        self.assertTrue(self.wl.record("evt_dup", "ping", b"first", "received", verified=True))
        self.assertFalse(self.wl.record("evt_dup", "ping", b"second", "processed"))
        rows = self.wl.list_recent()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "received")
        self.assertEqual(self.wl.get_payload("evt_dup"), b"first")
        self.assertTrue(self.wl.is_verified("evt_dup"))

    def test_record_defaults_to_unverified(self):
        self.wl.record("evt_u", "ping", b"u", "received")
        self.assertFalse(self.wl.is_verified("evt_u"))
        self.assertFalse(self.wl.is_verified("evt_missing"))

    def test_list_public_omits_payload(self):
        self.wl.record("evt_pub", "ping", b'{"email":"a@b.com"}', "received", verified=True)
        rows = self.wl.list_public()
        self.assertEqual(rows[0]["event_id"], "evt_pub")
        self.assertNotIn("payload", rows[0])

    def test_legacy_database_rows_are_not_verified(self):
        import sqlite3
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE webhook_events (event_id TEXT PRIMARY KEY, "
                "event_type TEXT NOT NULL DEFAULT '', payload BLOB NOT NULL DEFAULT (x''), "
                "received_at REAL NOT NULL DEFAULT 0.0, status TEXT NOT NULL DEFAULT 'received', "
                "error TEXT NOT NULL DEFAULT '')"
            )
            conn.execute("INSERT INTO webhook_events (event_id, payload) VALUES ('evt_old', x'7b7d')")
            conn.commit()
            conn.close()
            wl = WebhookLog(db_path=db)
            self.assertEqual(wl.get_payload("evt_old"), b"{}")
            self.assertFalse(wl.is_verified("evt_old"))

    def test_mark_skipped(self):
        self.wl.record("evt_s", "ping", b"s", "received", verified=True)
        self.wl.mark_skipped("evt_s")
        self.assertEqual(self.wl.get_status("evt_s"), "skipped")

    def test_mark_processed_changes_status(self):
        self.wl.record("evt_p", "charge.captured", b"p", "received")
        self.wl.mark_processed("evt_p")
        rows = self.wl.list_recent()
        self.assertEqual(rows[0]["status"], "processed")

    def test_mark_processed_clears_error(self):
        self.wl.record("evt_p2", "charge.captured", b"p2", "error", error="boom")
        self.wl.mark_processed("evt_p2")
        rows = self.wl.list_recent()
        self.assertEqual(rows[0]["error"], "")

    def test_mark_error_stores_message(self):
        self.wl.record("evt_e", "payment_intent.failed", b"e", "received")
        self.wl.mark_error("evt_e", "Signature mismatch")
        rows = self.wl.list_recent()
        self.assertEqual(rows[0]["status"], "error")
        self.assertEqual(rows[0]["error"], "Signature mismatch")

    def test_mark_error_overrides_previous_error(self):
        self.wl.record("evt_e2", "payment_intent.failed", b"e2", "error", error="first")
        self.wl.mark_error("evt_e2", "second error")
        rows = self.wl.list_recent()
        self.assertEqual(rows[0]["error"], "second error")

    def test_list_failed_only_returns_error_rows(self):
        self.wl.record("evt_ok", "ping", b"ok", "processed")
        self.wl.record("evt_bad", "ping", b"bad", "error", error="oops")
        self.wl.record("evt_skip", "ping", b"skip", "skipped")
        rows = self.wl.list_failed()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], "evt_bad")

    def test_list_failed_empty_when_no_errors(self):
        self.wl.record("evt_x", "ping", b"x", "processed")
        self.assertEqual(self.wl.list_failed(), [])

    def test_stats_returns_correct_counts(self):
        self.wl.record("s1", "t", b"", "processed")
        self.wl.record("s2", "t", b"", "processed")
        self.wl.record("s3", "t", b"", "error")
        self.wl.record("s4", "t", b"", "skipped")
        self.wl.record("s5", "t", b"", "received")
        s = self.wl.stats()
        self.assertEqual(s["total"], 5)
        self.assertEqual(s["processed"], 2)
        self.assertEqual(s["error"], 1)
        self.assertEqual(s["skipped"], 1)

    def test_stats_on_empty_db(self):
        s = self.wl.stats()
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["processed"], 0)
        self.assertEqual(s["error"], 0)
        self.assertEqual(s["skipped"], 0)
        self.assertEqual(s["last_24h"], 0)

    def test_stats_last_24h_counts_recent(self):
        self.wl.record("r1", "t", b"", "received")
        self.wl.record("r2", "t", b"", "processed")
        s = self.wl.stats()
        self.assertEqual(s["last_24h"], 2)

    def test_get_payload_returns_stored_bytes(self):
        raw = b'{"id": "evt_raw", "type": "charge.succeeded"}'
        self.wl.record("evt_raw", "charge.succeeded", raw, "received")
        self.assertEqual(self.wl.get_payload("evt_raw"), raw)

    def test_get_payload_returns_none_when_missing(self):
        self.assertIsNone(self.wl.get_payload("evt_nonexistent"))

    def test_get_payload_returns_bytes_type(self):
        self.wl.record("evt_bytes", "t", b"hello", "received")
        result = self.wl.get_payload("evt_bytes")
        self.assertIsInstance(result, bytes)


if __name__ == "__main__":
    unittest.main()
