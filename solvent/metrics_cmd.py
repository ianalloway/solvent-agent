"""
metrics_cmd.py — per-job performance metrics for SOLVENT.

Shows cost accuracy, margin drift, fulfillment speed, and tool usage
from the treasury job_metrics table.

Usage:
    solvent metrics                         show last 20 job metrics
    solvent metrics -n 50                   show last 50
    solvent metrics --job <id>              show metrics for one job
    solvent metrics --slow                  only jobs taking > 30s
    solvent metrics --drifted               only jobs with cost overrun > 15%
    solvent metrics --json                  output raw JSON
    solvent metrics --summary               aggregate stats (avg margin, speed)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional


def _fmt_ts(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def _pct(value) -> str:
    if value is None:
        return "  n/a"
    return f"{value:>5.1f}%"


def _cents(value) -> str:
    if value is None:
        return "  n/a  "
    return f"${value / 100:>6.2f}"


def _secs(value) -> str:
    if value is None:
        return "   n/a"
    return f"{value:>5.1f}s"


def _human_line(m: dict) -> str:
    job = (m.get("job_id") or "")[:12]
    ts = _fmt_ts(m["ts"])
    est_margin = _pct(m.get("est_margin_pct"))
    act_margin = _pct(m.get("actual_margin_pct"))
    drift = m.get("margin_drift_cents")
    drift_str = f"drift {_cents(drift)}" if drift is not None else ""
    speed = _secs(m.get("fulfillment_seconds"))
    tools = m.get("tool_calls")
    tools_str = f"{tools}t" if tools is not None else ""
    flags = []
    if m.get("decline_reason"):
        flags.append("DECLINED")
    if m.get("block_rule"):
        flags.append("BLOCKED")
    if m.get("refunded"):
        flags.append("REFUNDED")
    flag_str = "  [" + ",".join(flags) + "]" if flags else ""
    return (
        f"{ts}  [{job:<12}]  "
        f"est {est_margin}  act {act_margin}  {drift_str:<18}  "
        f"{speed}  {tools_str:<4}{flag_str}"
    )


def _summary(metrics: list[dict]) -> dict:
    completed = [m for m in metrics if m.get("actual_margin_pct") is not None]
    n = len(completed)
    declined = sum(1 for m in metrics if m.get("decline_reason"))
    blocked = sum(1 for m in metrics if m.get("block_rule"))
    refunded = sum(1 for m in metrics if m.get("refunded"))

    base = {
        "total": len(metrics),
        "completed": n,
        "declined": declined,
        "blocked": blocked,
        "refunded": refunded,
    }
    if not n:
        return base

    avg_margin = sum(m["actual_margin_pct"] for m in completed) / n
    speeds = [m["fulfillment_seconds"] for m in completed if m.get("fulfillment_seconds") is not None]
    avg_speed = sum(speeds) / len(speeds) if speeds else None
    drifted = [m for m in completed
               if m.get("margin_drift_cents") is not None
               and m.get("est_cost_cents")
               and abs(m["margin_drift_cents"]) > m["est_cost_cents"] * 0.15]

    base.update(
        avg_actual_margin_pct=round(avg_margin, 1),
        avg_fulfillment_seconds=round(avg_speed, 1) if avg_speed is not None else None,
        cost_overruns=len(drifted),
    )
    return base


def show_metrics(
    treasury=None,
    *,
    n: int = 20,
    job: Optional[str] = None,
    slow_threshold: Optional[float] = None,
    drifted_only: bool = False,
    as_json: bool = False,
    summary: bool = False,
) -> None:
    if treasury is None:
        from .treasury import Treasury
        treasury = Treasury()

    if job:
        m = treasury.get_metrics(job)
        rows = [m] if m else []
    else:
        rows = list(treasury.list_metrics())
        rows.reverse()  # newest first

    # Filters
    if slow_threshold is not None:
        rows = [r for r in rows if (r.get("fulfillment_seconds") or 0) >= slow_threshold]
    if drifted_only:
        rows = [
            r for r in rows
            if r.get("margin_drift_cents") is not None
            and r.get("est_cost_cents")
            and abs(r["margin_drift_cents"]) > r["est_cost_cents"] * 0.15
        ]

    rows = rows[:n]

    if summary:
        s = _summary(rows if rows else list(treasury.list_metrics()))
        if as_json:
            print(json.dumps(s, indent=2))
        else:
            print("Job metrics summary")
            print(f"  Total jobs        {s['total']}")
            print(f"  Completed         {s['completed']}")
            if s.get("avg_actual_margin_pct") is not None:
                print(f"  Avg actual margin {s['avg_actual_margin_pct']}%")
            if s.get("avg_fulfillment_seconds") is not None:
                print(f"  Avg fulfillment   {s['avg_fulfillment_seconds']}s")
            print(f"  Cost overruns     {s.get('cost_overruns', 0)}")
            print(f"  Declined          {s.get('declined', 0)}")
            print(f"  Blocked           {s.get('blocked', 0)}")
            print(f"  Refunded          {s.get('refunded', 0)}")
        return

    if not rows:
        print("(no job metrics)")
        return

    if as_json:
        print(json.dumps(rows, indent=2, default=str))
        return

    print(f"  {len(rows)} metric(s) shown (newest first)\n")
    for m in rows:
        print(_human_line(m))


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Show per-job performance metrics (cost accuracy, speed, margin).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-n", "--lines", type=int, default=20, metavar="N",
                   help="number of rows to show (default 20)")
    p.add_argument("--job", metavar="JOB_ID",
                   help="show metrics for a specific job")
    p.add_argument("--slow", dest="slow", type=float, nargs="?", const=30.0, metavar="SECS",
                   help="only jobs taking >= SECS seconds (default 30)")
    p.add_argument("--drifted", dest="drifted", action="store_true",
                   help="only jobs where actual cost exceeded estimate by > 15%%")
    p.add_argument("--summary", action="store_true",
                   help="show aggregate stats instead of per-row lines")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="output raw JSON")
    args = p.parse_args()

    show_metrics(
        n=args.lines,
        job=args.job,
        slow_threshold=args.slow,
        drifted_only=args.drifted,
        as_json=args.as_json,
        summary=args.summary,
    )


if __name__ == "__main__":
    main()
