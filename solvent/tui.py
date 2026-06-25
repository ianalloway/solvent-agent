"""
tui.py — Live terminal dashboard for SOLVENT.

Run with:
    python -m solvent tui

Displays treasury balance, recent jobs, and event log in a rich terminal UI
that refreshes every 2 seconds until Ctrl-C.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# Rich import guard
# ---------------------------------------------------------------------------
try:
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.console import Console
    from rich.columns import Columns
    from rich import box
except ImportError:  # pragma: no cover
    print(
        "[SOLVENT] The 'rich' library is required for the terminal dashboard.\n"
        "Install it with:  pip install rich"
    )
    sys.exit(1)

if TYPE_CHECKING:
    from .treasury import Treasury

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _status_color(status: str) -> str:
    """Return a Rich colour name for a job status string."""
    return {
        "completed": "green",
        "failed": "red",
        "in_progress": "yellow",
        "awaiting_payment": "cyan",
    }.get(status, "white")


def _margin_color(margin_pct: float) -> str:
    """Return a Rich colour name based on the net-margin percentage."""
    if margin_pct >= 50:
        return "green"
    if margin_pct >= 10:
        return "yellow"
    return "red"


def _balance_color(balance_cents: int) -> str:
    """Return a Rich colour name for the treasury balance."""
    return "green" if balance_cents > 0 else "red"


def _fmt(cents: int) -> str:
    """Format an integer cent amount as a dollar string (e.g. $1,234.56)."""
    return f"${cents / 100:,.2f}"


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------

def _header_panel() -> Panel:
    ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    title = Text("SOLVENT Treasury", style="bold white")
    title.append(f"    {ts}", style="dim white")
    return Panel(title, style="bold blue", box=box.HEAVY_HEAD)


def _stat_box(label: str, value: str, color: str) -> Panel:
    body = Text(value, style=f"bold {color}", justify="center")
    return Panel(body, title=f"[dim]{label}[/dim]", border_style=color, box=box.ROUNDED)


def _stats_panels(snapshot: dict) -> list[Panel]:
    balance = snapshot.get("balance_cents", 0)
    revenue = snapshot.get("revenue_cents", 0)
    margin = snapshot.get("margin_pct", 0.0)

    panels = [
        _stat_box("Balance", _fmt(balance), _balance_color(balance)),
        _stat_box("Revenue", _fmt(revenue), "bright_cyan"),
        _stat_box(
            "Net Margin %",
            f"{margin:.1f}%",
            _margin_color(margin),
        ),
    ]
    return panels


def _jobs_table(jobs: list[dict]) -> Table:
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("ID", style="dim", no_wrap=True, max_width=16)
    table.add_column("Topic", max_width=40)
    table.add_column("Status", no_wrap=True)
    table.add_column("P&L", justify="right", no_wrap=True)

    for job in jobs[-10:]:
        job_id = job.get("id", "")
        topic = job.get("topic") or ""
        if len(topic) > 40:
            topic = topic[:37] + "…"
        status = job.get("status", "")
        color = _status_color(status)
        status_text = Text(status, style=color)

        # P&L is loaded separately when build_layout is called
        pnl_cents = job.get("_pnl_cents", None)
        if pnl_cents is None:
            pnl_str = Text("—", style="dim")
        else:
            pnl_color = "green" if pnl_cents >= 0 else "red"
            pnl_str = Text(_fmt(pnl_cents), style=pnl_color)

        table.add_row(job_id, topic, status_text, pnl_str)

    return table


def _events_table(events: list[dict]) -> Table:
    table = Table(
        box=box.SIMPLE,
        show_header=True,
        header_style="bold blue",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Stage", no_wrap=True)
    table.add_column("Job ID", style="dim", no_wrap=True)
    table.add_column("Time", style="dim", no_wrap=True)

    for ev in events[:8]:
        stage = ev.get("stage", "")
        job_id = ev.get("job_id", "") or ""
        ts = ev.get("ts", 0)
        try:
            ts_str = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
        except (ValueError, OSError):
            ts_str = "—"
        table.add_row(stage, job_id, ts_str)

    return table


# ---------------------------------------------------------------------------
# Public layout builder (used directly in tests)
# ---------------------------------------------------------------------------

def build_layout(
    snapshot: dict,
    jobs: list[dict],
    events: list[dict],
) -> Layout:
    """
    Construct and return the full Rich Layout for one refresh cycle.

    Parameters
    ----------
    snapshot:
        Dict returned by ``Treasury.snapshot()``.
    jobs:
        List of job dicts, each optionally decorated with ``_pnl_cents``.
    events:
        List of event dicts returned by ``Treasury.list_events()``.
    """
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="stats", size=5),
        Layout(name="jobs", ratio=2),
        Layout(name="events", ratio=1),
        Layout(name="footer", size=3),
    )

    # Header
    layout["header"].update(_header_panel())

    # Stats row — three side-by-side panels
    stat_panels = _stats_panels(snapshot)
    stats_layout = Layout()
    stats_layout.split_row(
        Layout(stat_panels[0], name="balance"),
        Layout(stat_panels[1], name="revenue"),
        Layout(stat_panels[2], name="margin"),
    )
    layout["stats"].update(stats_layout)

    # Jobs table
    jobs_table = _jobs_table(jobs)
    layout["jobs"].update(
        Panel(jobs_table, title="[bold]Recent Jobs[/bold] (last 10)", border_style="magenta")
    )

    # Events log
    ev_table = _events_table(events)
    layout["events"].update(
        Panel(ev_table, title="[bold]Event Log[/bold] (last 8)", border_style="blue")
    )

    # Footer hint
    footer_text = Text(
        "  q / Ctrl-C  exit    ·    auto-refresh every 2 s",
        style="dim italic",
        justify="center",
    )
    layout["footer"].update(Panel(footer_text, box=box.MINIMAL))

    return layout


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(treasury: "Treasury | None" = None, refresh_interval: float = 2.0) -> None:
    """
    Start the live terminal dashboard and block until Ctrl-C.

    Parameters
    ----------
    treasury:
        An existing ``Treasury`` instance. If *None*, a default treasury is
        opened from the standard DB path.
    refresh_interval:
        How often (in seconds) to poll the database for updates.
    """
    if treasury is None:
        from .treasury import Treasury
        treasury = Treasury()

    console = Console()

    def _render() -> Layout:
        snapshot = treasury.snapshot()
        jobs = treasury.list_jobs()
        # Annotate each job with its P&L so the table renderer can display it
        for job in jobs:
            try:
                job["_pnl_cents"] = treasury.job_pnl_cents(job["id"])
            except Exception:
                job["_pnl_cents"] = None
        events = treasury.list_events(limit=8)
        return build_layout(snapshot, jobs, events)

    try:
        with Live(
            _render(),
            console=console,
            refresh_per_second=1 / refresh_interval,
            screen=True,
        ) as live:
            while True:
                time.sleep(refresh_interval)
                live.update(_render())
    except KeyboardInterrupt:
        pass
