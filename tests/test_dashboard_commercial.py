"""Tests for the dashboard's commercial panel (win rate, customers, quality, capacity)."""

from __future__ import annotations

from unittest import mock

import pytest

from solvent.dashboard import _commercial_html, _metric_row, _pct_txt
from solvent.treasury import Treasury


@pytest.fixture
def treasury(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.seed(100_000)
    return t


def _won(treasury, job_id, *, email, budget=4_900, quality=100.0):
    treasury.upsert_job(
        job_id,
        "completed",
        topic=f"topic {job_id}",
        budget_cents=budget,
        customer_email=email,
    )
    treasury.upsert_checkout(job_id, f"cs_{job_id}", f"https://pay/{job_id}", "paid")
    treasury.earn(budget, f"paid {job_id}", job_id=job_id, stripe_ref=f"pi_{job_id}")
    treasury.spend(500, "inference", job_id=job_id, vendor="nvidia-nemotron")
    treasury.upsert_metrics(job_id, quality_score=quality, quality_grade="pass", quality_flags="")


def test_an_empty_treasury_says_so(treasury):
    assert "No orders yet" in _commercial_html(treasury)


def test_the_panel_reports_win_rate_customers_quality_and_capacity(treasury):
    _won(treasury, "J1", email="a@x.example")
    treasury.upsert_job("J2", "failed", topic="t", budget_cents=600, error_reason="below floor")

    html = _commercial_html(treasury)
    assert "Orders won" in html
    assert "Gate acceptance" in html
    assert "Customers" in html
    assert "Brief quality" in html
    assert "Throughput ceiling" in html
    assert "Left on the table" in html


def test_an_untrusted_customer_email_is_escaped(treasury):
    _won(treasury, "J1", email="<img src=x onerror=alert(1)>@x.example")
    html = _commercial_html(treasury)
    assert "<img" not in html
    assert "&lt;img" in html


def test_a_failing_read_never_breaks_the_render(treasury):
    with mock.patch("solvent.funnel.analyse", side_effect=RuntimeError("db gone")):
        assert _commercial_html(treasury) == ""


def test_quality_is_flagged_when_a_brief_fell_short(treasury):
    _won(treasury, "J1", email="a@x.example")
    treasury.upsert_metrics("J1", quality_score=40.0, quality_grade="fail", quality_flags="length")
    assert "red-txt" in _commercial_html(treasury)


def test_a_clean_quality_record_is_not_flagged_red(treasury):
    _won(treasury, "J1", email="a@x.example", quality=100.0)
    html = _commercial_html(treasury)
    quality_line = next(line for line in html.split("vendor-metric") if "Brief quality" in line)
    assert "green-txt" in quality_line


def test_metric_row_escapes_its_label():
    assert "&lt;b&gt;" in _metric_row("<b>label</b>", "safe")


def test_missing_percentages_render_as_a_dash():
    assert _pct_txt(None) == "—"
    assert _pct_txt(42.0) == "42.0%"


def test_the_panel_is_rendered_into_the_dashboard(tmp_path, monkeypatch):
    from solvent import dashboard

    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    snapshot = {
        "balance_cents": 10_000,
        "capital_cents": 10_000,
        "revenue_cents": 0,
        "expense_cents": 0,
        "net_profit_cents": 0,
        "margin_pct": 0.0,
        "entries": [],
    }
    path = dashboard.render(snapshot, [])
    html = path.read_text(encoding="utf-8")
    assert 'id="commercial-panel"' in html
    assert "win rate · customers · quality · capacity" in html
