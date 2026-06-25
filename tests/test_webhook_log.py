"""Tests for solvent.webhook_log — all using an in-memory SQLite database."""

from __future__ import annotations

import time

import pytest

from solvent.webhook_log import WebhookLog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def wl() -> WebhookLog:
    """Return a fresh in-memory WebhookLog for each test."""
    return WebhookLog(db_path=":memory:")


# ---------------------------------------------------------------------------
# record() + list_recent()
# ---------------------------------------------------------------------------

class TestRecord:
    def test_record_appears_in_list_recent(self, wl: WebhookLog):
        wl.record("evt_001", "payment_intent.created", b'{"id":"evt_001"}', "received")
        rows = wl.list_recent()
        assert len(rows) == 1
        assert rows[0]["event_id"] == "evt_001"
        assert rows[0]["event_type"] == "payment_intent.created"
        assert rows[0]["status"] == "received"

    def test_list_recent_sorted_by_received_at_desc(self, wl: WebhookLog):
        wl.record("evt_A", "charge.succeeded", b"a", "received")
        time.sleep(0.01)
        wl.record("evt_B", "charge.failed", b"b", "received")
        rows = wl.list_recent()
        assert rows[0]["event_id"] == "evt_B"
        assert rows[1]["event_id"] == "evt_A"

    def test_list_recent_respects_limit(self, wl: WebhookLog):
        for i in range(10):
            wl.record(f"evt_{i:03}", "ping", b"x", "received")
        rows = wl.list_recent(limit=3)
        assert len(rows) == 3

    def test_received_at_fmt_populated(self, wl: WebhookLog):
        wl.record("evt_fmt", "foo", b"y", "received")
        rows = wl.list_recent()
        assert rows[0]["received_at_fmt"] != ""

    def test_record_idempotent_via_replace(self, wl: WebhookLog):
        wl.record("evt_dup", "ping", b"first", "received")
        wl.record("evt_dup", "ping", b"second", "processed")
        rows = wl.list_recent()
        assert len(rows) == 1
        assert rows[0]["status"] == "processed"


# ---------------------------------------------------------------------------
# mark_processed()
# ---------------------------------------------------------------------------

class TestMarkProcessed:
    def test_mark_processed_changes_status(self, wl: WebhookLog):
        wl.record("evt_p", "charge.captured", b"p", "received")
        wl.mark_processed("evt_p")
        rows = wl.list_recent()
        assert rows[0]["status"] == "processed"

    def test_mark_processed_clears_error(self, wl: WebhookLog):
        wl.record("evt_p2", "charge.captured", b"p2", "error", error="boom")
        wl.mark_processed("evt_p2")
        rows = wl.list_recent()
        assert rows[0]["error"] == ""


# ---------------------------------------------------------------------------
# mark_error()
# ---------------------------------------------------------------------------

class TestMarkError:
    def test_mark_error_stores_message(self, wl: WebhookLog):
        wl.record("evt_e", "payment_intent.failed", b"e", "received")
        wl.mark_error("evt_e", "Signature mismatch")
        rows = wl.list_recent()
        assert rows[0]["status"] == "error"
        assert rows[0]["error"] == "Signature mismatch"

    def test_mark_error_overrides_previous_error(self, wl: WebhookLog):
        wl.record("evt_e2", "payment_intent.failed", b"e2", "error", error="first")
        wl.mark_error("evt_e2", "second error")
        rows = wl.list_recent()
        assert rows[0]["error"] == "second error"


# ---------------------------------------------------------------------------
# list_failed()
# ---------------------------------------------------------------------------

class TestListFailed:
    def test_list_failed_only_returns_error_rows(self, wl: WebhookLog):
        wl.record("evt_ok", "ping", b"ok", "processed")
        wl.record("evt_bad", "ping", b"bad", "error", error="oops")
        wl.record("evt_skip", "ping", b"skip", "skipped")
        rows = wl.list_failed()
        assert len(rows) == 1
        assert rows[0]["event_id"] == "evt_bad"

    def test_list_failed_empty_when_no_errors(self, wl: WebhookLog):
        wl.record("evt_x", "ping", b"x", "processed")
        assert wl.list_failed() == []


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_returns_correct_counts(self, wl: WebhookLog):
        wl.record("s1", "t", b"", "processed")
        wl.record("s2", "t", b"", "processed")
        wl.record("s3", "t", b"", "error")
        wl.record("s4", "t", b"", "skipped")
        wl.record("s5", "t", b"", "received")

        s = wl.stats()
        assert s["total"] == 5
        assert s["processed"] == 2
        assert s["error"] == 1
        assert s["skipped"] == 1

    def test_stats_on_empty_db(self, wl: WebhookLog):
        s = wl.stats()
        assert s["total"] == 0
        assert s["processed"] == 0
        assert s["error"] == 0
        assert s["skipped"] == 0
        assert s["last_24h"] == 0

    def test_stats_last_24h_counts_recent(self, wl: WebhookLog):
        wl.record("r1", "t", b"", "received")
        wl.record("r2", "t", b"", "processed")
        s = wl.stats()
        assert s["last_24h"] == 2


# ---------------------------------------------------------------------------
# get_payload()
# ---------------------------------------------------------------------------

class TestGetPayload:
    def test_get_payload_returns_stored_bytes(self, wl: WebhookLog):
        raw = b'{"id": "evt_raw", "type": "charge.succeeded"}'
        wl.record("evt_raw", "charge.succeeded", raw, "received")
        assert wl.get_payload("evt_raw") == raw

    def test_get_payload_returns_none_when_missing(self, wl: WebhookLog):
        assert wl.get_payload("evt_nonexistent") is None

    def test_get_payload_returns_bytes_type(self, wl: WebhookLog):
        wl.record("evt_bytes", "t", b"hello", "received")
        result = wl.get_payload("evt_bytes")
        assert isinstance(result, bytes)
