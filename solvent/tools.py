"""Allowlisted research tools for the bounded Nemotron agent loop."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

MAX_TOOL_ROUNDS = 8
MAX_TOOL_CALLS = 20

ALLOWED_TOOLS = frozenset({"web_search", "market_data", "summarize"})

TOOL_REGISTRY: dict[str, dict] = {
    "web_search": {
        "description": "Search the web for research snippets",
        "category": "research",
        "parameters": {"query": "string"},
    },
    "market_data": {
        "description": "Fetch market data for a symbol",
        "category": "research",
        "parameters": {"symbol": "string"},
    },
    "summarize": {
        "description": "Summarize text notes",
        "category": "research",
        "parameters": {"text": "string"},
    },
}

BUSINESS_TOOL_REGISTRY: dict[str, dict] = {
    "treasury_status": {
        "description": "Current treasury balance, revenue, expenses, margin",
        "category": "business",
        "parameters": {},
    },
    "list_jobs": {
        "description": "List recent jobs and statuses",
        "category": "business",
        "parameters": {},
    },
    "job_status": {
        "description": "Status and metrics for one job",
        "category": "business",
        "parameters": {"job_id": "string"},
    },
    "quote_brief": {
        "description": "Margin-gate quote preview without creating payment",
        "category": "business",
        "parameters": {"topic": "string", "budget_cents": "integer"},
    },
    "submit_brief": {
        "description": "Create job and return Stripe checkout URL",
        "category": "business",
        "parameters": {
            "topic": "string",
            "budget_cents": "integer",
            "customer_email": "string",
        },
    },
}


@dataclass
class ToolContext:
    """Tracks tool invocations for COGS and guardrails."""

    calls: list[dict] = field(default_factory=list)
    web_search_calls: int = 0
    market_data_calls: int = 0
    budget_exhausted: bool = False  # set when MAX_TOOL_CALLS was hit (truncated)

    def record(self, name: str, args: dict, result: str) -> None:
        self.calls.append({"tool": name, "args": args, "result_len": len(result)})
        if name == "web_search":
            self.web_search_calls += 1
        elif name == "market_data":
            self.market_data_calls += 1

    @property
    def total_calls(self) -> int:
        return len(self.calls)


def _stub_web_search(query: str) -> str:
    digest = hashlib.sha256(query.encode()).hexdigest()[:8]
    return (
        f"[offline web_search] Query: {query[:120]}\n"
        f"- Finding A ({digest}): demand signals accelerating.\n"
        f"- Finding B: competitive set consolidating.\n"
        f"- Finding C: regulatory risk in two jurisdictions."
    )


def _live_web_search(query: str) -> str:
    try:
        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode({
            "q": query[:200],
            "format": "json",
            "no_redirect": "1",
        })
        req = urllib.request.Request(url, headers={"User-Agent": "SOLVENT/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        abstract = data.get("AbstractText") or data.get("Heading") or ""
        related = [t.get("Text", "") for t in (data.get("RelatedTopics") or [])[:3]]
        snippets = "\n".join(f"- {s}" for s in related if s)
        return f"{abstract}\n{snippets}".strip() or _stub_web_search(query)
    except Exception:
        return _stub_web_search(query)


def _stub_market_data(symbol: str) -> str:
    sym = symbol.upper()[:12]
    seed = int(hashlib.md5(sym.encode()).hexdigest()[:6], 16)
    price = 50 + (seed % 450)
    return (
        f"[market_data] {sym}: last ${price}.52, 30d range ${price-20}-${price+30}, "
        f"vol +{(seed % 40)}% YoY (offline stub)."
    )


def _live_market_data(symbol: str) -> str:
    """Keyless live quote via Stooq's CSV endpoint; falls back to the stub."""
    sym = symbol.upper()[:12]
    try:
        url = "https://stooq.com/q/l/?" + urllib.parse.urlencode({
            "s": sym.lower(), "f": "sd2t2ohlcv", "h": "", "e": "csv",
        })
        req = urllib.request.Request(url, headers={"User-Agent": "SOLVENT/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", "replace").strip()
        # CSV header + one row: Symbol,Date,Time,Open,High,Low,Close,Volume
        rows = raw.splitlines()
        if len(rows) >= 2:
            cols = rows[1].split(",")
            if len(cols) >= 8 and cols[6] not in ("N/D", ""):
                _, date, _t, o, hi, lo, close, vol = cols[:8]
                return (
                    f"[market_data] {sym}: close ${close} "
                    f"(O {o} / H {hi} / L {lo}), vol {vol}, as of {date}."
                )
    except Exception:
        pass
    return _stub_market_data(symbol)


def web_search(query: str, *, live: bool = False) -> str:
    if not query or not isinstance(query, str):
        return "empty query"
    return _live_web_search(query) if live else _stub_web_search(query)


def market_data(symbol: str, *, live: bool = False) -> str:
    if not symbol or not isinstance(symbol, str):
        return "empty symbol"
    return _live_market_data(symbol) if live else _stub_market_data(symbol)


def summarize(text: str, nemotron_fn) -> str:
    if not text:
        return ""
    system = "Summarize the following research notes in 3-5 bullet points."
    result, _ = nemotron_fn(system, text[:4000])
    return result


def dispatch(name: str, args: dict, ctx: ToolContext, nemotron_fn, live_search: bool = False) -> str:
    if name not in ALLOWED_TOOLS:
        raise ValueError(f"tool {name!r} not allowlisted")
    if ctx.total_calls >= MAX_TOOL_CALLS:
        ctx.budget_exhausted = True
        raise RuntimeError(f"max tool calls ({MAX_TOOL_CALLS}) exceeded")
    if name == "web_search":
        result = web_search(args.get("query", ""), live=live_search)
    elif name == "market_data":
        result = market_data(args.get("symbol", ""), live=live_search)
    elif name == "summarize":
        result = summarize(args.get("text", ""), nemotron_fn)
    else:
        raise ValueError(f"unknown tool: {name}")
    ctx.record(name, args, result)
    return result
