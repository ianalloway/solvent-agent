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


# --- operator-editable catalogue -------------------------------------------


def _write(tmp_path, payload):
    import json as _json

    path = tmp_path / "products.json"
    path.write_text(_json.dumps(payload) if not isinstance(payload, str) else payload)
    return path


def test_without_a_file_the_builtin_list_is_in_force(tmp_path):
    from solvent.products import load_catalog

    assert load_catalog(tmp_path / "missing.json") == CATALOG


def test_an_override_can_change_a_price_without_restating_the_scope(tmp_path):
    from solvent.products import by_slug, load_catalog

    path = _write(tmp_path, {"products": [{"slug": "express", "list_price_cents": 3_500}]})
    express = by_slug(path)["express"]

    assert express.list_price_cents == 3_500
    assert express.est_tokens == get_product("express").est_tokens  # scope inherited
    assert len(load_catalog(path)) == len(CATALOG)


def test_an_override_can_add_a_product(tmp_path):
    from solvent.products import by_slug

    path = _write(
        tmp_path,
        {
            "products": [
                {
                    "slug": "retainer",
                    "name": "Monthly retainer",
                    "list_price_cents": 49_900,
                    "est_tokens": 60_000,
                    "market_data_calls": 12,
                    "web_search_calls": 40,
                }
            ]
        },
    )
    retainer = by_slug(path)["retainer"]
    assert retainer.name == "Monthly retainer"
    assert retainer.list_price_cents == 49_900


def test_a_new_product_must_state_its_own_price_and_scope(tmp_path):
    from solvent.products import by_slug, catalog_problems

    path = _write(tmp_path, {"products": [{"slug": "mystery", "list_price_cents": 1_000}]})
    assert "mystery" not in by_slug(path)
    assert any("must state" in problem for problem in catalog_problems(path))


def test_an_override_can_retire_a_product(tmp_path):
    from solvent.products import by_slug

    path = _write(tmp_path, {"retire": ["deep-dive"]})
    index = by_slug(path)
    assert "deep-dive" not in index
    assert "standard" in index


def test_replace_makes_the_file_the_whole_list(tmp_path):
    from solvent.products import load_catalog

    path = _write(
        tmp_path,
        {
            "replace": True,
            "products": [
                {
                    "slug": "only",
                    "list_price_cents": 5_000,
                    "est_tokens": 8_000,
                    "market_data_calls": 2,
                    "web_search_calls": 6,
                }
            ],
        },
    )
    assert [p.slug for p in load_catalog(path)] == ["only"]


def test_a_bare_list_is_accepted(tmp_path):
    from solvent.products import by_slug

    path = _write(tmp_path, [{"slug": "express", "list_price_cents": 1_900}])
    assert by_slug(path)["express"].list_price_cents == 1_900


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        '"a string"',
        '{"products": "express"}',
        '{"products": [{"slug": "express", "list_price_cents": "lots"}]}',
        '{"products": [{"slug": "express", "est_tokens": -5}]}',
        '{"products": [{"list_price_cents": 100}]}',
        '{"products": ["express"]}',
    ],
)
def test_an_unreadable_override_never_changes_what_is_sold(tmp_path, payload):
    from solvent.products import by_slug

    path = _write(tmp_path, payload)
    assert by_slug(path)["express"] == get_product("express")


def test_emptying_the_catalogue_falls_back_to_the_builtins(tmp_path):
    from solvent.products import load_catalog

    path = _write(tmp_path, {"replace": True, "products": []})
    assert load_catalog(path) == CATALOG


def test_problems_name_the_entry_and_the_fault(tmp_path):
    from solvent.products import catalog_problems

    path = _write(
        tmp_path,
        {
            "products": [
                {"slug": "express", "list_price_cents": "lots"},
                {"slug": "standard"},
                {"slug": "standard", "list_price_cents": 8_000},
                {"name": "nameless"},
                "not an object",
            ]
        },
    )
    problems = catalog_problems(path)
    assert any("entry 1 (express)" in p and "unreadable" in p for p in problems)
    assert any("duplicate slug 'standard'" in p for p in problems)
    assert any("entry 4 has no slug" in p for p in problems)
    assert any("entry 5 is not an object" in p for p in problems)


def test_a_clean_catalogue_has_no_problems(tmp_path):
    from solvent.products import catalog_problems

    assert catalog_problems(tmp_path / "missing.json") == []
    path = _write(tmp_path, {"products": [{"slug": "express", "list_price_cents": 3_500}]})
    assert catalog_problems(path) == []


def test_problems_are_reported_alongside_the_price_list(tmp_path):
    rows = price_list(PricingPolicy())
    rendered = format_price_list(rows, PricingPolicy(), ["products.json entry 1: broken"])
    assert "⚠ products.json entry 1: broken" in rendered


def test_price_list_prices_whatever_catalogue_it_is_given(tmp_path):
    from solvent.products import load_catalog

    path = _write(tmp_path, {"retire": ["express", "standard"]})
    rows = price_list(PricingPolicy(), load_catalog(path))
    assert [row["slug"] for row in rows] == ["deep-dive"]
