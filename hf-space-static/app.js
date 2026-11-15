// Shared helpers: boot Pyodide, call the Python bridge, render tiny Markdown + tables.
const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/";
const WHEEL_URL = "https://files.pythonhosted.org/packages/e3/5d/364fdbe54e2420728c99b8c40aa3e0e93d4f97abead26b4ecfb0ac3343ef/solvent_agent-0.3.1-py3-none-any.whl";
const WHEEL_INTEGRITY = "sha256-YJZV0tccuOYrB2osjFthJdIDb1FR/juiBr0h2bjkszY=";
const runtimeIntegrity = new Map(Object.entries({
  "pyodide-lock.json": "sha384-kW8BHvD8flyCLy52v2Ca0t5mO5oCeKABys/zXNtKhH8+Mgxf/rnKFeksLsjy0y5Y",
  "pyodide.asm.wasm": "sha384-nfuXSg0f8YmFmBVMelAoom75Yji4r0iU2RrxMHYAa+AIt0JGdHWbau1imflLNNEu",
  "python_stdlib.zip": "sha384-3iWptIFECvk1VbDttuo8ZRZG8fk0MaHphrxgsJg6Rl40GnKqRLPRRuA7h+648E5D",
  "sqlite3-1.0.0-cp313-cp313-pyodide_2025_0_wasm32.whl": "sha256-vJQpqJgBbwQCcrV+RMO+t86/oYImzIVtC0q8qIyM7qc=",
}).map(([filename, integrity]) => [PYODIDE_URL + filename, integrity]));
let py = null;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

// Minimal Markdown for the strings the Python side produces: headings, tables, lists, paragraphs.
function md(text) {
  const out = [];
  const lines = String(text).split("\n");
  for (let i = 0; i < lines.length; ) {
    const l = lines[i];
    if (l.startsWith("|")) {
      const block = [];
      while (i < lines.length && lines[i].startsWith("|")) block.push(lines[i++]);
      const cells = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(block[0]);
      const body = block.slice(2).map(cells);
      const hasHead = head.some((h) => h);
      out.push("<table>" + (hasHead ? "<tr>" + head.map((h) => `<th>${inline(h)}</th>`).join("") + "</tr>" : "") +
        body.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</table>");
      continue;
    }
    const h = l.match(/^(#{1,4}) (.*)/);
    if (h) { out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); i++; continue; }
    if (l.startsWith("- ")) {
      const items = [];
      while (i < lines.length && lines[i].startsWith("- ")) items.push(`<li>${inline(lines[i++].slice(2))}</li>`);
      out.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    if (l.trim()) out.push(`<p>${inline(l)}</p>`);
    i++;
  }
  return out.join("");
}

function table(headers, rows) {
  return "<table><tr>" + headers.map((h) => `<th>${esc(h)}</th>`).join("") + "</tr>" +
    rows.map((r) => "<tr>" + r.map((c) => `<td>${esc(c)}</td>`).join("") + "</tr>").join("") + "</table>";
}

function status(msg, cls = "") { const el = $("#status"); el.textContent = msg; el.className = cls; }

async function boot() {
  const pkg = "solvent-agent 0.3.1";
  const t0 = performance.now();
  status("Loading verified Python runtime (Pyodide)…");
  // Pyodide fetches its WASM, stdlib, and extension wheels itself. Bind each
  // URL to its reviewed bytes before its fetch can be sent; unknown fetches
  // during runtime initialization fail closed.
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : input, location.href).href;
    const integrity = runtimeIntegrity.get(url);
    if (!integrity) throw new Error("Unexpected Python runtime resource");
    return originalFetch.call(globalThis, url, { ...init, integrity, redirect: "error" });
  };
  try {
    py = await loadPyodide({ indexURL: PYODIDE_URL });
    await py.loadPackage("sqlite3", { checkIntegrity: true });
  } finally {
    globalThis.fetch = originalFetch;
  }
  status(`Loading verified ${pkg} wheel…`);
  const wheel = await fetch(WHEEL_URL, { integrity: WHEEL_INTEGRITY, redirect: "error" });
  if (!wheel.ok) throw new Error("Verified SOLVENT wheel is unavailable");
  py.FS.writeFile("/tmp/solvent-wheel.zip", new Uint8Array(await wheel.arrayBuffer()));
  py.runPython(`
import sysconfig, zipfile
with zipfile.ZipFile("/tmp/solvent-wheel.zip") as wheel:
    wheel.extractall(sysconfig.get_path("purelib"))
`);
  const src = await (await fetch("space.py")).text();
  py.FS.writeFile("/home/pyodide/space.py", src);
  py.runPython("import space");
  const secs = ((performance.now() - t0) / 1000).toFixed(1);
  status(`Ready: real ${pkg} running in your browser (loaded in ${secs}s). No keys, no server.`, "ok");
  document.body.dataset.ready = "1";
  document.querySelectorAll("button[disabled]").forEach((b) => (b.disabled = false));
}

// Call a function in space.py; returns its result or throws with the friendly error text.
function call(name, ...args) {
  const r = JSON.parse(py.globals.get("space").api(name, JSON.stringify(args)));
  if (!r.ok) throw new Error(r.error);
  return r.result;
}

function wire(btnSel, fn) {
  $(btnSel).addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const errBox = btn.closest("section").querySelector(".error");
    errBox.textContent = "";
    btn.disabled = true;
    await new Promise((r) => setTimeout(r, 20)); // let the UI repaint before Python blocks the thread
    try { fn(); } catch (e) { errBox.textContent = "⚠ " + e.message; } finally { btn.disabled = false; }
  });
}

function tabs() {
  document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll("main > section").forEach((s) => (s.hidden = s.id !== b.dataset.tab));
  }));
}

// ---- SOLVENT UI ----
const DEMO_HEADERS = ["Job", "Topic", "Verdict", "Price", "Est. cost", "Proj. margin", "Realized P&L", "Cash after"];

tabs();
wire("#run", () => {
  const [rows, declinedHtml, pnl, log] = call("on_run_demo", Number($("#seed").value));
  $("#jobs").innerHTML = table(DEMO_HEADERS, rows);
  $("#declined").innerHTML = declinedHtml; // built by space.py with html.escape on every value
  $("#pnl").innerHTML = md(pnl);
  $("#log").textContent = log;
  $("#logbox").hidden = false;
});
wire("#quote", () => {
  const v = (id) => Number($(id).value);
  const [verdict, breakdown, cli] = call("on_quote", $("#topic").value, v("#budget"), v("#tokens"), v("#mcalls"), v("#scalls"), v("#floor"));
  $("#verdict").innerHTML = verdict; // escaped server-side in space.py
  $("#costs").innerHTML = table(["Cost line", "Estimate"], breakdown);
  $("#cli").textContent = cli;
  $("#clibox").hidden = false;
});
boot().catch((e) => status("Failed to load Python: " + e.message, "err"));
