"""Cost reconciliation should flag drift in either direction."""

from types import SimpleNamespace

from solvent.service import _resources_from_usage, reconcile_cogs
from solvent.tools import ToolContext


def _quote(estimate):
    return SimpleNamespace(est_cost_cents=estimate, price_cents=4900, margin_pct=80.0)


def test_aggregate_tokens_flow_into_inference_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    resources = _resources_from_usage({"total_tokens": 6000}, ToolContext())
    inference = next(amount for vendor, amount, _ in resources if vendor == "nvidia-nemotron")
    assert inference == 180


def test_reconcile_flags_underestimate():
    result = reconcile_cogs(_quote(1000), {"actual_cost_cents": 1200})
    assert result["cost_warning"] is True
    assert result["cost_drift_direction"] == "underestimated"
    assert result["margin_drift_cents"] == 200


def test_reconcile_flags_overestimate():
    result = reconcile_cogs(_quote(1000), {"actual_cost_cents": 100})
    assert result["cost_warning"] is True
    assert result["cost_drift_direction"] == "overestimated"
    assert result["margin_drift_cents"] == -900


def test_reconcile_keeps_small_drift_quiet():
    result = reconcile_cogs(_quote(1000), {"actual_cost_cents": 900})
    assert result["cost_warning"] is False
    assert result["cost_drift_direction"] == "overestimated"


def test_reconcile_flags_unestimated_cost():
    result = reconcile_cogs(_quote(0), {"actual_cost_cents": 100})
    assert result["cost_warning"] is True
    assert result["cost_drift_direction"] == "underestimated"
