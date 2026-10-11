"""Tests for the order funnel (solvent.funnel)."""

from __future__ import annotations

import json

import pytest

from solvent.funnel import analyse, classify, format_funnel, parse_since
from solvent.treasury import Treasury


@pytest.fixture
def treasury(tmp_path):
    return Treasury(path=tmp_path / "ledger.db")


def _job(treasury, job_id, status, *, budget=4_900, reason="", product=None, paid=False,
         checkout=False, refund=False):
    payload = {"id": job_id, "topic": f"topic {job_id}", "budget_cents": budget}
    if product:
        payload["product"] = product
    treasury.upsert_job(
        job_id,
        status,
        topic=payload["topic"],
        budget_cents=budget,
        customer_email=f"{job_id}@x.example",
        error_reason=reason,
        job_payload_json=payload,
    )
    if checkout:
        treasury.upsert_checkout(job_id, f"cs_{job_id}", f"https://pay/{job_id}", "open")
    if paid:
        treasury.earn(budget, f"paid {job_id}", job_id=job_id, stripe_ref=f"pi_{job_id}")
    if refund:
        from solvent.treasury import REFUND_VENDOR

        treasury.spend(budget, "refund", job_id=job_id, vendor=REFUND_VENDOR)


# --- period parsing ---------------------------------------------------------


@pytest.mark.parametrize("value,seconds", [("24h", 86_400), ("7d", 604_800), ("2w", 1_209_600)])
def test_relative_windows(value, seconds):
    now = 1_000_000_000.0
    assert parse_since(value, now=now) == pytest.approx(now - seconds)


def test_no_window_means_all_time():
    assert parse_since(None) is None


def test_a_bad_window_is_an_error():
    with pytest.raises(ValueError, match="unrecognised period"):
        parse_since("since tuesday")


# --- classification ---------------------------------------------------------


def test_the_ledger_decides_whether_a_job_was_paid():
    """A failed-but-paid job is a refund, which is a different loss entirely."""
    job = {"status": "failed", "error_reason": "guardrail blocked spend"}
    assert classify(job, paid=True, had_checkout=True) == "refunded"
    assert classify(job, paid=False, had_checkout=True) == "declined"


@pytest.mark.parametrize(
    "status,reason,expected",
    [
        ("completed", "", "delivered"),
        ("expired", "checkout unpaid after 48h", "abandoned"),
        ("cancelled", "rejected by operator", "cancelled"),
        ("failed", "intake: duplicate — x", "screened"),
        ("failed", "security violation: bad email", "rejected"),
        ("failed", "unknown product 'platinum'", "rejected"),
        ("failed", "projected margin 2% below floor 35%", "declined"),
        ("awaiting_payment", "", "awaiting_payment"),
        ("pending_quote", "", "open"),
    ],
)
def test_each_exit_from_the_funnel_is_named(status, reason, expected):
    assert classify({"status": status, "error_reason": reason}, paid=False, had_checkout=False) == expected


# --- the funnel -------------------------------------------------------------


def test_an_empty_funnel(treasury):
    data = analyse(treasury)
    assert data["jobs"] == 0
    assert "No orders in this period" in format_funnel(data)


def test_stages_are_nested_supersets(treasury):
    _job(treasury, "WON", "completed", paid=True, checkout=True)
    _job(treasury, "DECLINED", "failed", reason="projected margin 2% below floor")
    _job(treasury, "SCREENED", "failed", reason="intake: duplicate — x")
    _job(treasury, "GHOST", "expired", reason="checkout unpaid after 48h", checkout=True)

    stages = analyse(treasury)["stages"]
    assert stages["submitted"] == 4
    assert stages["quoted"] == 3  # the screened one never reached pricing
    assert stages["accepted"] == 2
    assert stages["checkout"] == 2
    assert stages["paid"] == 1
    assert stages["delivered"] == 1


def test_rates_are_computed_off_the_right_denominators(treasury):
    _job(treasury, "WON", "completed", paid=True, checkout=True)
    _job(treasury, "GHOST", "expired", reason="unpaid", checkout=True)
    _job(treasury, "DECLINED", "failed", reason="below floor")

    rates = analyse(treasury)["rates"]
    assert rates["gate_acceptance_pct"] == pytest.approx(66.7, abs=0.1)  # 2 of 3 quoted
    assert rates["payment_conversion_pct"] == pytest.approx(50.0)  # 1 paid of 2 checkouts
    assert rates["abandonment_pct"] == pytest.approx(50.0)
    assert rates["delivery_pct"] == pytest.approx(100.0)
    assert rates["end_to_end_pct"] == pytest.approx(33.3, abs=0.1)


def test_rates_are_none_rather_than_zero_without_data(treasury):
    rates = analyse(treasury)["rates"]
    assert rates["gate_acceptance_pct"] is None
    assert rates["payment_conversion_pct"] is None


def test_money_won_and_money_left_on_the_table(treasury):
    _job(treasury, "WON", "completed", budget=9_900, paid=True, checkout=True)
    _job(treasury, "LOST", "failed", budget=50_000, reason="below floor")
    _job(treasury, "GONE", "expired", budget=7_500, reason="unpaid", checkout=True)

    data = analyse(treasury)
    assert data["won_cents"] == 9_900
    assert data["lost_cents"] == 57_500


def test_losses_are_grouped_by_who_turned_the_order_away(treasury):
    _job(treasury, "A", "failed", reason="order $6 below minimum order size $15")
    _job(treasury, "B", "failed", reason="projected margin 9.5% below floor 35.0%")
    _job(treasury, "C", "failed", reason="intake: oversized_order — above the ceiling")

    data = analyse(treasury)
    assert data["gate_reasons"] == {"below minimum order size": 1, "below margin floor": 1}
    assert data["screen_reasons"] == {"oversized_order": 1}


def test_counter_offer_outcomes_are_tracked(treasury):
    _job(treasury, "OFFERED", "completed", paid=True, checkout=True)
    _job(treasury, "IGNORED", "failed", reason="below floor")
    for job_id in ("OFFERED", "IGNORED"):
        treasury.record_event(job_id, "counter_offer", {"stage": "counter_offer", "price_cents": 1_500})

    offers = analyse(treasury)["counter_offers"]
    assert offers["made"] == 2
    assert offers["won"] == 1
    assert offers["win_rate_pct"] == 50.0


def test_win_rate_is_broken_down_by_product_and_price_band(treasury):
    _job(treasury, "P1", "completed", budget=2_500, product="express", paid=True, checkout=True)
    _job(treasury, "P2", "failed", budget=2_500, product="express", reason="below floor")
    _job(treasury, "B1", "completed", budget=19_900, paid=True, checkout=True)

    data = analyse(treasury)
    assert data["by_product"]["express"]["submitted"] == 2
    assert data["by_product"]["express"]["win_rate_pct"] == 50.0
    assert data["by_product"]["bespoke"]["win_rate_pct"] == 100.0
    assert data["by_price_band"]["$100–$500"]["won"] == 1


def test_the_window_excludes_older_orders(treasury):
    import time

    _job(treasury, "OLD", "completed", paid=True, checkout=True)
    with treasury._conn() as conn, conn:
        conn.execute("UPDATE jobs SET created_at = ? WHERE id = 'OLD'", (time.time() - 30 * 86_400,))
    _job(treasury, "NEW", "completed", paid=True, checkout=True)

    assert analyse(treasury, since=time.time() - 86_400)["jobs"] == 1
    assert analyse(treasury)["jobs"] == 2


def test_rendering_covers_the_stages_and_the_losses(treasury):
    _job(treasury, "WON", "completed", paid=True, checkout=True)
    _job(treasury, "LOST", "failed", reason="projected margin 2% below floor")
    rendered = format_funnel(analyse(treasury), since_label="7d")

    assert "ORDER FUNNEL — 7d" in rendered
    assert "Accepted by the gate" in rendered
    assert "below margin floor" in rendered
    assert "left on the table" in rendered


def test_the_report_is_json_serialisable(treasury):
    _job(treasury, "WON", "completed", paid=True, checkout=True)
    json.dumps(analyse(treasury))
