"""Tests for the price list (solvent.products)."""

from __future__ import annotations

import pytest

from solvent.pricing import PricingPolicy, quote
from solvent.products import (
    BY_SLUG,
    CATALOG,
    UnknownProduct,
    apply_product,
    format_price_list,
    get_product,
    price_list,
    stale_products,
)


def test_every_product_is_sellable_at_current_costs():
    for row in price_list():
        assert row["sellable"], f"{row['slug']} does not clear the floor: {row['reason']}"
        assert row["margin_pct"] >= PricingPolicy().margin_floor_pct


def test_scope_and_price_rise_together():
    ordered = sorted(CATALOG, key=lambda p: p.list_price_cents)
    assert [p.slug for p in ordered] == ["express", "standard", "deep-dive"]
    assert [p.est_tokens for p in ordered] == sorted(p.est_tokens for p in ordered)


def test_slugs_are_unique_and_indexed():
    assert len(BY_SLUG) == len(CATALOG)
    for product in CATALOG:
        assert get_product(product.slug) is product


def test_lookup_is_forgiving_about_case_and_spacing():
    assert get_product("  STANDARD ").slug == "standard"


def test_an_unknown_product_names_the_ones_that_exist():
    with pytest.raises(UnknownProduct, match="known: express"):
        get_product("platinum")


def test_apply_product_fills_scope_and_price():
    job = apply_product({"id": "J1", "topic": "t", "product": "standard"})
    product = get_product("standard")
    assert job["budget_cents"] == product.list_price_cents
    assert job["est_tokens"] == product.est_tokens
    assert job["market_data_calls"] == product.market_data_calls
    assert job["web_search_calls"] == product.web_search_calls


def test_explicit_job_fields_beat_the_product():
    job = apply_product(
        {
            "id": "J1",
            "topic": "t",
            "product": "standard",
            "budget_cents": 9_900,
            "est_tokens": 1_000,
        }
    )
    assert job["budget_cents"] == 9_900
    assert job["est_tokens"] == 1_000
    assert job["web_search_calls"] == get_product("standard").web_search_calls


def test_a_job_without_a_product_is_untouched():
    job = {"id": "J1", "topic": "t", "budget_cents": 4_900}
    assert apply_product(job) == job


def test_apply_product_does_not_mutate_the_caller_s_job():
    job = {"id": "J1", "topic": "t", "product": "express"}
    apply_product(job)
    assert "budget_cents" not in job


def test_a_stale_price_is_flagged_when_costs_rise():
    """Cost calibration is exactly how a list price goes stale."""
    hot = PricingPolicy(cost_calibration=2.0, margin_floor_pct=90.0)
    rows = price_list(hot)
    stale = stale_products(rows)
    assert stale
    assert "below floor" in format_price_list(rows, hot)
    assert "no longer clear the margin floor" in format_price_list(rows, hot)


def test_format_lists_every_product_and_its_blurb():
    rows = price_list()
    rendered = format_price_list(rows, PricingPolicy())
    assert "PRICE LIST" in rendered
    for product in CATALOG:
        assert product.slug in rendered
        assert product.blurb in rendered
    assert "Every product clears the margin floor" in rendered


# --- stage machine integration ---------------------------------------------


def test_a_job_can_order_by_product(tmp_path):
    from solvent.stages import validate_and_coerce_job
    from solvent.treasury import Treasury

    t = Treasury(path=tmp_path / "ledger.db")
    job, err = validate_and_coerce_job(
        {"id": "J1", "topic": "Edge AI", "customer_email": "a@x.example", "product": "deep-dive"},
        t,
    )
    assert err is None
    assert job["budget_cents"] == get_product("deep-dive").list_price_cents
    assert quote(job).accept


def test_an_unknown_product_is_declined_not_crashed(tmp_path):
    from solvent.stages import validate_and_coerce_job
    from solvent.treasury import Treasury

    t = Treasury(path=tmp_path / "ledger.db")
    job, err = validate_and_coerce_job(
        {"id": "J1", "topic": "Edge AI", "customer_email": "a@x.example", "product": "platinum"}, t
    )
    assert job is None
    assert "unknown product" in err
    assert t.get_job("J1")["status"] == "failed"
