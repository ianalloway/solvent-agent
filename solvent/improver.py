"""Auto-improvement loop: tune pricing and guardrails from job metrics."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .pricing import RESOURCE_COSTS_CENTS
from .treasury import Treasury

IMPROVEMENT_LOG = Path("data/improvement_log.jsonl")
OVERRIDES_PATH = Path(".solvent/pricing_overrides.json")
PROMPTS_DIR = Path(".solvent/prompts")


def analyze_metrics(treasury: Treasury) -> dict:
    metrics = treasury.list_metrics()
    completed = [m for m in metrics if m.get("refunded", 0) == 0 and m.get("actual_cost_cents")]
    if len(completed) < 5:
        return {"ready": False, "reason": f"need 5+ completed jobs, have {len(completed)}"}
    margin_errors = [
        (m.get("actual_margin_pct") or 0) - (m.get("est_margin_pct") or 0)
        for m in completed
    ]
    avg_margin_error = sum(margin_errors) / len(margin_errors)
    refund_rate = sum(1 for m in metrics if m.get("refunded")) / max(len(metrics), 1)
    fulfill_times = [m.get("fulfillment_seconds") or 0 for m in completed]
    avg_fulfill = sum(fulfill_times) / len(fulfill_times) if fulfill_times else 0
    return {
        "ready": True,
        "avg_margin_error": avg_margin_error,
        "refund_rate": refund_rate,
        "avg_fulfillment_seconds": avg_fulfill,
        "sample_size": len(completed),
    }


def propose_changes(analysis: dict) -> list[dict]:
    proposals: list[dict] = []
    if not analysis.get("ready"):
        return proposals
    if analysis["avg_margin_error"] < -5:
        current = RESOURCE_COSTS_CENTS["nemotron_tokens_per_1k"]
        proposals.append({
            "type": "pricing",
            "key": "nemotron_tokens_per_1k",
            "from": current,
            "to": int(current * 1.05),
            "reason": f"avg margin error {analysis['avg_margin_error']:.1f}%",
        })
    if analysis["refund_rate"] > 0.10:
        proposals.append({
            "type": "guardrail",
            "key": "daily_budget_cents",
            "action": "reduce_10pct",
            "reason": f"refund rate {analysis['refund_rate']:.0%}",
        })
    if analysis["avg_fulfillment_seconds"] > 120:
        proposals.append({
            "type": "agent",
            "key": "max_tool_rounds",
            "action": "reduce_by_1",
            "reason": f"avg fulfillment {analysis['avg_fulfillment_seconds']:.0f}s",
        })
    return proposals


def apply_changes(proposals: list[dict]) -> list[dict]:
    applied: list[dict] = []
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    overrides = {}
    if OVERRIDES_PATH.is_file():
        try:
            overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            overrides = {}
    for p in proposals:
        if p["type"] == "pricing":
            overrides[p["key"]] = p["to"]
            applied.append(p)
    if overrides:
        OVERRIDES_PATH.write_text(json.dumps(overrides, indent=2) + "\n", encoding="utf-8")
    return applied


def log_improvement(entry: dict) -> None:
    IMPROVEMENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = time.time()
    with IMPROVEMENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def tune(treasury: Treasury | None = None, *, apply: bool = False) -> dict:
    treasury = treasury or Treasury()
    analysis = analyze_metrics(treasury)
    proposals = propose_changes(analysis)
    result = {"analysis": analysis, "proposals": proposals, "applied": []}
    if apply and proposals:
        result["applied"] = apply_changes(proposals)
        log_improvement({"action": "apply", "proposals": proposals})
    elif proposals:
        log_improvement({"action": "dry_run", "proposals": proposals})
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SOLVENT auto-improvement tuner")
    parser.add_argument("--apply", action="store_true", help="apply proposed changes")
    args = parser.parse_args()
    result = tune(apply=args.apply)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
