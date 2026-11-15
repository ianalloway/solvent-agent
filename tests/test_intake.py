"""Tests for the commercial intake screen (solvent.intake)."""

from __future__ import annotations

import json
import time

import pytest

from solvent.intake import (
    IntakePolicy,
    decline_reason,
    format_intake,
    load_policy,
    normalise_topic,
    recent_blocks,
    screen_job,
)
from solvent.treasury import Treasury

JOB = {
    "id": "NEW",
    "topic": "Competitive landscape for AI inference chips",
    "budget_cents": 4_900,
    "customer_email": "buyer@fund.example",
}


@pytest.fixture
def treasury(tmp_path):
    return Treasury(path=tmp_path / "ledger.db")


def _existing(
    treasury, job_id, *, topic=JOB["topic"], email=JOB["customer_email"], status="completed"
):
    treasury.upsert_job(job_id, status, topic=topic, budget_cents=4_900, customer_email=email)


def test_a_clean_job_passes(treasury):
    screen = screen_job(JOB, treasury)
    assert screen.allowed
    assert screen.rule is None


def test_a_repeat_submission_of_the_same_brief_is_a_duplicate(treasury):
    _existing(treasury, "J1")
    screen = screen_job(JOB, treasury)
    assert not screen.allowed
    assert screen.rule == "duplicate"
    assert screen.duplicate_of == "J1"
    assert "intake: duplicate" in decline_reason(screen)


def test_duplicate_matching_ignores_case_and_spacing(treasury):
    _existing(treasury, "J1", topic="  COMPETITIVE   landscape for AI inference chips ")
    assert screen_job(JOB, treasury).rule == "duplicate"
    assert normalise_topic(" A  B ") == "a b"


def test_a_different_brief_from_the_same_customer_passes(treasury):
    _existing(treasury, "J1", topic="something else entirely")
    assert screen_job(JOB, treasury).allowed


def test_the_same_brief_from_a_different_customer_passes(treasury):
    _existing(treasury, "J1", email="someone@else.example")
    assert screen_job(JOB, treasury).allowed


def test_an_old_submission_is_not_a_duplicate(treasury):
    _existing(treasury, "J1")
    with treasury._conn() as conn, conn:
        conn.execute("UPDATE jobs SET created_at = ? WHERE id = 'J1'", (time.time() - 7_200,))
    assert screen_job(JOB, treasury).allowed


def test_a_dead_job_does_not_block_a_resubmission(treasury):
    _existing(treasury, "J1", status="failed")
    assert screen_job(JOB, treasury).allowed


def test_a_job_never_duplicates_itself(treasury):
    """Retries and worker resumes re-screen the same job; that must be fine."""
    _existing(treasury, "NEW")
    assert screen_job(JOB, treasury).allowed


def test_a_burst_from_one_customer_is_turned_away(treasury):
    policy = IntakePolicy(max_jobs_per_customer_per_hour=3)
    for i in range(3):
        _existing(treasury, f"J{i}", topic=f"topic {i}")
    screen = screen_job(JOB, treasury, policy)
    assert not screen.allowed
    assert screen.rule == "customer_burst"


def test_an_oversized_order_needs_a_human(treasury):
    screen = screen_job({**JOB, "budget_cents": 500_000}, treasury)
    assert not screen.allowed
    assert screen.rule == "oversized_order"
    assert "operator" in screen.reason


@pytest.mark.parametrize("email", ["", "   ", "nope", "no@domain", "a b@x.example"])
def test_an_unreachable_customer_is_turned_away(treasury, email):
    screen = screen_job({**JOB, "customer_email": email}, treasury)
    assert not screen.allowed
    assert screen.rule == "unreachable_customer"


def test_email_requirement_can_be_switched_off(treasury):
    assert screen_job(
        {**JOB, "customer_email": ""}, treasury, IntakePolicy(require_email=False)
    ).allowed


def test_blocked_domains_are_refused(treasury):
    policy = IntakePolicy(blocked_email_domains=("burner.example",))
    screen = screen_job({**JOB, "customer_email": "a@burner.example"}, treasury, policy)
    assert not screen.allowed
    assert screen.rule == "blocked_domain"


def test_screening_declines_when_customer_history_is_unavailable(treasury):
    class Broken(Treasury):
        def recent_jobs_for_customer(self, email, cutoff):
            raise RuntimeError("db gone")

    broken = Broken(path=treasury.path)
    assert not screen_job(JOB, broken).allowed


def test_policy_file_overrides_defaults(tmp_path):
    path = tmp_path / "intake_policy.json"
    path.write_text(
        json.dumps(
            {
                "max_budget_cents": 5_000,
                "duplicate_window_minutes": 5,
                "blocked_email_domains": ["@Spam.example"],
                "require_email": False,
            }
        )
    )
    policy = load_policy(path)
    assert policy.max_budget_cents == 5_000
    assert policy.duplicate_window_minutes == 5
    assert policy.blocked_email_domains == ("spam.example",)
    assert policy.require_email is False


def test_a_broken_policy_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "intake_policy.json"
    path.write_text("not json")
    assert load_policy(path) == IntakePolicy()


# --- stage machine integration ---------------------------------------------


def test_a_screened_job_never_reaches_pricing(tmp_path):
    from solvent.agent import Solvent

    agent = Solvent(seed_cents=20_000, fresh=True)
    db = tmp_path / "t.db"
    agent.t.path = db
    agent.t.lock_path = db.with_suffix(".lock")
    agent.t._init_db()
    agent.t.seed(20_000)

    _existing(agent.t, "J1", status="awaiting_payment")

    result = agent.handle_job({**JOB, "id": "J2"})
    assert result["stage"] == "declined"
    assert "intake: duplicate" in result["reason"]
    # No quote was ever taken for the duplicate.
    assert [e["stage"] for e in agent.t.list_events(job_id="J2")] == ["declined"]
    assert agent.t.get_job("J2")["status"] == "failed"


def test_recent_blocks_and_report(treasury):
    treasury.upsert_job(
        "BAD",
        "failed",
        topic="dupe",
        budget_cents=4_900,
        customer_email="a@x.example",
        error_reason="intake: duplicate — same brief already submitted as J1",
    )
    treasury.upsert_job("FINE", "completed", topic="ok", budget_cents=1_000)

    blocks = recent_blocks(treasury)
    assert [b["job_id"] for b in blocks] == ["BAD"]
    assert blocks[0]["rule"] == "duplicate"

    rendered = format_intake(IntakePolicy(), blocks)
    assert "INTAKE SCREEN" in rendered
    assert "duplicate" in rendered


def test_report_with_nothing_blocked():
    assert "Nothing has been turned away" in format_intake(IntakePolicy(), [])
