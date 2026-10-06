"""
quality.py — does the brief the customer paid for actually hold up?

The agent protected its margin long before it protected its product. Every
other gate in this codebase asks whether the *money* is sound; nothing asked
whether the deliverable was. A brief that lost its risks section, echoed the
system prompt, or came back three sentences long still shipped, got billed, and
counted as a completed job.

The checks here are deliberately deterministic — structure, length, evidence
density, cleanliness, topic coverage — because a model grading its own homework
is the one judge you cannot audit. They are cheap, they run on every brief, and
they produce a score with a named reason for every point lost.

A failing brief gets one regeneration attempt and the better of the two ships.
It always ships: the customer has paid, and withholding the work is worse than
delivering it with the shortfall recorded for the operator to see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Headings a research brief is expected to carry. Each tuple is a set of
#: acceptable spellings for one required section.
REQUIRED_SECTIONS: tuple[tuple[str, ...], ...] = (
    ("executive summary", "summary"),
    ("key data", "findings", "data points"),
    ("risk",),
    ("recommendation", "conclusion"),
)

#: A brief shorter than this is not a brief.
MIN_CHARS = 400

#: Numbers are what separates research from opinion.
MIN_EVIDENCE_FIGURES = 3

#: Text that must never reach a customer: prompt scaffolding and dead ends.
LEAK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"<tool_call>", "tool-call markup left in the brief"),
    (r"(?i)you are solvent", "system prompt echoed into the brief"),
    (r"(?i)\b(todo|tbd|lorem ipsum|placeholder)\b", "placeholder text"),
    (r"(?i)as an ai (language )?model", "model disclaimer"),
)

PASS_SCORE = 80.0
WARN_SCORE = 60.0

GRADE_PASS = "pass"
GRADE_WARN = "warn"
GRADE_FAIL = "fail"

_FIGURE_RE = re.compile(r"\d")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*(.+?)\s*$", re.MULTILINE)
_WORD_RE = re.compile(r"[a-z0-9]{4,}")

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "over",
    "about",
    "outlook",
    "analysis",
    "research",
    "brief",
    "report",
    "market",
    "versus",
}


@dataclass
class Check:
    """One quality dimension, scored from 0 to its weight."""

    name: str
    weight: float
    earned: float
    detail: str

    @property
    def passed(self) -> bool:
        return self.earned >= self.weight

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "earned": round(self.earned, 1),
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class Score:
    """The verdict on one brief."""

    score: float
    grade: str
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        return [check.name for check in self.checks if not check.passed]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "grade": self.grade,
            "failures": self.failures,
            "checks": [check.as_dict() for check in self.checks],
        }


def headings(text: str) -> list[str]:
    """Markdown headings in the brief, lowercased."""
    return [match.group(1).strip().lower() for match in _HEADING_RE.finditer(text or "")]


def topic_keywords(topic: str) -> list[str]:
    """The words from a topic worth looking for in the brief."""
    words = [w for w in _WORD_RE.findall((topic or "").lower()) if w not in _STOPWORDS]
    # Keep order, drop duplicates.
    seen: dict[str, None] = {}
    for word in words:
        seen.setdefault(word, None)
    return list(seen)


def _section_check(text: str) -> Check:
    found = headings(text)
    hits = 0
    missing: list[str] = []
    for spellings in REQUIRED_SECTIONS:
        if any(any(spelling in heading for spelling in spellings) for heading in found):
            hits += 1
        else:
            missing.append(spellings[0])
    weight = 30.0
    earned = weight * hits / len(REQUIRED_SECTIONS)
    detail = (
        f"all {hits} required sections present" if not missing else f"missing: {', '.join(missing)}"
    )
    return Check("sections", weight, earned, detail)


def _length_check(text: str) -> Check:
    chars = len(text or "")
    weight = 20.0
    earned = weight if chars >= MIN_CHARS else weight * chars / MIN_CHARS
    return Check("length", weight, earned, f"{chars} characters (minimum {MIN_CHARS})")


def _evidence_check(text: str) -> Check:
    figures = len(_FIGURE_RE.findall(text or ""))
    weight = 20.0
    earned = weight if figures >= MIN_EVIDENCE_FIGURES else weight * figures / MIN_EVIDENCE_FIGURES
    return Check("evidence", weight, earned, f"{figures} figure(s) cited")


def _clean_check(text: str) -> Check:
    weight = 15.0
    leaks = [reason for pattern, reason in LEAK_PATTERNS if re.search(pattern, text or "")]
    earned = weight if not leaks else 0.0
    return Check("clean", weight, earned, "no scaffolding" if not leaks else "; ".join(leaks))


def _topic_check(text: str, topic: str) -> Check:
    weight = 15.0
    keywords = topic_keywords(topic)
    if not keywords:
        # Nothing to check against: do not punish the brief for a thin topic.
        return Check("topic_coverage", weight, weight, "no topic keywords to check")
    body = (text or "").lower()
    hits = [word for word in keywords if word in body]
    earned = weight * min(len(hits) / max(len(keywords) / 2, 1), 1.0)
    return Check(
        "topic_coverage",
        weight,
        earned,
        f"{len(hits)} of {len(keywords)} topic keyword(s) present",
    )


def blocking_failures(checks: list[Check]) -> list[str]:
    """Checks whose failure caps the grade however well the rest scored."""
    blocking = []
    for check in checks:
        if check.name == "clean" and not check.passed:
            blocking.append(check.name)
        elif check.name == "topic_coverage" and check.earned == 0:
            blocking.append(check.name)
    return blocking


def score_brief(text: str, topic: str = "") -> Score:
    """Score a brief out of 100 and grade it."""
    checks = [
        _section_check(text),
        _length_check(text),
        _evidence_check(text),
        _clean_check(text),
        _topic_check(text, topic),
    ]
    total = sum(check.earned for check in checks)
    if total >= PASS_SCORE:
        grade = GRADE_PASS
    elif total >= WARN_SCORE:
        grade = GRADE_WARN
    else:
        grade = GRADE_FAIL

    # Two failures are gates rather than tariffs, because points cannot buy
    # them back: scaffolding in the text (tool-call markup, a prompt echo) is
    # what a customer notices first, and a brief matching none of the topic's
    # keywords is not about what they asked for. Either one can score 85 on
    # structure, length and figures alone — neither is a brief worth "pass".
    if grade == GRADE_PASS and blocking_failures(checks):
        grade = GRADE_WARN
    return Score(round(total, 1), grade, checks)


def should_retry(score: Score) -> bool:
    """Worth one more attempt: a failing brief, or one that tripped a gate.

    A merely imperfect brief is not retried — the second draft of a thin brief
    is usually another thin brief, and the call costs money.
    """
    return score.grade == GRADE_FAIL or bool(blocking_failures(score.checks))


def better(first: tuple[str, Score], second: tuple[str, Score]) -> tuple[str, Score]:
    """Pick the higher-scoring of two attempts, keeping the first on a tie."""
    return second if second[1].score > first[1].score else first


def retry_instruction(score: Score) -> str:
    """What to tell the model about what was wrong, for the second attempt."""
    problems = [check.detail for check in score.checks if not check.passed]
    return (
        "The previous draft was rejected by the quality gate "
        f"(scored {score.score}/100). Fix specifically: "
        + "; ".join(problems)
        + ". Write the full brief again with markdown headings for executive summary, "
        "key data points, risks, and recommendation, citing concrete figures."
    )


# ---------------------------------------------------------------------------
# `solvent quality` — what the gate has been seeing
# ---------------------------------------------------------------------------


def delivered_scores(treasury: Any | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Quality verdicts recorded against delivered jobs, newest first."""
    from .treasury import Treasury

    t = treasury or Treasury()
    rows = [
        {
            "job_id": metric.get("job_id"),
            "score": metric.get("quality_score"),
            "grade": metric.get("quality_grade") or "",
            "flags": [f for f in (metric.get("quality_flags") or "").split(",") if f],
            "ts": metric.get("ts"),
        }
        for metric in t.list_metrics()
        if metric.get("quality_score") is not None
    ]
    rows.sort(key=lambda row: row["ts"] or 0, reverse=True)
    return rows[:limit]


def quality_report(treasury: Any | None = None, limit: int = 50) -> dict[str, Any]:
    """Score distribution, failing checks, and the worst briefs shipped."""
    rows = delivered_scores(treasury, limit)
    grades: dict[str, int] = {}
    flags: dict[str, int] = {}
    for row in rows:
        grades[row["grade"]] = grades.get(row["grade"], 0) + 1
        for flag in row["flags"]:
            flags[flag] = flags.get(flag, 0) + 1
    scores = [row["score"] for row in rows if row["score"] is not None]
    return {
        "briefs": len(rows),
        "mean_score": round(sum(scores) / len(scores), 1) if scores else None,
        "min_score": min(scores) if scores else None,
        "grades": grades,
        "failing_checks": dict(sorted(flags.items(), key=lambda kv: -kv[1])),
        "worst": sorted(rows, key=lambda row: row["score"] or 0)[:5],
        "recent": rows[:10],
    }


def format_quality(data: dict[str, Any]) -> str:
    """Render the quality report for a terminal."""
    lines = ["", "  DELIVERABLE QUALITY", f"  {'─' * 66}"]
    if not data["briefs"]:
        lines += ["  No briefs have been scored yet.", ""]
        return "\n".join(lines)

    lines += [
        f"  Briefs scored        {data['briefs']}",
        f"  Mean score           {data['mean_score']}/100  (worst {data['min_score']})",
        "  Grades               "
        + "  ·  ".join(f"{grade} {count}" for grade, count in sorted(data["grades"].items())),
    ]
    if data["failing_checks"]:
        lines += ["", "  Checks that failed"]
        for name, count in data["failing_checks"].items():
            lines.append(f"    {count:>4}  {name}")
    else:
        lines.append("  Every check passed on every brief.")

    lines += ["", f"  {'JOB':<14}{'SCORE':>7}{'GRADE':>8}  SHORTFALL"]
    for row in data["worst"]:
        lines.append(
            f"  {str(row['job_id'])[:13]:<14}{row['score']:>7}{row['grade']:>8}  "
            f"{', '.join(row['flags']) or '—'}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="solvent quality",
        description="Scores the quality gate gave the briefs it shipped.",
    )
    parser.add_argument("--limit", type=int, default=50, help="how many recent briefs to read")
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    data = quality_report(limit=args.limit)
    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_quality(data))


if __name__ == "__main__":
    main()
