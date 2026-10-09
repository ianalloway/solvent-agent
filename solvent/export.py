"""
export.py — the books, in a form an accountant (or a spreadsheet) can read.

Everything the agent knows lives in SQLite, which is fine for the agent and
useless for a bookkeeper, a tax return, or a board pack. This writes the ledger,
the jobs, the per-job metrics, and the customer book out as CSV or JSON over a
chosen period, with a period-close summary computed from the same rows.

The period is closed on the ledger, not on the job table: revenue and cost are
counted when the money moved, which is what the summary has to tie out against.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from .customers import book_summary, customer_stats
from .treasury import REFUND_VENDOR, LedgerEntry, Treasury, fmt

_PERIOD_RE = re.compile(r"^(\d+)([dwmh])$")

_UNIT_SECONDS = {"h": 3_600, "d": 86_400, "w": 604_800, "m": 2_592_000}

LEDGER_COLUMNS = (
    "id",
    "ts",
    "iso_time",
    "kind",
    "amount_cents",
    "signed_cents",
    "memo",
    "job_id",
    "vendor",
    "stripe_ref",
    "stripe_session_id",
    "stripe_link_id",
)

JOB_COLUMNS = (
    "id",
    "status",
    "topic",
    "customer_email",
    "budget_cents",
    "created_at",
    "updated_at",
    "current_stage",
    "error_reason",
    "deliverable_url",
    "retry_count",
)

METRIC_COLUMNS = (
    "job_id",
    "est_cost_cents",
    "actual_cost_cents",
    "est_margin_pct",
    "actual_margin_pct",
    "margin_drift_cents",
    "fulfillment_seconds",
    "tool_calls",
    "refunded",
    "decline_reason",
    "block_rule",
    "ts",
)

CUSTOMER_COLUMNS = (
    "email",
    "jobs",
    "completed",
    "declined",
    "revenue_cents",
    "cogs_cents",
    "net_cents",
    "margin_pct",
    "avg_order_cents",
    "repeat",
)


def parse_period(value: str | None, *, now: float | None = None) -> float | None:
    """Turn `7d` / `24h` / `2w` / `3m` into an absolute timestamp.

    An ISO date (`2026-01-31`) works too. Returns ``None`` for no bound.
    """
    if not value:
        return None
    stamp = now if now is not None else time.time()
    match = _PERIOD_RE.match(value.strip().lower())
    if match:
        count, unit = match.groups()
        return stamp - int(count) * _UNIT_SECONDS[unit]
    try:
        return time.mktime(time.strptime(value.strip(), "%Y-%m-%d"))
    except ValueError as exc:
        raise ValueError(
            f"unrecognised period {value!r}: use 7d, 24h, 2w, 3m, or YYYY-MM-DD"
        ) from exc


def _iso(ts: float | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(ts)))


def _in_window(ts: float | None, since: float | None, until: float | None) -> bool:
    if ts is None:
        return since is None
    value = float(ts)
    if since is not None and value < since:
        return False
    if until is not None and value > until:
        return False
    return True


def ledger_rows(
    entries: Iterable[LedgerEntry],
    *,
    since: float | None = None,
    until: float | None = None,
) -> list[dict[str, Any]]:
    """Ledger entries in the window, flattened for CSV."""
    rows = []
    for entry in entries:
        if not _in_window(entry.ts, since, until):
            continue
        rows.append(
            {
                "id": entry.id,
                "ts": entry.ts,
                "iso_time": _iso(entry.ts),
                "kind": entry.kind,
                "amount_cents": entry.amount_cents,
                "signed_cents": entry.signed_cents(),
                "memo": entry.memo,
                "job_id": entry.job_id or "",
                "vendor": entry.vendor or "",
                "stripe_ref": entry.stripe_ref or "",
                "stripe_session_id": entry.stripe_session_id or "",
                "stripe_link_id": entry.stripe_link_id or "",
            }
        )
    return rows


def period_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Close the period on the ledger rows themselves, so it always ties out."""
    revenue = sum(r["amount_cents"] for r in rows if r["kind"] == "revenue")
    capital = sum(r["amount_cents"] for r in rows if r["kind"] == "capital")
    refunds = sum(
        r["amount_cents"] for r in rows if r["kind"] == "expense" and r["vendor"] == REFUND_VENDOR
    )
    cogs = sum(
        r["amount_cents"] for r in rows if r["kind"] == "expense" and r["vendor"] != REFUND_VENDOR
    )
    net = revenue - refunds - cogs
    by_vendor: dict[str, int] = {}
    for row in rows:
        if row["kind"] != "expense":
            continue
        by_vendor[row["vendor"] or "unattributed"] = (
            by_vendor.get(row["vendor"] or "unattributed", 0) + row["amount_cents"]
        )
    stamps = [r["ts"] for r in rows if r["ts"]]
    return {
        "entries": len(rows),
        "from": _iso(min(stamps)) if stamps else "",
        "to": _iso(max(stamps)) if stamps else "",
        "revenue_cents": revenue,
        "refunds_cents": refunds,
        "cogs_cents": cogs,
        "net_cents": net,
        "capital_in_cents": capital,
        "margin_pct": round(100 * net / revenue, 1) if revenue else 0.0,
        "expense_by_vendor": dict(sorted(by_vendor.items(), key=lambda kv: -kv[1])),
    }


def collect(
    treasury: Treasury | None = None,
    *,
    since: float | None = None,
    until: float | None = None,
) -> dict[str, Any]:
    """Every table the export writes, filtered to the window."""
    t = treasury or Treasury()
    ledger = ledger_rows(t.entries, since=since, until=until)
    job_ids = {row["job_id"] for row in ledger if row["job_id"]}

    jobs = [
        {col: job.get(col, "") for col in JOB_COLUMNS}
        for job in t.list_jobs()
        if _in_window(job.get("created_at"), since, until) or job.get("id") in job_ids
    ]
    kept = {job["id"] for job in jobs}
    metrics = [
        {col: metric.get(col, "") for col in METRIC_COLUMNS}
        for metric in t.list_metrics()
        if metric.get("job_id") in kept
    ]
    customers = [{col: row.get(col, "") for col in CUSTOMER_COLUMNS} for row in customer_stats(t)]

    return {
        "summary": period_summary(ledger),
        "book": book_summary(customer_stats(t)),
        "ledger": ledger,
        "jobs": jobs,
        "metrics": metrics,
        "customers": customers,
    }


_TABLES = {
    "ledger": LEDGER_COLUMNS,
    "jobs": JOB_COLUMNS,
    "metrics": METRIC_COLUMNS,
    "customers": CUSTOMER_COLUMNS,
}


#: Free-text columns that can carry customer- or vendor-supplied strings. Only
#: these are neutralised, so numeric columns stay numeric for the spreadsheet.
_UNTRUSTED_TEXT_COLUMNS = {
    "ledger": frozenset(
        {"memo", "job_id", "vendor", "stripe_ref", "stripe_session_id", "stripe_link_id"}
    ),
    "jobs": frozenset(
        {"id", "status", "topic", "customer_email", "current_stage", "error_reason", "deliverable_url"}
    ),
    "metrics": frozenset({"job_id", "decline_reason", "block_rule"}),
    "customers": frozenset({"email"}),
}

_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
_LEADING_NOISE = re.compile(r"^[\s\x00-\x1f\x7f\u200b-\u200f\u2060\ufeff]*")


def neutralise_cell(value: Any) -> Any:
    """Stop a spreadsheet from evaluating a text cell as a formula.

    A string whose first significant character (after any leading whitespace
    or control characters) is ``= + - @``, or that starts with a tab or
    carriage return, is prefixed with a single quote. Non-strings pass through.
    """
    if not isinstance(value, str) or not value:
        return value
    significant = _LEADING_NOISE.sub("", value, count=1)
    if value.startswith(_FORMULA_TRIGGERS) or significant.startswith(_FORMULA_TRIGGERS):
        return "'" + value
    return value


def write_csv(data: dict[str, Any], out_dir: Path) -> list[Path]:
    """Write one CSV per table; returns the files written.

    Untrusted text columns are passed through `neutralise_cell` so a job topic
    or memo like ``=HYPERLINK(...)`` is shown as text, not run as a formula.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for table, columns in _TABLES.items():
        path = out_dir / f"{table}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
            writer.writeheader()
            text_columns = _UNTRUSTED_TEXT_COLUMNS.get(table, frozenset())
            for row in data[table]:
                writer.writerow(
                    {
                        key: neutralise_cell(value) if key in text_columns else value
                        for key, value in row.items()
                    }
                )
        written.append(path)
    return written


def write_json(data: dict[str, Any], path: Path) -> Path:
    """Write the whole export as one JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return path


def format_summary(data: dict[str, Any], written: list[Path]) -> str:
    """Render the period close and what was written."""
    summary = data["summary"]
    lines = [
        "",
        "  PERIOD CLOSE",
        f"  {'─' * 66}",
        f"  Period               {summary['from'] or '—'} → {summary['to'] or '—'}",
        f"  Ledger entries       {summary['entries']}",
        "",
        f"  Revenue              {fmt(summary['revenue_cents'])}",
        f"  Refunds              {fmt(summary['refunds_cents'])}",
        f"  Cost of sales        {fmt(summary['cogs_cents'])}",
        f"  Net                  {fmt(summary['net_cents'])}  ({summary['margin_pct']}% margin)",
        f"  Capital introduced   {fmt(summary['capital_in_cents'])}",
    ]
    if summary["expense_by_vendor"]:
        lines += ["", "  Expense by vendor"]
        for vendor, cents in summary["expense_by_vendor"].items():
            lines.append(f"    {vendor:<24}{fmt(cents):>10}")
    lines += [
        "",
        f"  Jobs {len(data['jobs'])}  ·  metrics {len(data['metrics'])}  ·  "
        f"customers {len(data['customers'])}",
    ]
    if written:
        lines += ["", "  Written"]
        lines += [f"    {path}" for path in written]
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="solvent export",
        description="Export the ledger, jobs, metrics, and customer book for a period.",
    )
    parser.add_argument("--since", help="start of the period: 7d, 24h, 2w, 3m, or YYYY-MM-DD")
    parser.add_argument("--until", help="end of the period, same formats")
    parser.add_argument(
        "--format",
        choices=("csv", "json"),
        default="csv",
        help="csv writes one file per table; json writes a single document",
    )
    parser.add_argument(
        "--out",
        help="output directory (csv) or file (json); defaults to ./solvent-export",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print the JSON export instead of writing files",
    )
    args = parser.parse_args()

    try:
        since = parse_period(args.since)
        until = parse_period(args.until)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    data = collect(since=since, until=until)

    if args.stdout:
        print(json.dumps(data, indent=2, default=str))
        return

    if args.format == "json":
        target = Path(args.out or "solvent-export/export.json")
        written = [write_json(data, target)]
    else:
        written = write_csv(data, Path(args.out or "solvent-export"))

    print(format_summary(data, written))


if __name__ == "__main__":
    main()
