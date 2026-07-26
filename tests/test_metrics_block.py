"""Tests for guardrail block reason persistence in job_metrics (+ migration)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from solvent.treasury import Treasury


class TestBlockMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_block_reason_round_trips(self):
        t = Treasury(path=self.db)
        t.upsert_metrics("J1", est_cost_cents=100)
        t.upsert_metrics(
            "J1",
            block_rule="min_reserve",
            block_reason="would breach minimum cash reserve",
        )
        m = t.get_metrics("J1")
        self.assertEqual(m["block_rule"], "min_reserve")
        self.assertEqual(m["block_reason"], "would breach minimum cash reserve")
        # the earlier metric is preserved by the upsert
        self.assertEqual(m["est_cost_cents"], 100)

    def test_migration_adds_columns_to_legacy_db(self):
        # Hand-build a legacy job_metrics table without the block_* columns.
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE job_metrics (job_id TEXT PRIMARY KEY, "
            "est_cost_cents INTEGER, ts REAL NOT NULL)"
        )
        conn.execute("INSERT INTO job_metrics (job_id, ts) VALUES ('OLD', 0)")
        conn.commit()
        conn.close()

        # Opening the Treasury should migrate the schema in place.
        t = Treasury(path=self.db)
        cols = {
            r[1]
            for r in sqlite3.connect(self.db).execute("PRAGMA table_info(job_metrics)")
        }
        self.assertIn("block_rule", cols)
        self.assertIn("block_reason", cols)

        # And the new columns are usable on the pre-existing row.
        t.upsert_metrics("OLD", block_rule="vendor_allowlist", block_reason="nope")
        self.assertEqual(t.get_metrics("OLD")["block_rule"], "vendor_allowlist")


if __name__ == "__main__":
    unittest.main()
