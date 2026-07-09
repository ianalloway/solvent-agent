"""
ledger.py — scrollable transaction timeline for the SOLVENT treasury.

Complements `solvent finance` (aggregate summaries) with a line-by-line
view of every revenue/expense/capital entry, newest first.

Usage:
    solvent ledger                  show last 20 entries
    solvent ledger -n 50            show last 50 entries
    solvent ledger --kind revenue   filter by kind (revenue|expense|capital)
    solvent ledger --job <id>       filter to a specific job ID (prefix match)
    solvent ledger --json           output raw JSON
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional

_KIND_EMOJI = {
    "revenue": "+",
    "expense": "-",
    "capital": "~",
}


def _fmt_ts(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:>8.2f}"


def _entry_to_dict(e) -> dict:
    return {
        "id": e.id,
        "ts": e.ts,
        "kind": e.kind,
        "amount_cents": e.amount_cents,
        "memo": e.memo,
        "job_id": e.job_id,
        "vendor": e.vendor,
        "stripe_ref": e.stripe_ref,
    }


def _human_line(e) -> str:
    sign = _KIND_EMOJI.get(e.kind, "?")
    ts = _fmt_ts(e.ts)
    amount = _fmt_cents(e.amount_cents)
    memo = (e.memo or "")[:50]
    job = f"[{e.job_id[:8]}]" if e.job_id else ""
    vendor = f"via {e.vendor}" if e.vendor else ""
    parts = [f"{ts}  {sign}{amount}  {memo}"]
    suffix = "  ".join(p for p in (job, vendor) if p)
    if suffix:
        parts.append(f"  {suffix}")
    return "".join(parts)


def show_ledger(
    treasury=None,
    *,
    n: int = 20,
    kind: Optional[str] = None,
    job: Optional[str] = None,
    as_json: bool = False,
) -> None:
    if treasury is None:
        from .treasury import Treasury
        treasury = Treasury()

    entries = list(treasury.entries)  # oldest first
    entries.reverse()                 # newest first

    # Filter
    if kind:
        entries = [e for e in entries if e.kind == kind]
    if job:
        entries = [e for e in entries if e.job_id and e.job_id.startswith(job)]

    entries = entries[:n]

    if not entries:
        print("(no ledger entries)")
        return

    if as_json:
        print(json.dumps([_entry_to_dict(e) for e in entries], indent=2, default=str))
        return

    # Running balance header
    all_entries = treasury.entries
    balance = sum(e.amount_cents if e.kind in ("revenue", "capital") else -e.amount_cents for e in all_entries)
    print(f"  Balance: ${balance / 100:.2f}  ({len(all_entries)} total entries)\n")

    for e in entries:
        print(_human_line(e))


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Show the SOLVENT treasury ledger (newest first).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-n", "--lines", type=int, default=20, metavar="N",
                   help="number of entries to show (default 20)")
    p.add_argument("--kind", choices=["revenue", "expense", "capital"],
                   help="filter by entry kind")
    p.add_argument("--job", metavar="ID",
                   help="filter to a specific job ID (prefix match)")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="output raw JSON")
    args = p.parse_args()

    show_ledger(n=args.lines, kind=args.kind, job=args.job, as_json=args.as_json)


if __name__ == "__main__":
    main()
