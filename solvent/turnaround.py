"""
turnaround.py — how long the shop actually takes, measured.

"When will I get it?" is the second question every customer asks, right after
the price, and the agent had no answer. Fulfilment time was recorded per job
(`job_metrics.fulfillment_seconds`) but that is only the model's part; what the
customer experiences is the wait from *paying* to *having the brief*, which
spans the queue, the worker's poll interval, retries and delivery.

That wait is already on the record — the ledger says when the money arrived,
the event log says when the brief went out — so this measures it rather than
estimating it, and turns the distribution into something quotable: a promise at
a chosen percentile, rounded up, which is the number a shop can put in writing
without lying.

It also replaces an arbitrary threshold. `alerts.py` called a paid job "stuck"
after two hours because two hours sounded reasonable; a shop that normally
delivers in forty seconds should not wait two hours to notice, and one that
takes a day should not cry wolf. With enough history, late is defined by the
shop's own p95.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from .treasury import Treasury

#: Below this many delivered jobs the distribution is noise, not a promise.
MIN_SAMPLES = 3

#: Never promise faster than this, however quick the sample was: the queue, the
#: payment webhook and the worker poll all have jitter the sample may have missed.
FLOOR_SECONDS = 60.0

#: The percentile a promise is made at. p90 means one order in ten may be late,
#: which is what "usually within" honestly means.
PROMISE_PERCENTILE = 0.90

#: The percentile used to decide a job is running late.
LATE_PERCENTILE = 0.95


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(round((len(ordered) - 1) * fraction)), len(ordered) - 1)
    return ordered[index]


def measure(treasury: Treasury | None = None) -> list[dict[str, Any]]:
    """Paid-to-delivered seconds for every delivered job, oldest first."""
    t = treasury or Treasury()

    paid_at: dict[str, float] = {}
    for entry in t.entries:
        if entry.kind == "revenue" and entry.job_id:
            # The first payment is when the clock starts.
            paid_at[entry.job_id] = min(paid_at.get(entry.job_id, entry.ts), entry.ts)

    delivered_at: dict[str, float] = {}
    for event in t.list_events(limit=5_000):
        if event.get("stage") != "delivered":
            continue
        job_id = event.get("job_id")
        stamp = float(event.get("ts") or 0)
        if job_id and stamp and job_id not in delivered_at:
            # Events come back newest first, so the first seen is the latest
            # delivery, and a redelivery is what the customer actually waited for.
            delivered_at[job_id] = stamp

    rows = []
    for job_id, start in paid_at.items():
        end = delivered_at.get(job_id)
        if end is None or end < start:
            continue
        rows.append(
            {
                "job_id": job_id,
                "paid_at": start,
                "delivered_at": end,
                "seconds": round(end - start, 2),
            }
        )
    rows.sort(key=lambda row: row["paid_at"])
    return rows


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The distribution of waits, in seconds."""
    seconds = [row["seconds"] for row in rows]
    if not seconds:
        return {"samples": 0, "p50": None, "p90": None, "p95": None, "max": None, "mean": None}
    return {
        "samples": len(seconds),
        "p50": round(_percentile(seconds, 0.5), 2),
        "p90": round(_percentile(seconds, 0.9), 2),
        "p95": round(_percentile(seconds, 0.95), 2),
        "max": round(max(seconds), 2),
        "mean": round(sum(seconds) / len(seconds), 2),
    }


def promise_seconds(
    rows: list[dict[str, Any]],
    *,
    percentile: float = PROMISE_PERCENTILE,
    min_samples: int = MIN_SAMPLES,
) -> float | None:
    """The turnaround the shop can quote, or ``None`` without enough history."""
    seconds = [row["seconds"] for row in rows]
    if len(seconds) < min_samples:
        return None
    return max(_percentile(seconds, percentile), FLOOR_SECONDS)


def humanise(seconds: float | None) -> str:
    """Round a duration up to something a customer would accept in writing."""
    if seconds is None:
        return "not enough history to promise a turnaround"
    if seconds <= 300:
        return "within 5 minutes"
    if seconds <= 3_600:
        return f"within {int(-(-seconds // 600) * 10)} minutes"
    if seconds <= 86_400:
        return f"within {int(-(-seconds // 3_600))} hour(s)"
    return f"within {int(-(-seconds // 86_400))} day(s)"


def late_threshold_seconds(
    treasury: Treasury | None = None,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> float | None:
    """When a paid-but-undelivered job should count as late, from history."""
    measured = rows if rows is not None else measure(treasury)
    if len(measured) < MIN_SAMPLES:
        return None
    # Twice the p95 is the slack that keeps a normal slow job from paging anyone.
    return max(
        _percentile([row["seconds"] for row in measured], LATE_PERCENTILE) * 2, FLOOR_SECONDS
    )


def breaches(rows: list[dict[str, Any]], promise: float | None) -> list[dict[str, Any]]:
    """Delivered jobs that took longer than the promise."""
    if promise is None:
        return []
    return [row for row in rows if row["seconds"] > promise]


def report(
    treasury: Treasury | None = None, *, percentile: float = PROMISE_PERCENTILE
) -> dict[str, Any]:
    """Everything `solvent turnaround` prints."""
    t = treasury or Treasury()
    rows = measure(t)
    promise = promise_seconds(rows, percentile=percentile)
    missed = breaches(rows, promise)
    return {
        "stats": stats(rows),
        "percentile": percentile,
        "promise_seconds": promise,
        "promise": humanise(promise),
        "late_after_seconds": late_threshold_seconds(rows=rows),
        "breaches": len(missed),
        "breach_pct": round(100 * len(missed) / len(rows), 1) if rows else 0.0,
        "slowest": sorted(rows, key=lambda row: -row["seconds"])[:5],
    }


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.1f}s"
    if seconds < 5_400:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3_600:.1f}h"


def format_turnaround(data: dict[str, Any]) -> str:
    """Render the turnaround report for a terminal."""
    figures = data["stats"]
    lines = ["", "  TURNAROUND — paid to delivered", f"  {'─' * 66}"]
    if not figures["samples"]:
        lines += ["  No delivered jobs yet, so nothing to measure.", ""]
        return "\n".join(lines)

    lines += [
        f"  Delivered jobs       {figures['samples']}",
        f"  Median               {_duration(figures['p50'])}",
        f"  p90 / p95            {_duration(figures['p90'])} / {_duration(figures['p95'])}",
        f"  Slowest              {_duration(figures['max'])}",
        "",
        f"  Quotable promise     {data['promise']}  (p{round(data['percentile'] * 100)})",
        f"  Late after           {_duration(data['late_after_seconds'])}",
        f"  Missed the promise   {data['breaches']} ({data['breach_pct']}%)",
    ]
    if data["slowest"]:
        lines += ["", f"  {'JOB':<14}{'WAIT':>10}"]
        for row in data["slowest"]:
            lines.append(f"  {str(row['job_id'])[:13]:<14}{_duration(row['seconds']):>10}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent turnaround",
        description="Measured paid-to-delivered time, and the promise it supports.",
    )
    parser.add_argument(
        "--percentile",
        type=float,
        default=PROMISE_PERCENTILE * 100,
        help="percentile to promise at (default 90)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="output as JSON")
    args = parser.parse_args()

    fraction = min(max(args.percentile / 100, 0.0), 1.0)
    data = report(percentile=fraction)
    if args.as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_turnaround(data))


if __name__ == "__main__":
    main()
