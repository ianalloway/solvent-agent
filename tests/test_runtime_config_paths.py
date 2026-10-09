"""Operator config and persistent stores must not depend on process cwd."""

import json

from solvent import checkout, intake, pairing, workspace
from solvent.config import SolventConfig, load_config, save_config
from solvent.guardrail_cmd import format_guardrails, gather
from solvent.guardrails import load_spend_policy
from solvent.paths import config_dir, config_path
from solvent.pricing import get_resource_costs
from solvent.rate_limit import RateLimiter
from solvent.stripe_client import StripeClient
from solvent.treasury import Treasury
from solvent.webhook_log import WebhookLog


def test_policies_and_preferences_follow_solvent_home_after_cwd_change(tmp_path, monkeypatch):
    home = tmp_path / "home"
    start = tmp_path / "start"
    other = tmp_path / "other"
    start.mkdir()
    other.mkdir()
    monkeypatch.setenv("SOLVENT_HOME", str(home))
    monkeypatch.setenv("SOLVENT_TELEGRAM_DM_POLICY", "allowlist")
    monkeypatch.chdir(start)

    config_dir().mkdir(parents=True)
    config_path("spend_policy.json").write_text(
        json.dumps({"daily_budget_cents": 500, "max_txn_cents": 50}), encoding="utf-8"
    )
    config_path("pricing_overrides.json").write_text(
        json.dumps({"pdf_render": 25}), encoding="utf-8"
    )
    config_path("intake_policy.json").write_text(
        json.dumps({"max_budget_cents": 5000}), encoding="utf-8"
    )
    config_path("checkout_policy.json").write_text(
        json.dumps({"expire_after_hours": 12}), encoding="utf-8"
    )
    config_path("telegram_allowlist.json").write_text('["approved-user"]', encoding="utf-8")
    save_config(SolventConfig(model="nemotron"))

    # A cwd-local policy must not override the policy under SOLVENT_HOME.
    (other / ".solvent").mkdir()
    (other / ".solvent" / "spend_policy.json").write_text(
        json.dumps({"daily_budget_cents": 99_999}), encoding="utf-8"
    )
    monkeypatch.chdir(other)

    assert load_spend_policy().daily_budget_cents == 500
    assert load_spend_policy().max_txn_cents == 50
    assert get_resource_costs()["pdf_render"] == 25
    assert intake.load_policy().max_budget_cents == 5000
    assert checkout.load_policy().expire_after_hours == 12
    assert pairing.is_allowed("approved-user")
    assert not pairing.is_allowed("other-user")
    assert load_config().model == "nemotron"
    assert workspace.workspace_path() == config_dir() / "workspace"
    assert workspace.skills_dir() == config_dir() / "skills"
    report = gather(Treasury(path=tmp_path / "ledger.db"))
    assert report["policy_path"] == str(config_path("spend_policy.json"))
    assert report["policy_override_present"]
    assert str(config_path("spend_policy.json")) in format_guardrails(report)


def test_persistent_stores_follow_solvent_home_after_cwd_change(tmp_path, monkeypatch):
    home = tmp_path / "home"
    start = tmp_path / "start"
    other = tmp_path / "other"
    start.mkdir()
    other.mkdir()
    monkeypatch.setenv("SOLVENT_HOME", str(home))
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    monkeypatch.chdir(start)

    first_limit = RateLimiter(burst_limit=1)
    assert first_limit.check("same-user")[0]
    first_limit.close()
    first_webhook_log = WebhookLog()
    first_webhook_log.record("evt_1", "checkout.session.completed", b"{}", "processed")
    first_stripe = StripeClient()
    first_stripe._cache_webhook_payment("job:J1", {"paid": True})

    monkeypatch.chdir(other)
    second_limit = RateLimiter(burst_limit=1)
    assert not second_limit.check("same-user")[0]
    second_limit.close()
    assert WebhookLog().list_recent()[0]["event_id"] == "evt_1"
    assert StripeClient().get_cached_payment("J1") == {"paid": True}
    for name in ("rate_limits.db", "webhooks.db", "stripe_payments.json"):
        assert config_path(name).is_file()
        assert not (other / ".solvent" / name).exists()
