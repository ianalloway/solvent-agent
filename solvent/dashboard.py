"""
dashboard.py — renders the treasury to a premium, standalone HTML dashboard.

Includes:
- Dark-mode, glassmorphic UI styled with HSL colors
- High-fidelity SVG cumulative balance line chart
- Visual COGS breakdown metrics per vendor
- Dynamic, interactive job cards with colored state badges
- Slide-out details drawer showing job finances and markdown reports
- Simulated console terminal representing agent thoughts and Stripe events

Page data comes from ``dashboard_status``; static CSS/JS live in
``dashboard_assets``. This module assembles the page and writes it.
"""

from __future__ import annotations

import json
from pathlib import Path

from .dashboard_assets import DASHBOARD_CSS, DASHBOARD_JS, MODAL_CSS
from .dashboard_chat import CHAT_PANEL_CSS, CHAT_PANEL_HTML, LIVE_CLIENT_JS
from .dashboard_status import (  # noqa: F401 — re-exported for existing callers
    _ledger_from_snapshot,
    _script_json,
    build_status_data,
    generate_svg_chart,
    h,
    safe_href,
)
from .paths import dashboard_html, data_dir
from .treasury import fmt

OUT = dashboard_html()


def _financials_html(report: dict) -> str:
    """Render the finance report as an HTML fragment for the dashboard panel."""
    inc = report["income_statement"]
    ue = report["unit_economics"]
    rw = report["runway"]

    def row(label: str, value: str, cls: str = "") -> str:
        return (
            '<div class="vendor-metric"><div class="vendor-info">'
            f'<span class="vendor-name">{h(label)}</span>'
            f'<span class="vendor-amount {cls}">{value}</span>'
            "</div></div>"
        )

    net_cls = "green-txt" if inc["net_profit_cents"] >= 0 else "red-txt"
    parts = [
        row("Revenue", fmt(inc["revenue_cents"])),
        row("Operating cost", fmt(inc["operating_cost_cents"])),
        row(
            f"Net profit ({inc['net_margin_pct']}% margin)",
            fmt(inc["net_profit_cents"]),
            net_cls,
        ),
        row(
            "Avg profit / job",
            f"{fmt(ue['avg_profit_cents'])} ({ue['contribution_margin_pct']}%)",
        ),
    ]

    if rw["status"] == "cashflow_positive":
        runway_txt = f"Cash-flow positive (+{fmt(rw['daily_net_cents'])}/day)"
    elif rw["status"] == "burning":
        runway_txt = f"{rw['runway_days']} days (burning {fmt(-rw['daily_net_cents'])}/day)"
    elif rw["status"] == "insufficient_history":
        runway_txt = "Insufficient history"
    else:
        runway_txt = "No activity yet"
    parts.append(row("Runway", h(runway_txt)))

    fc = report.get("forecast")
    if fc and fc.get("status") == "ok":
        proj_cls = (
            "green-txt" if fc["projected_balance_cents"] >= fc["balance_cents"] else "red-txt"
        )
        parts.append(
            row(
                f"Projected balance ({fc['horizon_days']}d)",
                fmt(fc["projected_balance_cents"]),
                proj_cls,
            )
        )
        parts.append(
            row(
                "Forecast best / worst",
                f"{fmt(fc['best_cents'])} / {fmt(fc['worst_cents'])}",
            )
        )

    out = "".join(parts)

    trend = report.get("period_pnl") or []
    if trend:
        spark = report.get("balance_sparkline", "")
        out += (
            '<div style="margin-top:10px;font-size:12px;color:var(--color-text-muted)">'
            f"Net P&amp;L trend by {h(report.get('period', 'day'))} "
            f'<span style="letter-spacing:2px">{h(spark)}</span></div>'
        )
        for b in trend[-8:]:
            sign = "+" if b["net_cents"] >= 0 else "−"
            cls = "green-txt" if b["net_cents"] >= 0 else "red-txt"
            out += row(b["period"], f"{sign}{fmt(abs(b['net_cents']))}", cls)
    return out


def _metric_row(label: str, value: str, cls: str = "") -> str:
    """One label/value line in a breakdown panel. `value` must already be safe."""
    return (
        '<div class="vendor-metric"><div class="vendor-info">'
        f'<span class="vendor-name">{h(label)}</span>'
        f'<span class="vendor-amount {cls}">{value}</span>'
        "</div></div>"
    )


def _commercial_html(treasury=None) -> str:
    """Win rate, customers, deliverable quality and throughput, for the dashboard.

    The dashboard showed the money and the jobs; the commercial picture lived
    only in the CLI. This is the same data those commands read, best-effort:
    a panel is never worth failing a render over.
    """
    try:
        from .capacity import report as capacity_report
        from .customers import book_summary, customer_stats
        from .funnel import analyse
        from .quality import quality_report
        from .treasury import Treasury

        t = treasury or Treasury()
        parts: list[str] = []

        funnel = analyse(t)
        if not funnel["jobs"]:
            # Capacity still means something with an empty book — it is what the
            # shop *could* take — so the panel says both.
            parts.append("<p class='no-data-msg'>No orders yet.</p>")
        else:
            stages, rates = funnel["stages"], funnel["rates"]
            parts.append(
                _metric_row(
                    "Orders won",
                    f"{stages['delivered']} of {stages['submitted']} "
                    f"({h(_pct_txt(rates['end_to_end_pct']))})",
                )
            )
            parts.append(_metric_row("Gate acceptance", h(_pct_txt(rates["gate_acceptance_pct"]))))
            parts.append(
                _metric_row("Payment conversion", h(_pct_txt(rates["payment_conversion_pct"])))
            )
            if funnel["lost_cents"]:
                parts.append(_metric_row("Left on the table", fmt(funnel["lost_cents"]), "red-txt"))

        customers = customer_stats(t)
        if customers:
            book = book_summary(customers)
            parts.append(
                _metric_row(
                    "Customers",
                    f"{book['customers']} ({book['repeat_customers']} repeat)",
                )
            )
            best = customers[0]
            parts.append(
                _metric_row(
                    f"Top customer ({book['top_customer_share_pct']}% of revenue)",
                    h(str(best["email"])[:38]),
                )
            )

        quality = quality_report(t)
        if quality["briefs"]:
            failing = sum(n for grade, n in quality["grades"].items() if grade != "pass")
            cls = "green-txt" if not failing else "red-txt"
            parts.append(
                _metric_row(
                    f"Brief quality ({quality['briefs']} scored)",
                    f"{h(quality['mean_score'])}/100 mean",
                    cls,
                )
            )

        capacity = capacity_report(t)
        parts.append(
            _metric_row(
                "Throughput ceiling",
                f"{h(capacity['ceiling_jobs_per_day'])}/day — {h(capacity['binding_rule'])}",
            )
        )

        return "".join(parts)
    except Exception:
        return ""


def _pct_txt(value) -> str:
    return "—" if value is None else f"{value}%"


def render(snapshot: dict, log: list[dict], *, live: bool = False) -> Path:
    s = snapshot
    status_data = build_status_data(s, log)
    from .finance import build_report

    _entries = _ledger_from_snapshot(s.get("entries", []))
    financials_html = _financials_html(build_report(_entries))
    commercial_html = _commercial_html()
    chart_svg = status_data["chart_svg"]
    expense_breakdown_html = status_data["expense_breakdown_html"]
    ops_html = status_data["ops_html"]
    job_cards_html = status_data["job_cards_html"]
    console_log_html = status_data["console_log_html"]
    jobs_data = status_data["jobs_data"]
    briefs = status_data["briefs"]

    live_css = CHAT_PANEL_CSS if live else ""
    if live:
        feed_section = CHAT_PANEL_HTML.format(console_log_html=console_log_html)
        client_updates_js = LIVE_CLIENT_JS
    else:
        feed_section = f"""
  <div class="console-panel">
    <div class="console-header">
      <span class="console-title"><span class="console-dot"></span> AGENT TRANSACTION & LOG FEED</span>
      <span>SYSTEM: ACTIVE</span>
    </div>
    <div class="console-body" id="consoleBody">
      <div id="console-lines">{console_log_html}</div>
    </div>
  </div>"""
        client_updates_js = """
    async function pollStatus() {
      try {
        const res = await fetch('data/dashboard_status.json');
        if (!res.ok) throw new Error('Status fetch failed');
        const data = await res.json();
        const indicator = document.getElementById("status-indicator");
        if (indicator) indicator.innerHTML = '<span class="status-dot live"></span> Live';
        document.getElementById("header-seed-capital").innerText = "Seed Capital: " + formatCents(data.capital_cents);
        const growth = data.balance_cents - data.capital_cents;
        document.getElementById("header-net-growth-val").innerHTML = `${growth >= 0 ? '+' : ''}${formatCents(growth)}`;
        document.getElementById("header-net-growth-val").className = growth >= 0 ? 'green' : 'red';
        document.getElementById("metric-cash-balance").innerText = formatCents(data.balance_cents);
        updateRefundWarning(data);
        document.getElementById("metric-revenue").innerText = formatCents(data.revenue_cents);
        document.getElementById("metric-operating-spend").innerText = formatCents(data.expense_cents);
        const netProfit = data.net_profit_cents;
        const profitElem = document.getElementById("metric-net-profit");
        profitElem.innerText = (netProfit >= 0 ? '+' : '') + formatCents(netProfit);
        profitElem.className = 'val ' + (netProfit >= 0 ? 'green' : 'red');
        document.getElementById("metric-margin").innerText = data.margin_pct + '%';
        if (lastChartSVG !== data.chart_svg) {
          document.getElementById("chart-container").innerHTML = data.chart_svg;
          lastChartSVG = data.chart_svg;
        }
        if (lastExpenseHTML !== data.expense_breakdown_html) {
          document.getElementById("expense-breakdown").innerHTML = data.expense_breakdown_html;
          lastExpenseHTML = data.expense_breakdown_html;
        }
        if (lastJobsHTML !== data.job_cards_html) {
          document.getElementById("jobs-grid").innerHTML = data.job_cards_html;
          lastJobsHTML = data.job_cards_html;
        }
        if (lastConsoleHTML !== data.console_log_html) {
          document.getElementById("console-lines").innerHTML = data.console_log_html;
          lastConsoleHTML = data.console_log_html;
          scrollConsole();
        }
        jobsData = data.jobs_data;
        briefs = data.briefs;
        if (currentOpenJobId) updateDrawerContent(currentOpenJobId);
        if (typeof updateBriefsList === 'function') updateBriefsList();
      } catch (err) {
        const indicator = document.getElementById("status-indicator");
        if (indicator) indicator.innerHTML = '<span class="status-dot offline"></span> Reconnecting...';
      }
    }
    setInterval(pollStatus, 2000);"""

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SOLVENT — Autonomous Treasury</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
{DASHBOARD_CSS}
    {live_css}
{MODAL_CSS}
  </style>
</head>
<body>

  <header>
    <div>
      <h1>🪙 SOLVENT — Autonomous Treasury</h1>
      <p class="sub">A self-funding analyst agent · earns on Stripe · spends to provision resources · profit-gated loop</p>
    </div>
    <div class="header-pills">
      <div id="status-indicator" class="pill"><span class="status-dot live"></span> Live</div>
      <div id="header-seed-capital" class="pill">Seed Capital: {fmt(s["capital_cents"])}</div>
      <div id="header-net-growth" class="pill">Net Growth: <span class="green" id="header-net-growth-val">+{fmt(s["balance_cents"] - s["capital_cents"])}</span></div>
    </div>
  </header>

  <div id="refund-warning" class="metric-card red-txt"
       style="display: {"block" if status_data["pending_refund_cents"] else "none"}">
    Unresolved refunds: {fmt(status_data["pending_refund_cents"])}.
    Booked revenue/profit are not confirmed retained funds.
    Available cash: {fmt(status_data["available_balance_cents"])}. Run solvent reconcile.
  </div>

  <div class="metrics-grid">
    <div class="metric-card">
      <div class="label">Cash Balance</div>
      <div class="val" id="metric-cash-balance">{fmt(s["balance_cents"])}</div>
    </div>
    <div class="metric-card">
      <div class="label">Revenue</div>
      <div class="val green" id="metric-revenue">{fmt(s["revenue_cents"])}</div>
    </div>
    <div class="metric-card">
      <div class="label">Operating Spend</div>
      <div class="val red" id="metric-operating-spend">{fmt(s["expense_cents"])}</div>
    </div>
    <div class="metric-card">
      <div class="label">Net Profit</div>
      <div class="val {"green" if s["net_profit_cents"] >= 0 else "red"}" id="metric-net-profit">{fmt(s["net_profit_cents"])}</div>
    </div>
    <div class="metric-card">
      <div class="label">Total Margin</div>
      <div class="val" id="metric-margin">{s["margin_pct"]}%</div>
    </div>
  </div>

  <div class="dashboard-body">

    <div class="panel">
      <h2>Financial Performance <span style="font-size:11px;color:var(--color-text-muted);font-weight:normal">cumulative ledger balance</span></h2>
      <div class="chart-container" id="chart-container">
        {chart_svg}
      </div>

      <h2 style="margin-top:24px;border-top:1px solid var(--color-border);padding-top:16px">Resource Allocation Breakdown</h2>
      <div class="vendor-breakdown-list" id="expense-breakdown">
        {expense_breakdown_html}
      </div>
      <h2 style="margin-top:24px;border-top:1px solid var(--color-border);padding-top:16px">Operations</h2>
      <div class="vendor-breakdown-list" id="ops-panel">
        {ops_html}
      </div>

      <h2 style="margin-top:24px;border-top:1px solid var(--color-border);padding-top:16px">Financial Statement <span style="font-size:11px;color:var(--color-text-muted);font-weight:normal">income · unit economics · runway</span></h2>
      <div class="vendor-breakdown-list" id="financials-panel">
        {financials_html}
      </div>

      <h2 style="margin-top:24px;border-top:1px solid var(--color-border);padding-top:16px">Commercial <span style="font-size:11px;color:var(--color-text-muted);font-weight:normal">win rate · customers · quality · capacity</span></h2>
      <div class="vendor-breakdown-list" id="commercial-panel">
        {commercial_html}
      </div>

      <h2 style="margin-top:24px;border-top:1px solid var(--color-border);padding-top:16px">Delivered Research Briefs</h2>
      <div id="briefs-list-container" style="display:flex; flex-direction:column; gap:10px; max-height: 250px; overflow-y: auto;">
        <p class='no-data-msg'>No research briefs delivered yet.</p>
      </div>
    </div>

    <div class="panel">
      <h2>Inbound & Outbound Jobs</h2>
      <div class="jobs-list" id="jobs-grid">
        {job_cards_html}
      </div>
    </div>

  </div>

  {feed_section}

  <!-- Drawer Overlay & Container -->
  <div id="overlay" class="overlay" onclick="closeDrawer()"></div>
  <div id="drawer" class="drawer">
    <div class="drawer-header">
      <h2 id="drawer-title">Job Audit Details</h2>
      <button class="btn-close" onclick="closeDrawer()">&times;</button>
    </div>
    <div class="drawer-body">
      <div id="drawer-metadata"></div>
      <hr class="drawer-divider">
      <h3>Deliverable Report Brief</h3>
      <div id="drawer-brief" class="brief-container markdown-body"></div>
    </div>
  </div>

  <!-- Modal for Brief Live Preview -->
  <div id="brief-modal" class="modal" onclick="closeBriefModal()">
    <div class="modal-content" onclick="event.stopPropagation()">
      <div class="modal-header">
        <h3 id="modal-title">Research Brief Preview</h3>
        <button class="btn-close" onclick="closeBriefModal()">&times;</button>
      </div>
      <div class="modal-body">
        <iframe id="brief-iframe" src="about:blank"></iframe>
      </div>
    </div>
  </div>

  <script>
    let briefs = {_script_json(briefs)};
    let jobsData = {_script_json(jobs_data)};
{DASHBOARD_JS}
    {client_updates_js}
  </script>
</body>
</html>"""

    status_dir = data_dir()
    status_json_path = status_dir / "dashboard_status.json"
    status_json_path.write_text(json.dumps(status_data, indent=2))

    OUT.write_text(html)
    return OUT
