"""Live dashboard UI fragments: chat panel + voice (Web Speech API)."""

from __future__ import annotations

CHAT_PANEL_CSS = """
    .chat-agent-row {
      display: grid;
      grid-template-columns: 1fr 380px;
      gap: 24px;
      margin-bottom: 24px;
    }
    @media (max-width: 1100px) {
      .chat-agent-row { grid-template-columns: 1fr; }
    }
    .chat-panel {
      background: var(--color-surface);
      border: 1px solid var(--color-border);
      border-radius: 16px;
      display: flex;
      flex-direction: column;
      height: 420px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    }
    .chat-header {
      padding: 14px 18px;
      border-bottom: 1px solid var(--color-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 13px;
      font-weight: 600;
    }
    .chat-messages {
      flex: 1;
      overflow-y: auto;
      padding: 14px 18px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .chat-bubble {
      max-width: 92%;
      padding: 10px 12px;
      border-radius: 12px;
      font-size: 13px;
      line-height: 1.45;
      white-space: pre-wrap;
    }
    .chat-bubble.user {
      align-self: flex-end;
      background: var(--color-primary-glow);
      border: 1px solid var(--color-primary);
    }
    .chat-bubble.assistant {
      align-self: flex-start;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--color-border);
    }
    .chat-bubble.system {
      align-self: center;
      font-size: 11px;
      color: var(--color-text-muted);
      background: transparent;
      border: none;
      padding: 4px;
    }
    .chat-input-row {
      display: flex;
      gap: 8px;
      padding: 12px 14px;
      border-top: 1px solid var(--color-border);
    }
    .chat-input {
      flex: 1;
      background: var(--color-bg);
      border: 1px solid var(--color-border);
      border-radius: 8px;
      color: var(--color-text-primary);
      font-family: var(--font-display);
      font-size: 13px;
      padding: 10px 12px;
      outline: none;
    }
    .chat-input:focus { border-color: var(--color-primary); }
    .btn-mic {
      background: transparent;
      border: 1px solid var(--color-border);
      color: var(--color-text-primary);
      border-radius: 8px;
      width: 40px;
      cursor: pointer;
      font-size: 16px;
    }
    .btn-mic.active {
      border-color: var(--color-danger);
      color: var(--color-danger);
      box-shadow: 0 0 10px var(--color-danger-glow);
    }
    .btn-mic:disabled { opacity: 0.4; cursor: not-allowed; }
    .voice-pill {
      font-size: 11px;
      color: var(--color-text-muted);
      cursor: pointer;
      user-select: none;
    }
    .voice-pill.on { color: var(--color-success); }
"""

CHAT_PANEL_HTML = """
  <div class="chat-agent-row">
    <div class="console-panel" style="margin-bottom:0">
      <div class="console-header">
        <span class="console-title"><span class="console-dot"></span> AGENT TRANSACTION & LOG FEED</span>
        <span>SYSTEM: ACTIVE</span>
      </div>
      <div class="console-body" id="consoleBody">
        <div id="console-lines">{console_log_html}</div>
      </div>
    </div>
    <div class="chat-panel" id="chat-panel">
      <div class="chat-header">
        <span>💬 Agent Chat</span>
        <span class="voice-pill" id="tts-toggle" title="Toggle spoken replies">🔊 Voice off</span>
      </div>
      <div class="chat-messages" id="chat-messages">
        <div class="chat-bubble system">Ask about treasury, quote a brief, or commission research.</div>
      </div>
      <div class="chat-input-row">
        <input class="chat-input" id="chat-input" type="text" placeholder="Type or use mic…" autocomplete="off" />
        <button class="btn-mic" id="mic-btn" title="Speech to text" disabled>🎤</button>
        <button class="btn btn-primary btn-sm" id="chat-send">Send</button>
      </div>
    </div>
  </div>
"""

LIVE_CLIENT_JS = """
    const chatSessionId = localStorage.getItem('solvent_chat_session') || ('web-' + Math.random().toString(36).slice(2, 10));
    localStorage.setItem('solvent_chat_session', chatSessionId);
    let ttsEnabled = localStorage.getItem('solvent_tts') === '1';
    let listening = false;
    let recognition = null;

    function appendChat(role, text) {
      const box = document.getElementById('chat-messages');
      if (!box) return;
      const el = document.createElement('div');
      el.className = 'chat-bubble ' + role;
      el.textContent = text;
      box.appendChild(el);
      box.scrollTop = box.scrollHeight;
    }

    function speak(text) {
      if (!ttsEnabled || !window.speechSynthesis) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.0;
      window.speechSynthesis.speak(u);
    }

    function updateTtsToggle() {
      const el = document.getElementById('tts-toggle');
      if (!el) return;
      el.textContent = ttsEnabled ? '🔊 Voice on' : '🔊 Voice off';
      el.classList.toggle('on', ttsEnabled);
    }
    updateTtsToggle();
    document.getElementById('tts-toggle')?.addEventListener('click', () => {
      ttsEnabled = !ttsEnabled;
      localStorage.setItem('solvent_tts', ttsEnabled ? '1' : '0');
      updateTtsToggle();
    });

    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const micBtn = document.getElementById('mic-btn');
    if (SR && micBtn) {
      micBtn.disabled = false;
      recognition = new SR();
      recognition.lang = 'en-US';
      recognition.interimResults = false;
      recognition.onresult = (e) => {
        const text = e.results[0][0].transcript.trim();
        const input = document.getElementById('chat-input');
        if (input) input.value = text;
        sendChat();
      };
      recognition.onend = () => {
        listening = false;
        micBtn.classList.remove('active');
      };
      recognition.onerror = () => {
        listening = false;
        micBtn.classList.remove('active');
      };
      micBtn.addEventListener('click', () => {
        if (listening) { recognition.stop(); return; }
        listening = true;
        micBtn.classList.add('active');
        recognition.start();
      });
    } else if (micBtn) {
      micBtn.title = 'Speech recognition not supported in this browser';
    }

    async function sendChat() {
      const input = document.getElementById('chat-input');
      const text = (input?.value || '').trim();
      if (!text) return;
      input.value = '';
      appendChat('user', text);
      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text, session_id: chatSessionId }),
        });
        if (!res.ok) throw new Error('chat failed');
        const data = await res.json();
        appendChat('assistant', data.reply || '(no reply)');
        if (data.reply) speak(data.reply);
      } catch (err) {
        appendChat('system', 'Chat unavailable — start: python -m solvent serve');
      }
    }

    document.getElementById('chat-send')?.addEventListener('click', sendChat);
    document.getElementById('chat-input')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
    });

    function applyStatus(data) {
      if (!data) return;
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
      const ops = document.getElementById("ops-panel");
      if (ops && data.ops_html !== undefined && ops.innerHTML !== data.ops_html) {
        ops.innerHTML = data.ops_html;
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
      const indicator = document.getElementById("status-indicator");
      if (indicator) indicator.innerHTML = '<span class="status-dot live"></span> Live';
    }

    async function pollStatus() {
      try {
        const res = await fetch('/api/status');
        if (!res.ok) throw new Error('status failed');
        applyStatus(await res.json());
      } catch (err) {
        try {
          const res = await fetch('data/dashboard_status.json');
          if (!res.ok) throw err;
          applyStatus(await res.json());
        } catch (_) {
          const indicator = document.getElementById("status-indicator");
          if (indicator) indicator.innerHTML = '<span class="status-dot offline"></span> Reconnecting...';
        }
      }
    }

    if (typeof EventSource !== 'undefined') {
      const es = new EventSource('/api/events?session_id=' + encodeURIComponent(chatSessionId));
      es.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'status' && msg.data) applyStatus(msg.data);
          if (msg.type === 'chat' && msg.role === 'assistant' && msg.channel === 'dashboard' && (!msg.session_id || msg.session_id === chatSessionId)) {
            appendChat('assistant', msg.text);
            speak(msg.text);
          }
          if (msg.type === 'agent_event' && msg.data) applyStatus(msg.data);
        } catch (e) { console.error(e); }
      };
      es.onerror = () => {
        const indicator = document.getElementById("status-indicator");
        if (indicator) indicator.innerHTML = '<span class="status-dot offline"></span> Reconnecting...';
      };
    }
    setInterval(pollStatus, 4000);
    pollStatus();
"""
