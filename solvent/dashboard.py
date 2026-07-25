"""
dashboard.py — renders the treasury to a premium, standalone HTML dashboard.

Includes:
- Dark-mode, glassmorphic UI styled with HSL colors
- High-fidelity SVG cumulative balance line chart
- Visual COGS breakdown metrics per vendor
- Dynamic, interactive job cards with colored state badges
- Slide-out details drawer showing job finances and markdown reports
- Simulated console terminal representing agent thoughts and Stripe events
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path
from urllib.parse import urlparse
from .treasury import fmt
from .dashboard_chat import CHAT_PANEL_CSS, CHAT_PANEL_HTML, LIVE_CLIENT_JS

OUT = Path(__file__).resolve().parent.parent / "treasury_dashboard.html"


def h(value: object) -> str:
    """HTML-escape dashboard values before interpolating into fragments."""
    return html.escape(str(value), quote=True)


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
        
    path_data = " ".join(f"{'M' if i == 0 else 'L'} {x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords))
    area_data = f"M {coords[0][0]:.1f},{height:.1f} " + " ".join(f"L {x:.1f},{y:.1f}" for x, y in coords) + f" L {coords[-1][0]:.1f},{height:.1f} Z"
    
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" class="chart-dot" data-val="{p}" onclick="alert(\'Balance: \' + formatCents({p}))"><title>Balance: {fmt(p)}</title></circle>' for p, (x, y) in zip(points, coords))
    
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
    
    # 1. Read generated briefs from reports folder
    briefs = {}
    reports_dir = Path(__file__).resolve().parent.parent / "data" / "reports"
    if reports_dir.exists():
        for p in reports_dir.glob("*.md"):
            try:
                briefs[p.stem] = p.read_text()
            except Exception:
                pass

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
    vendor_spends = {}
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
        stuck = [j for j in t.list_jobs() if j.get("status") in ("awaiting_payment", "in_progress", "paid_pending_fulfill")]
        metrics = t.list_metrics()
        drift_rows = [
            m for m in metrics
            if m.get("margin_drift_cents") is not None and abs(m.get("margin_drift_cents", 0)) > 0
        ]
        if stuck:
            ops_html += "<h3>Stuck jobs</h3><ul>"
            for j in stuck[:10]:
                ops_html += f"<li>{j['id']}: {j.get('status')} — {j.get('topic', '')[:40]}</li>"
            ops_html += "</ul>"
        if drift_rows:
            ops_html += "<h3>Margin drift</h3><ul>"
            for m in drift_rows[-10:]:
                ops_html += (
                    f"<li>{m['job_id']}: est {m.get('est_margin_pct')}% → "
                    f"actual {m.get('actual_margin_pct')}% (drift {m.get('margin_drift_cents')}c)</li>"
                )
            ops_html += "</ul>"
        if not ops_html:
            ops_html = "<p class='no-data-msg'>No stuck jobs or margin drift.</p>"
    except Exception:
        ops_html = ""

    # 4. Group details by job for card listing
    from collections import defaultdict
    jobs_data = defaultdict(lambda: {
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
        "pnl": 0
    })
    
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
            jobs_data[jid]["expenses"].append({
                "vendor": e["vendor"],
                "amount": e["amount"],
                "memo": e["memo"]
            })
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
        margin_str = f"{job['margin_pct']}%" if job["margin_pct"] else "N/A"
        
        btn_html = ""
        if job["status"] == "completed":
            btn_html = f"""<button class="btn btn-primary btn-sm" onclick="openBriefModal({jid_js_arg})">View Brief</button>"""
        elif job["status"] == "declined":
            btn_html = f"""<span class="decline-label">Declined: {h(job['reason'])}</span>"""
        elif job["status"] == "awaiting_payment":
            btn_html = f"""<a href="{safe_href(job['invoice_url'])}" target="_blank" rel="noopener noreferrer" class="btn btn-outline btn-sm">Pay Invoice</a>"""
            
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
              <span class="job-metric-val">{fmt(job['price'])}</span>
            </div>
            <div class="job-metric-item">
              <span class="job-metric-lbl">COGS</span>
              <span class="job-metric-val">{fmt(expense_total)}</span>
            </div>
            <div class="job-metric-item">
              <span class="job-metric-lbl">P&L</span>
              <span class="job-metric-val {'green' if fin_pnl >= 0 else 'red'}">{'+' if fin_pnl >= 0 else ''}{fmt(fin_pnl)}</span>
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
        job_ref = f"<span class='console-job-id'>[{h(e.get('job_id'))}]</span>" if e.get("job_id") else ""
        
        if st == "quote":
            verdict = "<span class='green-txt'>ACCEPT</span>" if e["accept"] else "<span class='red-txt'>DECLINE</span>"
            msg = f"Quoting topic: \"{h(e['title'])}\" | Price: {fmt(e['price'])} | Est. Cost: {fmt(e['est_cost'])} | Margin: {h(e['margin_pct'])}% → {verdict}"
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
            msg = f"🛑 Spend BLOCKED by guardrail: {fmt(e['amount'])} to {h(e['vendor'])} ({h(e['memo'])})"
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


def render(snapshot: dict, log: list[dict], *, live: bool = False) -> Path:
    s = snapshot
    status_data = build_status_data(s, log)
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
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

    :root {{
      --color-bg: #070a0e;
      --color-surface: #0f141c;
      --color-border: #1c2430;
      --color-text-primary: #f3f4f6;
      --color-text-muted: #9ca3af;
      --color-primary: #6366f1;
      --color-primary-glow: rgba(99, 102, 241, 0.15);
      --color-success: #10b981;
      --color-success-glow: rgba(16, 185, 129, 0.15);
      --color-danger: #f43f5e;
      --color-danger-glow: rgba(244, 63, 94, 0.15);
      --color-warning: #fbbf24;
      --font-display: 'Plus Jakarta Sans', sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      font-family: var(--font-display);
      background: var(--color-bg);
      color: var(--color-text-primary);
      padding: 24px;
      line-height: 1.5;
    }}

    a {{
      color: var(--color-primary);
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}

    /* Layout structure */
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--color-border);
    }}

    header h1 {{
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.5px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    header p.sub {{
      color: var(--color-text-muted);
      font-size: 13px;
      margin-top: 4px;
    }}

    .header-pills {{
      display: flex;
      gap: 12px;
    }}

    .pill {{
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 500;
      color: var(--color-text-muted);
    }}

    /* Card Metrics Grid */
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}

    .metric-card {{
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
      transition: transform 0.2s ease, border-color 0.2s ease;
    }}

    .metric-card:hover {{
      transform: translateY(-2px);
      border-color: var(--color-primary);
    }}

    .metric-card .label {{
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--color-text-muted);
    }}

    .metric-card .val {{
      font-size: 28px;
      font-weight: 700;
      margin-top: 8px;
      letter-spacing: -0.5px;
    }}

    .green {{ color: var(--color-success); }}
    .red {{ color: var(--color-danger); }}

    /* Main Content Sections */
    .dashboard-body {{
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 24px;
      margin-bottom: 24px;
    }}

    @media (max-width: 900px) {{
      .dashboard-body {{
        grid-template-columns: 1fr;
      }}
    }}

    .panel {{
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    }}

    .panel h2 {{
      font-size: 16px;
      font-weight: 600;
      margin-bottom: 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}

    /* SVG Line Chart styling */
    .chart-container {{
      height: 160px;
      position: relative;
      margin-bottom: 16px;
    }}

    .line-chart {{
      width: 100%;
      height: 100%;
      display: block;
    }}

    .chart-dot {{
      fill: var(--color-surface);
      stroke: var(--color-primary);
      stroke-width: 2.5px;
      cursor: pointer;
      transition: r 0.2s ease, stroke-width 0.2s ease;
    }}

    .chart-dot:hover {{
      r: 6.5px;
      stroke-width: 3px;
      stroke: var(--color-success);
    }}

    /* Vendor Allocation Progress Bars */
    .vendor-metric {{
      margin-bottom: 12px;
    }}

    .vendor-info {{
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      margin-bottom: 4px;
    }}

    .vendor-name {{
      color: var(--color-text-primary);
      font-weight: 500;
    }}

    .vendor-amount {{
      color: var(--color-text-muted);
    }}

    .progress-bar-bg {{
      background: var(--color-border);
      height: 6px;
      border-radius: 3px;
      overflow: hidden;
    }}

    .progress-bar-fill {{
      background: var(--color-primary);
      height: 100%;
      border-radius: 3px;
      transition: width 0.5s ease;
    }}

    /* Job Card list style */
    .jobs-list {{
      display: flex;
      flex-direction: column;
      gap: 12px;
      max-height: 400px;
      overflow-y: auto;
      padding-right: 4px;
    }}

    /* Custom Scrollbars */
    ::-webkit-scrollbar {{
      width: 6px;
    }}
    ::-webkit-scrollbar-track {{
      background: var(--color-bg);
    }}
    ::-webkit-scrollbar-thumb {{
      background: var(--color-border);
      border-radius: 3px;
    }}
    ::-webkit-scrollbar-thumb:hover {{
      background: var(--color-text-muted);
    }}

    .job-card {{
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid var(--color-border);
      border-radius: 12px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      position: relative;
      overflow: hidden;
      flex-shrink: 0;
    }}

    .job-card::before {{
      content: '';
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 4px;
      background: var(--color-text-muted);
    }}

    .job-card.status-completed::before {{ background: var(--color-success); }}
    .job-card.status-failed::before {{ background: var(--color-danger); }}
    .job-card.status-declined::before {{ background: var(--color-danger); }}
    .job-card.status-awaiting_payment::before {{ background: var(--color-warning); }}

    .job-card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11px;
      color: var(--color-text-muted);
    }}

    .job-id {{
      font-family: var(--font-mono);
      font-weight: 500;
    }}

    .badge {{
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.5px;
    }}

    .badge-completed {{ background: var(--color-success-glow); color: var(--color-success); }}
    .badge-failed {{ background: var(--color-danger-glow); color: var(--color-danger); }}
    .badge-declined {{ background: var(--color-danger-glow); color: var(--color-danger); }}
    .badge-awaiting_payment {{ background: rgba(251, 191, 36, 0.15); color: var(--color-warning); }}
    .badge-accepted {{ background: var(--color-primary-glow); color: var(--color-primary); }}

    .job-title {{
      font-size: 13px;
      font-weight: 600;
      color: var(--color-text-primary);
      margin: 4px 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .job-metrics {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      background: rgba(0, 0, 0, 0.15);
      border-radius: 8px;
      padding: 8px;
      text-align: center;
    }}

    .job-metric-item {{
      display: flex;
      flex-direction: column;
      gap: 2px;
    }}

    .job-metric-lbl {{
      font-size: 9px;
      color: var(--color-text-muted);
      text-transform: uppercase;
    }}

    .job-metric-val {{
      font-size: 11px;
      font-weight: 600;
    }}

    .job-card-footer {{
      display: flex;
      justify-content: flex-end;
      margin-top: 4px;
    }}

    .decline-label {{
      font-size: 11px;
      color: var(--color-danger);
    }}

    /* Buttons */
    .btn {{
      font-family: var(--font-display);
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      text-align: center;
      transition: all 0.2s ease;
      border: 1px solid transparent;
    }}

    .btn-sm {{
      padding: 4px 10px;
      font-size: 11px;
    }}

    .btn-primary {{
      background: var(--color-primary);
      color: white;
    }}
    .btn-primary:hover {{
      background: #4f46e5;
    }}

    .btn-outline {{
      background: transparent;
      border-color: var(--color-border);
      color: var(--color-text-primary);
    }}
    .btn-outline:hover {{
      background: rgba(255, 255, 255, 0.05);
      border-color: var(--color-text-muted);
    }}

    .btn-close {{
      background: transparent;
      border: none;
      color: var(--color-text-muted);
      font-size: 20px;
      cursor: pointer;
    }}
    .btn-close:hover {{
      color: white;
    }}

    /* System Console Panel */
    .console-panel {{
      background: #090d13;
      border: 1px solid var(--color-border);
      border-radius: 16px;
      padding: 16px 20px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    }}

    .console-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: var(--color-text-muted);
      margin-bottom: 12px;
      border-bottom: 1px solid var(--color-border);
      padding-bottom: 8px;
    }}

    .console-title {{
      display: flex;
      align-items: center;
      gap: 6px;
      font-family: var(--font-display);
      font-weight: 600;
      color: var(--color-text-primary);
    }}

    .console-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--color-success);
      box-shadow: 0 0 8px var(--color-success);
      display: inline-block;
    }}

    .console-body {{
      font-family: var(--font-mono);
      font-size: 12px;
      line-height: 1.6;
      max-height: 250px;
      overflow-y: auto;
      color: #cbd5e1;
    }}

    .console-line {{
      display: flex;
      gap: 8px;
      margin-bottom: 4px;
      white-space: pre-wrap;
    }}

    .console-time {{
      color: #64748b;
      user-select: none;
    }}

    .console-job-id {{
      color: var(--color-primary);
      font-weight: 500;
    }}

    .green-txt {{ color: var(--color-success); }}
    .red-txt {{ color: var(--color-danger); }}
    .grey-txt {{ color: var(--color-text-muted); }}
    .gold-txt {{ color: var(--color-warning); }}
    .filepath-txt {{ color: #38bdf8; font-weight: 500; }}
    
    .vendor-badge {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--color-border);
      padding: 1px 4px;
      border-radius: 3px;
      font-size: 11px;
    }}

    .console-link {{
      color: #38bdf8;
      text-decoration: underline;
    }}

    /* Side Drawer Overlay & Panel */
    .overlay {{
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(4px);
      opacity: 0;
      visibility: hidden;
      transition: opacity 0.3s ease, visibility 0.3s ease;
      z-index: 100;
    }}

    .overlay.open {{
      opacity: 1;
      visibility: visible;
    }}

    .drawer {{
      position: fixed;
      top: 0;
      right: -450px;
      width: 450px;
      height: 100%;
      background: var(--color-surface);
      border-left: 1px solid var(--color-border);
      box-shadow: -8px 0 32px rgba(0, 0, 0, 0.4);
      z-index: 101;
      display: flex;
      flex-direction: column;
      transition: right 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    }}

    .drawer.open {{
      right: 0;
    }}

    .drawer-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 20px;
      border-bottom: 1px solid var(--color-border);
    }}

    .drawer-header h2 {{
      font-size: 16px;
      font-weight: 600;
    }}

    .drawer-body {{
      padding: 20px;
      overflow-y: auto;
      flex-grow: 1;
    }}

    .meta-row {{
      display: flex;
      justify-content: space-between;
      margin-bottom: 10px;
      font-size: 13px;
    }}

    .drawer-divider {{
      border: 0;
      border-top: 1px solid var(--color-border);
      margin: 16px 0;
    }}

    .drawer-body h3 {{
      font-size: 14px;
      margin-bottom: 12px;
      color: var(--color-text-primary);
    }}

    .drawer-expenses-list {{
      list-style-type: none;
      padding-left: 0;
      font-size: 12px;
    }}

    .drawer-expenses-list li {{
      padding: 6px 0;
      border-bottom: 1px dashed var(--color-border);
    }}

    /* Markdown preview box inside drawer */
    .brief-container {{
      background: var(--color-bg);
      border: 1px solid var(--color-border);
      border-radius: 8px;
      padding: 14px;
      font-size: 12px;
      line-height: 1.6;
      color: #cbd5e1;
    }}

    .markdown-body h1, .markdown-body h2 {{
      margin-top: 12px;
      margin-bottom: 8px;
      font-weight: 600;
      color: white;
    }}

    .markdown-body h1 {{ font-size: 15px; border-bottom: 1px solid var(--color-border); padding-bottom: 4px; }}
    .markdown-body h2 {{ font-size: 13px; }}
    .markdown-body p {{ margin-bottom: 10px; }}
    .markdown-body ul {{ margin-bottom: 10px; padding-left: 16px; }}
    .markdown-body li {{ margin-bottom: 4px; }}

    .no-data-msg {{
      color: var(--color-text-muted);
      font-size: 12px;
      text-align: center;
      padding: 16px 0;
    }}
    
    /* Connection Status Indicator */
    .status-dot {{
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      margin-right: 6px;
      vertical-align: middle;
    }}
    .status-dot.live {{
      background-color: #10b981;
      box-shadow: 0 0 8px #10b981;
      animation: statusPulse 2s infinite ease-in-out;
    }}
    .status-dot.offline {{
      background-color: #f59e0b;
      box-shadow: 0 0 8px #f59e0b;
      animation: statusPulse 1s infinite ease-in-out;
    }}
    @keyframes statusPulse {{
      0% {{ opacity: 0.3; }}
      50% {{ opacity: 1; }}
      100% {{ opacity: 0.3; }}
    }}
    {live_css}
    /* Modal styles */
    .modal {{
      display: none;
      position: fixed;
      z-index: 2000;
      left: 0;
      top: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background-color: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(4px);
      align-items: center;
      justify-content: center;
      opacity: 0;
      transition: opacity 0.3s ease;
    }}
    .modal.open {{
      display: flex;
      opacity: 1;
    }}
    .modal-content {{
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: 20px;
      width: 90%;
      max-width: 850px;
      height: 80%;
      max-height: 700px;
      display: flex;
      flex-direction: column;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
      transform: scale(0.95);
      transition: transform 0.3s ease;
    }}
    .modal.open .modal-content {{
      transform: scale(1);
    }}
    .modal-header {{
      padding: 16px 20px;
      border-bottom: 1px solid var(--color-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .modal-header h3 {{
      font-size: 16px;
      font-weight: 600;
      color: var(--color-text-primary);
    }}
    .modal-body {{
      flex: 1;
      padding: 0;
      background: #070a13;
      position: relative;
    }}
    .modal-body iframe {{
      width: 100%;
      height: 100%;
      border: none;
      background: transparent;
    }}
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
      <div id="header-seed-capital" class="pill">Seed Capital: {fmt(s['capital_cents'])}</div>
      <div id="header-net-growth" class="pill">Net Growth: <span class="green" id="header-net-growth-val">+{fmt(s['balance_cents'] - s['capital_cents'])}</span></div>
    </div>
  </header>

  <div class="metrics-grid">
    <div class="metric-card">
      <div class="label">Cash Balance</div>
      <div class="val" id="metric-cash-balance">{fmt(s['balance_cents'])}</div>
    </div>
    <div class="metric-card">
      <div class="label">Revenue</div>
      <div class="val green" id="metric-revenue">{fmt(s['revenue_cents'])}</div>
    </div>
    <div class="metric-card">
      <div class="label">Operating Spend</div>
      <div class="val red" id="metric-operating-spend">{fmt(s['expense_cents'])}</div>
    </div>
    <div class="metric-card">
      <div class="label">Net Profit</div>
      <div class="val {'green' if s['net_profit_cents']>=0 else 'red'}" id="metric-net-profit">{fmt(s['net_profit_cents'])}</div>
    </div>
    <div class="metric-card">
      <div class="label">Total Margin</div>
      <div class="val" id="metric-margin">{s['margin_pct']}%</div>
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
    let briefs = {json.dumps(briefs)};
    let jobsData = {json.dumps(jobs_data)};
    let currentOpenJobId = null;

    function openBriefModal(jobId) {{
      document.getElementById("modal-title").innerText = "Research Brief Preview: " + jobId;
      const iframe = document.getElementById("brief-iframe");
      const reportText = briefs[jobId] || "_No report brief generated or found in data/reports._";
      iframe.removeAttribute("src");
      iframe.srcdoc = renderMarkdown(reportText);
      document.getElementById("brief-modal").classList.add("open");
    }}

    function closeBriefModal() {{
      document.getElementById("brief-modal").classList.remove("open");
      const iframe = document.getElementById("brief-iframe");
      iframe.removeAttribute("srcdoc");
      iframe.src = "about:blank";
    }}

    function updateBriefsList() {{
      const container = document.getElementById("briefs-list-container");
      if (!container) return;
      
      const jobIds = Object.keys(briefs || {{}});
      if (jobIds.length === 0) {{
        container.innerHTML = "<p class='no-data-msg'>No research briefs delivered yet.</p>";
        return;
      }}
      
      let html = "";
      jobIds.forEach(jobId => {{
        const job = jobsData[jobId] || {{}};
        const title = job.title || `Research Brief (${{jobId}})`;
        html += `
          <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.02); border:1px solid var(--color-border); padding:10px 14px; border-radius:10px; margin-bottom: 8px;">
            <div style="display:flex; flex-direction:column; gap:2px; max-width:70%;">
              <span style="font-size:11px; font-family:var(--font-mono); color:var(--color-text-muted);">${{jobId}}</span>
              <span style="font-size:13px; font-weight:600; color:var(--color-text-primary); text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${{title}}</span>
            </div>
            <button class="btn btn-outline btn-sm" onclick="openBriefModal('${{jobId}}')" style="padding:4px 8px; font-size:11px;">Preview</button>
          </div>
        `;
      }});
      container.innerHTML = html;
    }}

    // Auto-scroll terminal log to the bottom
    function scrollConsole() {{
      const consoleBody = document.getElementById("consoleBody");
      if (consoleBody) {{
        consoleBody.scrollTop = consoleBody.scrollHeight;
      }}
    }}
    scrollConsole();
    updateBriefsList();

    function updateDrawerContent(jobId) {{
      const job = jobsData[jobId];
      if (!job) return;

      document.getElementById("drawer-title").innerText = "Job Details: " + jobId;

      let metaHTML = `
        <div class="meta-row"><strong>Title:</strong> <span>${{job.title || 'N/A'}}</span></div>
        <div class="meta-row"><strong>Status:</strong> <span class="badge badge-${{job.status}}">${{job.status.toUpperCase()}}</span></div>
        <div class="meta-row"><strong>Client Budget:</strong> <span>${{formatCents(job.price)}}</span></div>
        <div class="meta-row"><strong>Incurred COGS:</strong> <span>${{formatCents(getExpensesTotal(job.expenses))}}</span></div>
        <div class="meta-row"><strong>Net P&L:</strong> <span class="${{job.pnl >= 0 ? 'green' : 'red'}}">${{job.pnl >= 0 ? '+' : ''}}${{formatCents(job.pnl)}}</span></div>
        <div class="meta-row"><strong>Est. Margin:</strong> <span>${{job.margin_pct}}%</span></div>
      `;

      if (job.expenses.length > 0) {{
        metaHTML += `
          <h4 style="margin-top: 16px; margin-bottom: 8px; font-size:12px; color:var(--color-text-muted)">ITEMIZED EXPENSES</h4>
          <ul class="drawer-expenses-list">
        `;
        job.expenses.forEach(e => {{
          metaHTML += `<li><strong>${{e.vendor}}:</strong> -${{formatCents(e.amount)}} (${{e.memo}})</li>`;
        }});
        metaHTML += '</ul>';
      }}

      document.getElementById("drawer-metadata").innerHTML = metaHTML;

      const reportText = briefs[jobId] || "_No report brief generated or found in data/reports._";
      document.getElementById("drawer-brief").innerHTML = renderMarkdown(reportText);
    }}

    function openDrawer(jobId) {{
      currentOpenJobId = jobId;
      updateDrawerContent(jobId);
      document.getElementById("drawer").classList.add("open");
      document.getElementById("overlay").classList.add("open");
    }}

    function closeDrawer() {{
      currentOpenJobId = null;
      document.getElementById("drawer").classList.remove("open");
      document.getElementById("overlay").classList.remove("open");
    }}

    function formatCents(cents) {{
      return "$" + (cents / 100).toFixed(2);
    }}

    function getExpensesTotal(expenses) {{
      return expenses.reduce((sum, e) => sum + e.amount, 0);
    }}

    function renderMarkdown(md) {{
      // Clean up html entities first
      let html = md
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

      // Headings
      html = html.replace(/^# (.*?)$/gm, '<h1>$1</h1>');
      html = html.replace(/^## (.*?)$/gm, '<h2>$1</h2>');
      html = html.replace(/^### (.*?)$/gm, '<h3>$1</h3>');

      // Lists
      html = html.replace(/^- (.*?)$/gm, '<li>$1</li>');
      html = html.replace(/(<li>.*?<\\/li>)/gs, '<ul>$1<\\/ul>');
      html = html.replace(/<\\/ul>\\s*<ul>/g, '');

      // Bold & Italics
      html = html.replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
      html = html.replace(/\\*(.*?)\\*/g, '<em>$1</em>');

      // Double line breaks to paragraphs
      const paragraphs = html.split(/\n\n+/);
      html = paragraphs.map(p => {{
        p = p.trim();
        if (!p) return '';
        if (p.startsWith('<h') || p.startsWith('<ul') || p.startsWith('<li')) return p;
        return `<p>${{p.replace(/\n/g, '<br>')}}</p>`;
      }}).join('\n');

      return html;
    }}

    let lastChartSVG = '';
    let lastExpenseHTML = '';
    let lastJobsHTML = '';
    let lastConsoleHTML = '';

    {client_updates_js}
  </script>
</body>
</html>"""

    status_dir = Path(__file__).resolve().parent.parent / "data"
    status_dir.mkdir(exist_ok=True)
    status_json_path = status_dir / "dashboard_status.json"
    status_json_path.write_text(json.dumps(status_data, indent=2))

    OUT.write_text(html)
    return OUT
