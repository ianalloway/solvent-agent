"""Fix 8: earn re-checks revenue under the treasury lock (double-earn race)."""

from unittest import mock

from solvent.agent import Solvent


def test_concurrent_booking_during_confirm_is_not_double_earned(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    agent = Solvent(seed_cents=10_000, fresh=True)
    runner = agent._runner
    job = {"id": "J-race", "topic": "race", "budget_cents": 5000}
    link = {"id": "cs_race", "url": "http://x", "amount_cents": 5000, "simulated": True}
    payment = {
        "paid": True,
        "stripe_ref": "pi_race",
        "checkout_session_id": "cs_race",
        "amount_cents": 5000,
    }

    def confirm_and_race(*a, **kw):
        # A webhook worker books the same payment after the fast-path check
        # but before this worker takes the lock to earn.
        agent.t.earn(5000, "webhook", job_id="J-race", stripe_ref="pi_race")
        return payment

    q = mock.Mock(price_cents=5000, est_cost_cents=100, margin_cents=4900, margin_pct=90)
    monkeypatch.setattr(runner, "sync_payment", True, raising=False)
    with mock.patch.object(agent.stripe, "confirm_payment", side_effect=confirm_and_race):
        runner._stage_paid(job, link, q)
    revenue = [e for e in agent.t.entries if e.kind == "revenue" and e.job_id == "J-race"]
    assert len(revenue) == 1
    assert agent.t.balance_cents() == 15_000
