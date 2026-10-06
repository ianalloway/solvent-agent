"""Tests for the deliverable quality gate (solvent.quality)."""

from __future__ import annotations

from unittest import mock

import pytest

from solvent.quality import (
    GRADE_FAIL,
    GRADE_PASS,
    GRADE_WARN,
    MIN_CHARS,
    PASS_SCORE,
    WARN_SCORE,
    better,
    delivered_scores,
    format_quality,
    headings,
    quality_report,
    retry_instruction,
    score_brief,
    should_retry,
    topic_keywords,
)
from solvent.treasury import Treasury

GOOD_BRIEF = """# Research Brief: AI inference chips, 2026

## Executive summary
Demand is up 24% year on year and the competitive set is consolidating.

## Key data points
- Market growing 18-24% YoY.
- Gross margins of 55-65% for leaders.
- Customer acquisition cost up 30% in two years.

## Risks
- Regulatory exposure in 2 jurisdictions.
- Buyer concentration among the top 3 customers.

## Recommendation
Selective exposure, sized at no more than 4% of the book.
"""


def test_a_complete_brief_scores_full_marks():
    score = score_brief(GOOD_BRIEF, "AI inference chips, 2026")
    assert score.score == 100.0
    assert score.grade == GRADE_PASS
    assert score.failures == []


def test_a_missing_section_costs_points():
    without_risks = GOOD_BRIEF.replace("## Risks", "## Notes")
    score = score_brief(without_risks, "AI inference chips")
    assert score.score < 100
    assert "sections" in score.failures
    detail = next(c.detail for c in score.checks if c.name == "sections")
    assert "risk" in detail


def test_a_stub_of_a_brief_fails():
    score = score_brief("# Brief\n\nNot much to say.", "AI inference chips")
    assert score.grade == GRADE_FAIL
    assert "length" in score.failures
    assert should_retry(score)


def test_length_is_scored_proportionally():
    half = "x" * (MIN_CHARS // 2)
    check = next(c for c in score_brief(half).checks if c.name == "length")
    assert 0 < check.earned < check.weight


def test_opinion_without_numbers_loses_the_evidence_points():
    prose = GOOD_BRIEF
    for digit in "0123456789":
        prose = prose.replace(digit, "")
    assert "evidence" in score_brief(prose, "AI inference chips").failures


@pytest.mark.parametrize(
    "leak",
    [
        '<tool_call>{"name": "web_search"}</tool_call>',
        "You are SOLVENT, a disciplined sell-side research analyst.",
        "TODO: finish this section",
        "As an AI language model, I cannot predict markets.",
    ],
)
def test_scaffolding_never_passes_the_clean_check(leak):
    score = score_brief(GOOD_BRIEF + "\n" + leak, "AI inference chips")
    assert "clean" in score.failures


def test_a_brief_about_the_wrong_topic_can_never_pass():
    """Matching none of the topic's keywords is the most serious defect there is."""
    score = score_brief(GOOD_BRIEF, "stablecoin payment volumes and take rates")
    assert "topic_coverage" in score.failures
    assert score.score >= 80  # would have passed on structure alone
    assert score.grade == GRADE_WARN
    assert should_retry(score)


def test_a_thin_topic_is_not_held_against_the_brief():
    check = next(
        c for c in score_brief(GOOD_BRIEF, "the market").checks if c.name == "topic_coverage"
    )
    assert check.passed


def test_topic_keywords_drop_stopwords_and_duplicates():
    assert topic_keywords("The outlook for the stablecoin market and stablecoin rails") == [
        "stablecoin",
        "rails",
    ]


def test_headings_are_extracted_lowercased():
    assert "executive summary" in headings(GOOD_BRIEF)


def test_grades_sit_on_the_thresholds():
    assert score_brief(GOOD_BRIEF, "AI inference chips").grade == GRADE_PASS
    # On topic, clean, well evidenced, but three of four headings are wrong:
    # a warn, and not worth a retry.
    mislabelled = (
        GOOD_BRIEF.replace("## Executive summary", "## Overview")
        .replace("## Risks", "## Notes")
        .replace("## Recommendation", "## Closing")
    )
    warn = score_brief(mislabelled, "AI inference chips, 2026")
    assert WARN_SCORE <= warn.score < PASS_SCORE
    assert warn.grade == GRADE_WARN
    assert not should_retry(warn)


def test_scaffolding_can_never_grade_as_a_pass():
    """A leak is what the customer notices first, whatever the score says."""
    leaky = score_brief(GOOD_BRIEF + "\nTODO: tidy this up", "AI inference chips")
    assert leaky.score >= 80  # would have passed on points alone
    assert leaky.grade == GRADE_WARN
    assert should_retry(leaky)


def test_the_better_draft_wins_and_ties_keep_the_first():
    first = ("a", score_brief(GOOD_BRIEF, "AI inference chips"))
    worse = ("b", score_brief("short", "AI inference chips"))
    assert better(first, worse) is first
    assert better(worse, first) is first
    assert better(first, ("c", first[1]))[0] == "a"


def test_the_retry_instruction_names_what_was_wrong():
    score = score_brief("# Brief\n\nNothing here.", "AI inference chips")
    instruction = retry_instruction(score)
    assert "rejected by the quality gate" in instruction
    assert "characters" in instruction


# --- fulfilment integration -------------------------------------------------


def _tool_ctx():
    return type("C", (), {"total_calls": 0, "market_data_calls": 0, "web_search_calls": 0})()


def test_fulfilment_scores_the_brief(tmp_path, monkeypatch):
    from solvent import service

    monkeypatch.setattr(service, "OUTPUT_DIR", tmp_path)
    with mock.patch(
        "solvent.nemotron.research_brief",
        return_value=(GOOD_BRIEF, {"total_tokens": 300}, _tool_ctx()),
    ):
        result = service.fulfill({"id": "J1", "topic": "AI inference chips, 2026"})

    assert result["quality"].grade == GRADE_PASS
    assert result["quality_attempts"] == 1


def test_a_failing_brief_is_retried_once_and_the_better_draft_ships(tmp_path, monkeypatch):
    from solvent import service

    monkeypatch.setattr(service, "OUTPUT_DIR", tmp_path)
    attempts = [
        ("# Brief\n\nToo short.", {"total_tokens": 100}, _tool_ctx()),
        (GOOD_BRIEF, {"total_tokens": 300}, _tool_ctx()),
    ]
    with mock.patch("solvent.nemotron.research_brief", side_effect=attempts) as brief:
        result = service.fulfill({"id": "J1", "topic": "AI inference chips, 2026"})

    assert brief.call_count == 2
    assert result["quality_attempts"] == 2
    assert result["quality"].grade == GRADE_PASS
    assert "Executive summary" in result["text"]
    # Both calls were paid for, so both are billed.
    assert result["tokens"] == 400


def test_a_brief_that_cannot_be_saved_still_ships(tmp_path, monkeypatch):
    """Two failures in, the customer still gets the best draft rather than nothing."""
    from solvent import service

    monkeypatch.setattr(service, "OUTPUT_DIR", tmp_path)
    weak = ("# Brief\n\nStill short.", {"total_tokens": 100}, _tool_ctx())
    with mock.patch("solvent.nemotron.research_brief", side_effect=[weak, weak]):
        result = service.fulfill({"id": "J1", "topic": "AI inference chips"})

    assert result["quality"].grade == GRADE_FAIL
    assert result["deliverable_path"]


def test_the_stage_machine_records_and_publishes_the_verdict(tmp_path):
    from solvent.agent import Solvent

    agent = Solvent(seed_cents=20_000, fresh=True)
    db = tmp_path / "t.db"
    agent.t.path = db
    agent.t.lock_path = db.with_suffix(".lock")
    agent.t._init_db()
    agent.t.seed(20_000)

    with mock.patch.dict("os.environ", {"SOLVENT_DELIVERY_SECRET": "x" * 40}, clear=False):
        agent.handle_job(
            {
                "id": "J1",
                "topic": "Competitive landscape for AI inference chips",
                "customer_email": "a@x.example",
                "budget_cents": 4_900,
            }
        )

    verdict = next(e for e in agent.log if e.get("stage") == "quality_checked")
    assert verdict["grade"] == GRADE_PASS
    metrics = agent.t.get_metrics("J1")
    assert metrics["quality_score"] == verdict["score"]
    assert metrics["quality_grade"] == GRADE_PASS


# --- the report -------------------------------------------------------------


def test_report_summarises_scores(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.upsert_metrics("J1", quality_score=100.0, quality_grade="pass", quality_flags="")
    t.upsert_metrics(
        "J2", quality_score=55.0, quality_grade="fail", quality_flags="length,evidence"
    )

    data = quality_report(t)
    assert data["briefs"] == 2
    assert data["mean_score"] == 77.5
    assert data["min_score"] == 55.0
    assert data["grades"] == {"pass": 1, "fail": 1}
    assert data["failing_checks"] == {"length": 1, "evidence": 1}
    assert data["worst"][0]["job_id"] == "J2"

    rendered = format_quality(data)
    assert "DELIVERABLE QUALITY" in rendered
    assert "J2" in rendered


def test_unscored_jobs_are_skipped(tmp_path):
    t = Treasury(path=tmp_path / "ledger.db")
    t.upsert_metrics("J1", est_cost_cents=500)
    assert delivered_scores(t) == []
    assert "No briefs have been scored yet" in format_quality(quality_report(t))
