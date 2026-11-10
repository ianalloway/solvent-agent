"""Static CSS and JavaScript for the treasury dashboard (``dashboard.render``).

Kept out of ``dashboard.py`` so the renderer reads as Python. These are plain
strings, not f-strings: braces here are literal CSS/JS.
"""

#: Base dashboard styles, emitted before the live-mode overrides.
DASHBOARD_CSS = """    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

    :root {
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
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      font-family: var(--font-display);
      background: var(--color-bg);
      color: var(--color-text-primary);
      padding: 24px;
      line-height: 1.5;
    }

    a {
      color: var(--color-primary);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }

    /* Layout structure */
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--color-border);
    }

    header h1 {
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.5px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    header p.sub {
      color: var(--color-text-muted);
      font-size: 13px;
      margin-top: 4px;
    }

    .header-pills {
      display: flex;
      gap: 12px;
    }

    .pill {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 500;
      color: var(--color-text-muted);
    }

    /* Card Metrics Grid */
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }

    .metric-card {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
      transition: transform 0.2s ease, border-color 0.2s ease;
    }

    .metric-card:hover {
      transform: translateY(-2px);
      border-color: var(--color-primary);
    }

    .metric-card .label {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--color-text-muted);
    }

    .metric-card .val {
      font-size: 28px;
      font-weight: 700;
      margin-top: 8px;
      letter-spacing: -0.5px;
    }

    .green { color: var(--color-success); }
    .red { color: var(--color-danger); }

    /* Main Content Sections */
    .dashboard-body {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 24px;
      margin-bottom: 24px;
    }

    @media (max-width: 900px) {
      .dashboard-body {
        grid-template-columns: 1fr;
      }
    }

    .panel {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: 16px;
      padding: 20px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    }

    .panel h2 {
      font-size: 16px;
      font-weight: 600;
      margin-bottom: 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    /* SVG Line Chart styling */
    .chart-container {
      height: 160px;
      position: relative;
      margin-bottom: 16px;
    }

    .line-chart {
      width: 100%;
      height: 100%;
      display: block;
    }

    .chart-dot {
      fill: var(--color-surface);
      stroke: var(--color-primary);
      stroke-width: 2.5px;
      cursor: pointer;
      transition: r 0.2s ease, stroke-width 0.2s ease;
    }

    .chart-dot:hover {
      r: 6.5px;
      stroke-width: 3px;
      stroke: var(--color-success);
    }

    /* Vendor Allocation Progress Bars */
    .vendor-metric {
      margin-bottom: 12px;
    }

    .vendor-info {
      display: flex;
      justify-content: space-between;
      font-size: 12px;
      margin-bottom: 4px;
    }

    .vendor-name {
      color: var(--color-text-primary);
      font-weight: 500;
    }

    .vendor-amount {
      color: var(--color-text-muted);
    }

    .progress-bar-bg {
      background: var(--color-border);
      height: 6px;
      border-radius: 3px;
      overflow: hidden;
    }

    .progress-bar-fill {
      background: var(--color-primary);
      height: 100%;
      border-radius: 3px;
      transition: width 0.5s ease;
    }

    /* Job Card list style */
    .jobs-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
      max-height: 400px;
      overflow-y: auto;
      padding-right: 4px;
    }

    /* Custom Scrollbars */
    ::-webkit-scrollbar {
      width: 6px;
    }
    ::-webkit-scrollbar-track {
      background: var(--color-bg);
    }
    ::-webkit-scrollbar-thumb {
      background: var(--color-border);
      border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: var(--color-text-muted);
    }

    .job-card {
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
    }

    .job-card::before {
      content: '';
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 4px;
      background: var(--color-text-muted);
    }

    .job-card.status-completed::before { background: var(--color-success); }
    .job-card.status-failed::before { background: var(--color-danger); }
    .job-card.status-declined::before { background: var(--color-danger); }
    .job-card.status-awaiting_payment::before { background: var(--color-warning); }

    .job-card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11px;
      color: var(--color-text-muted);
    }

    .job-id {
      font-family: var(--font-mono);
      font-weight: 500;
    }

    .badge {
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.5px;
    }

    .badge-completed { background: var(--color-success-glow); color: var(--color-success); }
    .badge-failed { background: var(--color-danger-glow); color: var(--color-danger); }
    .badge-declined { background: var(--color-danger-glow); color: var(--color-danger); }
    .badge-awaiting_payment { background: rgba(251, 191, 36, 0.15); color: var(--color-warning); }
    .badge-accepted { background: var(--color-primary-glow); color: var(--color-primary); }

    .job-title {
      font-size: 13px;
      font-weight: 600;
      color: var(--color-text-primary);
      margin: 4px 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .job-metrics {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      background: rgba(0, 0, 0, 0.15);
      border-radius: 8px;
      padding: 8px;
      text-align: center;
    }

    .job-metric-item {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .job-metric-lbl {
      font-size: 9px;
      color: var(--color-text-muted);
      text-transform: uppercase;
    }

    .job-metric-val {
      font-size: 11px;
      font-weight: 600;
    }

    .job-card-footer {
      display: flex;
      justify-content: flex-end;
      margin-top: 4px;
    }

    .decline-label {
      font-size: 11px;
      color: var(--color-danger);
    }

    /* Buttons */
    .btn {
      font-family: var(--font-display);
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      text-align: center;
      transition: all 0.2s ease;
      border: 1px solid transparent;
    }

    .btn-sm {
      padding: 4px 10px;
      font-size: 11px;
    }

    .btn-primary {
      background: var(--color-primary);
      color: white;
    }
    .btn-primary:hover {
      background: #4f46e5;
    }

    .btn-outline {
      background: transparent;
      border-color: var(--color-border);
      color: var(--color-text-primary);
    }
    .btn-outline:hover {
      background: rgba(255, 255, 255, 0.05);
      border-color: var(--color-text-muted);
    }

    .btn-close {
      background: transparent;
      border: none;
      color: var(--color-text-muted);
      font-size: 20px;
      cursor: pointer;
    }
    .btn-close:hover {
      color: white;
    }

    /* System Console Panel */
    .console-panel {
      background: #090d13;
      border: 1px solid var(--color-border);
      border-radius: 16px;
      padding: 16px 20px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    }

    .console-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: var(--color-text-muted);
      margin-bottom: 12px;
      border-bottom: 1px solid var(--color-border);
      padding-bottom: 8px;
    }

    .console-title {
      display: flex;
      align-items: center;
      gap: 6px;
      font-family: var(--font-display);
      font-weight: 600;
      color: var(--color-text-primary);
    }

    .console-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--color-success);
      box-shadow: 0 0 8px var(--color-success);
      display: inline-block;
    }

    .console-body {
      font-family: var(--font-mono);
      font-size: 12px;
      line-height: 1.6;
      max-height: 250px;
      overflow-y: auto;
      color: #cbd5e1;
    }

    .console-line {
      display: flex;
      gap: 8px;
      margin-bottom: 4px;
      white-space: pre-wrap;
    }

    .console-time {
      color: #64748b;
      user-select: none;
    }

    .console-job-id {
      color: var(--color-primary);
      font-weight: 500;
    }

    .green-txt { color: var(--color-success); }
    .red-txt { color: var(--color-danger); }
    .grey-txt { color: var(--color-text-muted); }
    .gold-txt { color: var(--color-warning); }
    .filepath-txt { color: #38bdf8; font-weight: 500; }

    .vendor-badge {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--color-border);
      padding: 1px 4px;
      border-radius: 3px;
      font-size: 11px;
    }

    .console-link {
      color: #38bdf8;
      text-decoration: underline;
    }

    /* Side Drawer Overlay & Panel */
    .overlay {
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
    }

    .overlay.open {
      opacity: 1;
      visibility: visible;
    }

    .drawer {
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
    }

    .drawer.open {
      right: 0;
    }

    .drawer-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 20px;
      border-bottom: 1px solid var(--color-border);
    }

    .drawer-header h2 {
      font-size: 16px;
      font-weight: 600;
    }

    .drawer-body {
      padding: 20px;
      overflow-y: auto;
      flex-grow: 1;
    }

    .meta-row {
      display: flex;
      justify-content: space-between;
      margin-bottom: 10px;
      font-size: 13px;
    }

    .drawer-divider {
      border: 0;
      border-top: 1px solid var(--color-border);
      margin: 16px 0;
    }

    .drawer-body h3 {
      font-size: 14px;
      margin-bottom: 12px;
      color: var(--color-text-primary);
    }

    .drawer-expenses-list {
      list-style-type: none;
      padding-left: 0;
      font-size: 12px;
    }

    .drawer-expenses-list li {
      padding: 6px 0;
      border-bottom: 1px dashed var(--color-border);
    }

    /* Markdown preview box inside drawer */
    .brief-container {
      background: var(--color-bg);
      border: 1px solid var(--color-border);
      border-radius: 8px;
      padding: 14px;
      font-size: 12px;
      line-height: 1.6;
      color: #cbd5e1;
    }

    .markdown-body h1, .markdown-body h2 {
      margin-top: 12px;
      margin-bottom: 8px;
      font-weight: 600;
      color: white;
    }

    .markdown-body h1 { font-size: 15px; border-bottom: 1px solid var(--color-border); padding-bottom: 4px; }
    .markdown-body h2 { font-size: 13px; }
    .markdown-body p { margin-bottom: 10px; }
    .markdown-body ul { margin-bottom: 10px; padding-left: 16px; }
    .markdown-body li { margin-bottom: 4px; }

    .no-data-msg {
      color: var(--color-text-muted);
      font-size: 12px;
      text-align: center;
      padding: 16px 0;
    }

    /* Connection Status Indicator */
    .status-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      margin-right: 6px;
      vertical-align: middle;
    }
    .status-dot.live {
      background-color: #10b981;
      box-shadow: 0 0 8px #10b981;
      animation: statusPulse 2s infinite ease-in-out;
    }
    .status-dot.offline {
      background-color: #f59e0b;
      box-shadow: 0 0 8px #f59e0b;
      animation: statusPulse 1s infinite ease-in-out;
    }
    @keyframes statusPulse {
      0% { opacity: 0.3; }
      50% { opacity: 1; }
      100% { opacity: 0.3; }
    }"""

#: Brief-viewer modal styles, emitted after the live-mode overrides.
MODAL_CSS = """    /* Modal styles */
    .modal {
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
    }
    .modal.open {
      display: flex;
      opacity: 1;
    }
    .modal-content {
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
    }
    .modal.open .modal-content {
      transform: scale(1);
    }
    .modal-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--color-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-header h3 {
      font-size: 16px;
      font-weight: 600;
      color: var(--color-text-primary);
    }
    .modal-body {
      flex: 1;
      padding: 0;
      background: #070a13;
      position: relative;
    }
    .modal-body iframe {
      width: 100%;
      height: 100%;
      border: none;
      background: transparent;
    }"""

#: Client-side behaviour: brief modal, job cards and ledger rendering.
DASHBOARD_JS = """    let currentOpenJobId = null;

    function openBriefModal(jobId) {
      document.getElementById("modal-title").innerText = "Research Brief Preview: " + jobId;
      const iframe = document.getElementById("brief-iframe");
      iframe.src = `http://localhost:8787/api/briefs/${encodeURIComponent(jobId)}`;
      document.getElementById("brief-modal").classList.add("open");
    }

    function closeBriefModal() {
      document.getElementById("brief-modal").classList.remove("open");
      document.getElementById("brief-iframe").src = "about:blank";
    }

    function updateBriefsList() {
      const container = document.getElementById("briefs-list-container");
      if (!container) return;

      const jobIds = Object.keys(briefs || {});
      if (jobIds.length === 0) {
        container.innerHTML = "<p class='no-data-msg'>No research briefs delivered yet.</p>";
        return;
      }

      container.replaceChildren();
      jobIds.forEach(jobId => {
        const job = jobsData[jobId] || {};
        const row = document.createElement("div");
        row.style.cssText = "display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.02); border:1px solid var(--color-border); padding:10px 14px; border-radius:10px; margin-bottom: 8px;";
        const labels = document.createElement("div");
        labels.style.cssText = "display:flex; flex-direction:column; gap:2px; max-width:70%;";
        const idEl = document.createElement("span");
        idEl.style.cssText = "font-size:11px; font-family:var(--font-mono); color:var(--color-text-muted);";
        idEl.textContent = jobId;
        const titleEl = document.createElement("span");
        titleEl.style.cssText = "font-size:13px; font-weight:600; color:var(--color-text-primary); text-overflow:ellipsis; overflow:hidden; white-space:nowrap;";
        titleEl.textContent = job.title || `Research Brief (${jobId})`;
        labels.append(idEl, titleEl);
        const button = document.createElement("button");
        button.className = "btn btn-outline btn-sm";
        button.style.cssText = "padding:4px 8px; font-size:11px;";
        button.textContent = "Preview";
        button.addEventListener("click", () => openBriefModal(jobId));
        row.append(labels, button);
        container.appendChild(row);
      });
    }

    // Auto-scroll terminal log to the bottom
    function scrollConsole() {
      const consoleBody = document.getElementById("consoleBody");
      if (consoleBody) {
        consoleBody.scrollTop = consoleBody.scrollHeight;
      }
    }
    scrollConsole();
    updateBriefsList();

    function updateDrawerContent(jobId) {
      const job = jobsData[jobId];
      if (!job) return;

      document.getElementById("drawer-title").innerText = "Job Details: " + jobId;

      const meta = document.getElementById("drawer-metadata");
      meta.replaceChildren();
      const metaRow = (label, value, className) => {
        const row = document.createElement("div");
        row.className = "meta-row";
        const strong = document.createElement("strong");
        strong.textContent = label;
        const span = document.createElement("span");
        if (className) span.className = className;
        span.textContent = value;
        row.append(strong, " ", span);
        meta.appendChild(row);
      };
      const status = String(job.status);
      metaRow("Title:", job.title || "N/A");
      metaRow("Status:", status.toUpperCase(), "badge badge-" + status.replace(/[^a-z_]/gi, ""));
      metaRow("Client Budget:", formatCents(job.price));
      metaRow("Incurred COGS:", formatCents(getExpensesTotal(job.expenses)));
      metaRow("Net P&L:", (job.pnl >= 0 ? "+" : "") + formatCents(job.pnl), job.pnl >= 0 ? "green" : "red");
      metaRow("Est. Margin:", String(job.margin_pct) + "%");

      if (job.expenses.length > 0) {
        const heading = document.createElement("h4");
        heading.style.cssText = "margin-top: 16px; margin-bottom: 8px; font-size:12px; color:var(--color-text-muted)";
        heading.textContent = "ITEMIZED EXPENSES";
        const list = document.createElement("ul");
        list.className = "drawer-expenses-list";
        job.expenses.forEach(e => {
          const item = document.createElement("li");
          const vendor = document.createElement("strong");
          vendor.textContent = String(e.vendor) + ":";
          item.append(vendor, ` -${formatCents(e.amount)} (${e.memo})`);
          list.appendChild(item);
        });
        meta.append(heading, list);
      }

      const hasBrief = Object.prototype.hasOwnProperty.call(briefs || {}, jobId);
      const reportText = hasBrief
        ? "_Brief generated. Use the hosted brief link with a delivery token to view full contents._"
        : "_No report brief generated or found in data/reports._";
      document.getElementById("drawer-brief").innerHTML = renderMarkdown(reportText);
    }

    function openDrawer(jobId) {
      currentOpenJobId = jobId;
      updateDrawerContent(jobId);
      document.getElementById("drawer").classList.add("open");
      document.getElementById("overlay").classList.add("open");
    }

    function closeDrawer() {
      currentOpenJobId = null;
      document.getElementById("drawer").classList.remove("open");
      document.getElementById("overlay").classList.remove("open");
    }

    function updateRefundWarning(data) {
      const warning = document.getElementById('refund-warning');
      const pending = data.pending_refund_cents || 0;
      warning.style.display = pending ? 'block' : 'none';
      warning.textContent = 'Unresolved refunds: ' + formatCents(pending) +
        '. Booked revenue/profit are not confirmed retained funds. Available cash: ' +
        formatCents(data.available_balance_cents) + '. Run solvent reconcile.';
    }

    function formatCents(cents) {
      return "$" + (cents / 100).toFixed(2);
    }

    function getExpensesTotal(expenses) {
      return expenses.reduce((sum, e) => sum + e.amount, 0);
    }

    function renderMarkdown(md) {
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
      const paragraphs = html.split(/\\n\\n+/);
      html = paragraphs.map(p => {
        p = p.trim();
        if (!p) return '';
        if (p.startsWith('<h') || p.startsWith('<ul') || p.startsWith('<li')) return p;
        return `<p>${p.replace(/\\n/g, '<br>')}</p>`;
      }).join("\\n");

      return html;
    }

    let lastChartSVG = '';
    let lastExpenseHTML = '';
    let lastJobsHTML = '';
    let lastConsoleHTML = '';
"""
