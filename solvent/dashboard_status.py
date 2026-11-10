"""Status data for the treasury dashboard: the JSON the page and SSE feed share.

``build_status_data`` turns a treasury snapshot and the event log into the
fragments ``dashboard.render`` places on the page (chart, job cards, console,
ops panel), plus escaping helpers for anything that reaches HTML or a script.
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .paths import reports_dir
from .treasury import fmt


def h(value: object) -> str:
    """HTML-escape dashboard values before interpolating into fragments."""
    return html.escape(str(value), quote=True)


def _script_json(value: object) -> str:
    """JSON-encode for an inline <script> block without allowing tag breakout."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def safe_href(value: object) -> str:
    """Return an escaped HTTP(S) URL or a harmless placeholder."""
    url = str(value)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "#"
    return h(url)


def generate_svg_chart(points: list[int]) -> str:
    """Generate an inline, styled SVG path representing running balance."""
    if not points:
        return ""

    N = len(points)
    width = 600
    height = 140
    min_v = min(points)
    max_v = max(points)

    # Breathing room (padding)
    val_range = max_v - min_v
    padding = val_range * 0.15 if val_range != 0 else 1000
    if padding == 0:
        padding = 1000
    bottom_limit = min_v - padding
    top_limit = max_v + padding
    v_range = top_limit - bottom_limit

    coords = []
    for i, val in enumerate(points):
        x = i * (width / (N - 1)) if N > 1 else width / 2
        # Invert Y coordinate since SVG y=0 is top
        y = (height - 15) - ((val - bottom_limit) / v_range * (height - 30))
        coords.append((x, y))

    path_data = " ".join(
        f"{'M' if i == 0 else 'L'} {x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords)
    )
    area_data = (
        f"M {coords[0][0]:.1f},{height:.1f} "
        + " ".join(f"L {x:.1f},{y:.1f}" for x, y in coords)
        + f" L {coords[-1][0]:.1f},{height:.1f} Z"
    )

    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" class="chart-dot" data-val="{p}" onclick="alert(\'Balance: \' + formatCents({p}))"><title>Balance: {fmt(p)}</title></circle>'
        for p, (x, y) in zip(points, coords)
    )

    # Subtle dashed guidelines in the background
    guidelines = ""
    for h_pct in [0.25, 0.5, 0.75]:
        y_g = 15 + h_pct * (height - 30)
        guidelines += f'<line x1="0" y1="{y_g:.1f}" x2="{width}" y2="{y_g:.1f}" stroke="var(--color-border)" stroke-width="0.8" stroke-dasharray="4,4" opacity="0.3" />'

    svg = f"""
    <svg viewBox="0 0 {width} {height}" class="line-chart" preserveAspectRatio="none">
      <defs>
        <linearGradient id="chart-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--color-primary)" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="var(--color-primary)" stop-opacity="0.0"/>
        </linearGradient>
      </defs>
      {guidelines}
      <path d="{area_data}" fill="url(#chart-grad)" />
      <path d="{path_data}" fill="none" stroke="var(--color-primary)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
      {dots}
    </svg>
    """
    return svg


def build_status_data(snapshot: dict, log: list[dict]) -> dict:
    s = snapshot

    # 1. List generated briefs without loading their private contents.
    # Full reports are served only by token-protected delivery routes.
    briefs = {}
    reports_path = reports_dir()
    if reports_path.exists():
        for p in reports_path.glob("*.md"):
            briefs[p.stem] = ""

    # 2. Build cumulative running balance points
    running_balance = s["capital_cents"]
    balance_points = [running_balance]
    for e in s["entries"]:
        if e["kind"] == "capital":
            continue
        if e["kind"] == "revenue":
            running_balance += e["amount_cents"]
        elif e["kind"] == "expense":
            running_balance -= e["amount_cents"]
        balance_points.append(running_balance)

    chart_svg = generate_svg_chart(balance_points)

    # 3. Calculate vendor expenses dynamically
    vendor_spends: dict[str, int] = {}
    total_expense = 0
    for e in s["entries"]:
        if e["kind"] == "expense":
            vendor = e.get("vendor") or "unknown"
            amount = e["amount_cents"]
            vendor_spends[vendor] = vendor_spends.get(vendor, 0) + amount
            total_expense += amount

    sorted_vendors = sorted(vendor_spends.items(), key=lambda x: x[1], reverse=True)
    expense_breakdown_html = ""
    for vendor, amt in sorted_vendors:
        pct = (amt / total_expense * 100) if total_expense > 0 else 0
        expense_breakdown_html += f"""
        <div class="vendor-metric">
          <div class="vendor-info">
            <span class="vendor-name">{h(vendor)}</span>
            <span class="vendor-amount">{fmt(amt)} ({pct:.1f}%)</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" style="width: {pct}%"></div>
          </div>
        </div>
        """
    if not expense_breakdown_html:
        expense_breakdown_html = "<p class='no-data-msg'>No vendor expenses logged yet.</p>"

    # 3b. Ops: stuck jobs and margin drift from treasury metrics
    ops_html = ""
    try:
        from .treasury import Treasury

        t = Treasury()
        stuck = [
            j
            for j in t.list_jobs()
            if j.get("status") in ("awaiting_payment", "in_progress", "paid_pending_fulfill")
        ]
        metrics = t.list_metrics()
        drift_rows = [
            m
            for m in metrics
            if m.get("margin_drift_cents") is not None and abs(m.get("margin_drift_cents", 0)) > 0
        ]
        if stuck:
            ops_html += "<h3>Stuck jobs</h3><ul>"
            for j in stuck[:10]:
                ops_html += (
                    f"<li>{h(j.get('id', ''))}: {h(j.get('status'))} — "
                    f"{h(str(j.get('topic') or '')[:40])}</li>"
                )
            ops_html += "</ul>"
        if drift_rows:
            ops_html += "<h3>Margin drift</h3><ul>"
            for m in drift_rows[-10:]:
                ops_html += (
                    f"<li>{h(m.get('job_id', ''))}: est {h(m.get('est_margin_pct'))}% → "
                    f"actual {h(m.get('actual_margin_pct'))}% "
                    f"(drift {h(m.get('margin_drift_cents'))}c)</li>"
                )
            ops_html += "</ul>"
        if not ops_html:
            ops_html = "<p class='no-data-msg'>No stuck jobs or margin drift.</p>"
    except Exception:
        ops_html = ""

    # 4. Group details by job for card listing
    from collections import defaultdict

    jobs_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "id": "",
            "title": "",
            "status": "pending",
            "price": 0,
            "est_cost": 0,
            "margin_pct": 0.0,
            "reason": "",
            "invoice_url": "",
            "tokens": 0,
            "deliverable": "",
            "expenses": [],
            "pnl": 0,
            "counter_offer": "",
        }
    )

    for e in log:
        jid = e.get("job_id")
        if not jid:
            continue
        jobs_data[jid]["id"] = jid
        st = e["stage"]
        if st == "quote":
            jobs_data[jid]["title"] = e["title"]
            jobs_data[jid]["price"] = e["price"]
            jobs_data[jid]["est_cost"] = e["est_cost"]
            jobs_data[jid]["margin_pct"] = e["margin_pct"]
            if e["accept"]:
                jobs_data[jid]["status"] = "accepted"
            else:
                jobs_data[jid]["status"] = "declined"
                jobs_data[jid]["reason"] = e["reason"]
        elif st == "declined":
            jobs_data[jid]["status"] = "declined"
            jobs_data[jid]["reason"] = e["reason"]
        elif st == "counter_offer":
            jobs_data[jid]["counter_offer"] = e.get("message", "")
        elif st == "invoice":
            jobs_data[jid]["status"] = "awaiting_payment"
            jobs_data[jid]["invoice_url"] = e["url"]
        elif st == "paid":
            jobs_data[jid]["status"] = "paid"
        elif st == "fulfilled":
            jobs_data[jid]["status"] = "fulfilled"
            jobs_data[jid]["deliverable"] = e["deliverable"]
            jobs_data[jid]["tokens"] = e["tokens"]
        elif st == "spend":
            jobs_data[jid]["expenses"].append(
                {"vendor": e["vendor"], "amount": e["amount"], "memo": e["memo"]}
            )
        elif st == "booked":
            jobs_data[jid]["status"] = e.get("status", "completed")
            jobs_data[jid]["pnl"] = e["job_pnl"]

    job_cards_html = ""
    for jid, job in sorted(jobs_data.items()):
        if not job["id"]:
            continue

        status_class = h(job["status"])
        status_label = h(job["status"].replace("_", " ").upper())
        jid_text = h(jid)
        jid_js_arg = h(json.dumps(str(jid)))
        job_title = h(job["title"] or "No Topic Provided")

        expense_total = sum(x["amount"] for x in job["expenses"])
        fin_pnl = job["price"] - expense_total if job["status"] == "completed" else 0
        margin_str = f"{h(job['margin_pct'])}%" if job["margin_pct"] else "N/A"

        btn_html = ""
        if job["status"] == "completed":
            btn_html = f"""<button class="btn btn-primary btn-sm" onclick="openBriefModal({jid_js_arg})">View Brief</button>"""
        elif job["status"] == "declined":
            btn_html = f"""<span class="decline-label">Declined: {h(job["reason"])}</span>"""
            if job["counter_offer"]:
                btn_html += (
                    f"""<span class="decline-label">💡 Counter-offer: """
                    f"""{h(job["counter_offer"])}</span>"""
                )
        elif job["status"] == "awaiting_payment":
            btn_html = f"""<a href="{safe_href(job["invoice_url"])}" target="_blank" rel="noopener noreferrer" class="btn btn-outline btn-sm">Pay Invoice</a>"""

        job_cards_html += f"""
        <div class="job-card status-{status_class}">
          <div class="job-card-header">
            <span class="job-id">{jid_text}</span>
            <span class="badge badge-{status_class}">{status_label}</span>
          </div>
          <h3 class="job-title">{job_title}</h3>

          <div class="job-metrics">
            <div class="job-metric-item">
              <span class="job-metric-lbl">Budget</span>
              <span class="job-metric-val">{fmt(job["price"])}</span>
            </div>
            <div class="job-metric-item">
              <span class="job-metric-lbl">COGS</span>
              <span class="job-metric-val">{fmt(expense_total)}</span>
            </div>
            <div class="job-metric-item">
              <span class="job-metric-lbl">P&L</span>
              <span class="job-metric-val {"green" if fin_pnl >= 0 else "red"}">{"+" if fin_pnl >= 0 else ""}{fmt(fin_pnl)}</span>
            </div>
            <div class="job-metric-item">
              <span class="job-metric-lbl">Margin</span>
              <span class="job-metric-val">{margin_str}</span>
            </div>
          </div>

          <div class="job-card-footer">
            {btn_html}
          </div>
        </div>
        """

    # 5. Render scrolling console terminal simulation
    console_log_html = ""
    for e in log:
        ts_str = time.strftime("%H:%M:%S", time.localtime(e.get("ts", time.time())))
        st = e["stage"]
        job_ref = (
            f"<span class='console-job-id'>[{h(e.get('job_id'))}]</span>" if e.get("job_id") else ""
        )

        if st == "quote":
            verdict = (
                "<span class='green-txt'>ACCEPT</span>"
                if e["accept"]
                else "<span class='red-txt'>DECLINE</span>"
            )
            msg = f'Quoting topic: "{h(e["title"])}" | Price: {fmt(e["price"])} | Est. Cost: {fmt(e["est_cost"])} | Margin: {h(e["margin_pct"])}% → {verdict}'
        elif st == "declined":
            msg = f"✋ Declined job. Reason: <span class='grey-txt'>{h(e['reason'])}</span>"
        elif st == "invoice":
            tag = "simulated" if e["simulated"] else "live"
            url = safe_href(e["url"])
            msg = f"📄 Issued Stripe Payment Link for {fmt(e['amount'])} ({tag}) → <a href='{url}' target='_blank' rel='noopener noreferrer' class='console-link'>{url}</a>"
        elif st == "paid":
            msg = f"💵 Stripe payment confirmed: <span class='green-txt'>+{fmt(e['amount'])}</span> received from client"
        elif st == "fulfilled":
            msg = f"📝 Job fulfilled. Deliverable written to <span class='filepath-txt'>{h(Path(e['deliverable']).name)}</span> ({h(e['tokens'])} tokens)"
        elif st == "spend":
            msg = f"↳ Paid vendor <span class='vendor-badge'>{h(e['vendor'])}</span>: <span class='red-txt'>−{fmt(e['amount'])}</span> ({h(e['memo'])})"
        elif st == "spend_blocked":
            _why = e.get("reason") or e.get("rule")
            _why_txt = f" — {h(_why)}" if _why else ""
            msg = f"🛑 Spend BLOCKED by guardrail: {fmt(e['amount'])} to {h(e['vendor'])} ({h(e['memo'])}){_why_txt}"
        elif st == "counter_offer":
            msg = (
                f"💡 Counter-offer: {h(e.get('message', ''))} "
                f"(<span class='green-txt'>{h(e.get('margin_pct'))}%</span> margin)"
            )
        elif st == "refunded":
            msg = f"↩️ Escrow Refunded: Returned <span class='red-txt'>{fmt(e['amount'])}</span> to customer ({h(e['reason'])})"
        elif st == "booked":
            pnl_color = "green-txt" if e["job_pnl"] >= 0 else "red-txt"
            pnl_sign = "+" if e["job_pnl"] >= 0 else ""
            msg = f"✓ Booked P&L. Job net: <span class='{pnl_color}'>{pnl_sign}{fmt(e['job_pnl'])}</span> | Current Capital Balance: <span class='gold-txt'>{fmt(e['balance'])}</span>"
        else:
            msg = h(e)

        console_log_html += f"""
        <div class="console-line">
          <span class="console-time">{ts_str}</span>
          {job_ref}
          <span class="console-msg">{msg}</span>
        </div>
        """

    return {
        "balance_cents": s["balance_cents"],
        "pending_refund_cents": sum(r["amount_cents"] for r in s.get("pending_refunds", [])),
        "available_balance_cents": s.get("available_balance_cents", s["balance_cents"]),
        "capital_cents": s["capital_cents"],
        "revenue_cents": s["revenue_cents"],
        "expense_cents": s["expense_cents"],
        "net_profit_cents": s["net_profit_cents"],
        "margin_pct": s["margin_pct"],
        "chart_svg": chart_svg,
        "expense_breakdown_html": expense_breakdown_html,
        "ops_html": ops_html,
        "job_cards_html": job_cards_html,
        "console_log_html": console_log_html,
        "jobs_data": dict(jobs_data),
        "briefs": briefs,
    }


def _ledger_from_snapshot(entries: list[dict]) -> list:
    """Rebuild LedgerEntry objects from snapshot dicts, tolerating partial rows."""
    from .treasury import LedgerEntry

    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        try:
            out.append(
                LedgerEntry(
                    kind=e.get("kind", "expense"),
                    amount_cents=int(e.get("amount_cents", 0) or 0),
                    memo=str(e.get("memo", "")),
                    job_id=e.get("job_id"),
                    vendor=e.get("vendor"),
                    ts=float(e.get("ts", 0) or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return out
