"""Policy choices must not change the synthetic jobs or latent cost draws."""

import random

import pytest

from solvent.guardrails import SpendPolicy
from solvent.optimize import sweep
from solvent.pricing import PricingPolicy
from solvent.simulate import JobMix, run_trial


class RecordingMix(JobMix):
    def __init__(self):
        super().__init__()
        self.jobs = []

    def draw(self, rng, index):
        job = super().draw(rng, index)
        self.jobs.append(job.copy())
        return job


class RecordingRandom(random.Random):
    def __init__(self, seed):
        super().__init__(seed)
        self.cost_factors = []

    def lognormvariate(self, mu, sigma):
        factor = super().lognormvariate(mu, sigma)
        self.cost_factors.append(factor)
        return factor


@pytest.mark.parametrize("decline_path", ["pricing", "cash_reserve"])
def test_rejected_jobs_keep_demand_and_latent_costs_aligned(decline_path):
    def run(pricing, spend_policy):
        rng = RecordingRandom(37)
        mix = RecordingMix()
        results = [
            run_trial(
                rng,
                jobs=10,
                capital_cents=100_000,
                pricing=pricing,
                spend_policy=spend_policy,
                mix=mix,
                cost_spread=0.35,
            )
            for _ in range(2)
        ]
        return mix.jobs, rng.cost_factors, results

    loose = PricingPolicy(margin_floor_pct=0, min_price_cents=0)
    accepted_jobs, accepted_costs, accepted = run(loose, SpendPolicy())
    strict = PricingPolicy(min_price_cents=100_000) if decline_path == "pricing" else loose
    guard = (
        SpendPolicy(min_reserve_cents=1_000_000)
        if decline_path == "cash_reserve"
        else SpendPolicy()
    )
    declined_jobs, declined_costs, declined = run(strict, guard)

    assert sum(result.accepted for result in accepted) > 0
    assert sum(result.declined for result in declined) == 20
    assert declined_jobs == accepted_jobs
    assert declined_costs == accepted_costs
    assert len(declined_costs) == 20


def test_sweep_cells_share_actual_demand_and_cost_draws(monkeypatch):
    jobs = []
    costs = []
    draw_job = JobMix.draw
    draw_cost = random.Random.lognormvariate

    def record_job(self, rng, index):
        job = draw_job(self, rng, index)
        jobs.append(job.copy())
        return job

    def record_cost(self, mu, sigma):
        factor = draw_cost(self, mu, sigma)
        costs.append(factor)
        return factor

    monkeypatch.setattr(JobMix, "draw", record_job)
    monkeypatch.setattr(random.Random, "lognormvariate", record_cost)
    sweep((0.0, 99.9), (0, 100_000), trials=2, jobs=8, seed=23)

    per_cell = 16
    assert len(jobs) == len(costs) == per_cell * 4
    for start in range(per_cell, len(jobs), per_cell):
        assert jobs[start : start + per_cell] == jobs[:per_cell]
        assert costs[start : start + per_cell] == costs[:per_cell]
