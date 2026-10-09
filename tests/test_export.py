"""Tests for the books export and period close (solvent.export)."""

from __future__ import annotations

import csv
import json
import time

import pytest

from solvent.export import (
    JOB_COLUMNS,
    LEDGER_COLUMNS,
    collect,
    format_summary,
    ledger_rows,
    neutralise_cell,
    parse_period,
    period_summary,
    write_csv,
    write_json,
)
from solvent.treasury import REFUND_VENDOR, Treasury

DAY = 86_400


@pytest.fixture
def treasury(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.seed(10_000)
    t.upsert_job(
        "J1", "completed", topic="a brief", budget_cents=4_900, customer_email="a@x.example"
    )
    t.earn(4_900, "Payment for J1", job_id="J1", stripe_ref="pi_1")
    t.spend(600, "inference", job_id="J1", vendor="nvidia-nemotron")
    t.upsert_metrics("J1", est_cost_cents=700, actual_cost_cents=600)
    return t


# --- period parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    "value,seconds",
    [("24h", 86_400), ("7d", 7 * 86_400), ("2w", 2 * 604_800), ("3m", 3 * 2_592_000)],
)
def test_relative_periods(value, seconds):
    now = 1_000_000_000.0
    assert parse_period(value, now=now) == pytest.approx(now - seconds)


def test_absolute_date_period():
    assert parse_period("2026-01-31") == pytest.approx(
        time.mktime(time.strptime("2026-01-31", "%Y-%m-%d"))
    )


def test_no_period_means_no_bound():
    assert parse_period(None) is None
    assert parse_period("") is None


def test_an_unparseable_period_is_an_error():
    with pytest.raises(ValueError, match="unrecognised period"):
        parse_period("last tuesday")


# --- windowing --------------------------------------------------------------


def test_ledger_rows_respect_the_window(treasury):
    now = time.time()
    assert len(ledger_rows(treasury.entries, since=now - DAY)) == 3
    assert ledger_rows(treasury.entries, since=now + DAY) == []
    assert ledger_rows(treasury.entries, until=now - DAY) == []


def test_ledger_rows_carry_every_column(treasury):
    row = ledger_rows(treasury.entries)[0]
    assert set(row) == set(LEDGER_COLUMNS)
    assert row["iso_time"]


def test_signed_cents_carries_direction(treasury):
    rows = {r["kind"]: r for r in ledger_rows(treasury.entries)}
    assert rows["revenue"]["signed_cents"] > 0
    assert rows["expense"]["signed_cents"] < 0


# --- the close --------------------------------------------------------------


def test_period_summary_ties_out(treasury):
    summary = period_summary(ledger_rows(treasury.entries))
    assert summary["revenue_cents"] == 4_900
    assert summary["cogs_cents"] == 600
    assert summary["net_cents"] == 4_300
    assert summary["capital_in_cents"] == 10_000
    assert summary["margin_pct"] == pytest.approx(87.8, abs=0.1)


def test_refunds_are_separated_from_cost_of_sales(treasury):
    treasury.spend(1_000, "refund for J1", job_id="J1", vendor=REFUND_VENDOR)
    summary = period_summary(ledger_rows(treasury.entries))
    assert summary["refunds_cents"] == 1_000
    assert summary["cogs_cents"] == 600  # unchanged
    assert summary["net_cents"] == 4_900 - 1_000 - 600


def test_summary_of_an_empty_period():
    summary = period_summary([])
    assert summary["entries"] == 0
    assert summary["net_cents"] == 0
    assert summary["margin_pct"] == 0.0
    assert summary["from"] == ""


def test_expense_is_broken_down_by_vendor(treasury):
    treasury.spend(40, "pdf", job_id="J1", vendor="pdf-render-saas")
    summary = period_summary(ledger_rows(treasury.entries))
    assert summary["expense_by_vendor"]["nvidia-nemotron"] == 600
    assert summary["expense_by_vendor"]["pdf-render-saas"] == 40


# --- collection and writing -------------------------------------------------


def test_collect_gathers_every_table(treasury):
    data = collect(treasury)
    assert {"summary", "book", "ledger", "jobs", "metrics", "customers"} <= set(data)
    assert [j["id"] for j in data["jobs"]] == ["J1"]
    assert set(data["jobs"][0]) == set(JOB_COLUMNS)
    assert [m["job_id"] for m in data["metrics"]] == ["J1"]
    assert [c["email"] for c in data["customers"]] == ["a@x.example"]


def test_collect_keeps_a_job_whose_money_moved_in_the_window(treasury):
    """An older job still belongs in the close if it was paid inside it."""
    with treasury._conn() as conn, conn:
        conn.execute("UPDATE jobs SET created_at = ? WHERE id = 'J1'", (time.time() - 30 * DAY,))
    data = collect(treasury, since=time.time() - DAY)
    assert [j["id"] for j in data["jobs"]] == ["J1"]


def test_write_csv_writes_one_file_per_table(treasury, tmp_path):
    written = write_csv(collect(treasury), tmp_path / "books")
    assert {p.name for p in written} == {
        "ledger.csv",
        "jobs.csv",
        "metrics.csv",
        "customers.csv",
    }
    with (tmp_path / "books" / "ledger.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3
    assert rows[0]["kind"] == "capital"


def test_write_csv_is_readable_when_a_table_is_empty(tmp_path):
    empty = Treasury(path=tmp_path / "empty.db")
    write_csv(collect(empty), tmp_path / "books")
    with (tmp_path / "books" / "jobs.csv").open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert list(reader) == []
        assert reader.fieldnames == list(JOB_COLUMNS)


def test_write_json_round_trips(treasury, tmp_path):
    path = write_json(collect(treasury), tmp_path / "out" / "export.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["summary"]["revenue_cents"] == 4_900
    assert len(data["ledger"]) == 3


def test_format_summary_reports_the_close_and_the_files(treasury, tmp_path):
    data = collect(treasury)
    written = write_csv(data, tmp_path / "books")
    rendered = format_summary(data, written)
    assert "PERIOD CLOSE" in rendered
    assert "$49.00" in rendered
    assert "ledger.csv" in rendered


@pytest.mark.parametrize(
    "raw",
    [
        "=HYPERLINK(\"http://evil.example\",\"x\")",
        "+1+cmd|' /C calc'!A0",
        "-2+3",
        "@SUM(A1:A9)",
        "\t=1+1",
        "\r=1+1",
        "   =1+1",
        "\x00\u200b=1+1",
    ],
)
def test_formula_like_text_is_neutralised(raw):
    assert neutralise_cell(raw) == "'" + raw


@pytest.mark.parametrize("value", ["a brief", "a@x.example", "", None, 4_900, -600, 12.5])
def test_ordinary_values_pass_through(value):
    assert neutralise_cell(value) == value


def test_write_csv_neutralises_untrusted_text_but_keeps_numbers(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.seed(10_000)
    t.upsert_job(
        "J1",
        "completed",
        topic='=HYPERLINK("http://evil.example","open")',
        budget_cents=4_900,
        customer_email="a@x.example",
    )
    t.spend(600, "@SUM(1+1)", job_id="J1", vendor="-vendor")
    write_csv(collect(t), tmp_path / "books")

    with (tmp_path / "books" / "jobs.csv").open(encoding="utf-8") as fh:
        job = next(csv.DictReader(fh))
    assert job["topic"] == '\'=HYPERLINK("http://evil.example","open")'
    assert job["budget_cents"] == "4900"

    with (tmp_path / "books" / "ledger.csv").open(encoding="utf-8") as fh:
        expense = next(r for r in csv.DictReader(fh) if r["kind"] == "expense")
    assert expense["memo"] == "'@SUM(1+1)"
    assert expense["vendor"] == "'-vendor"
    assert expense["signed_cents"] == "-600"
