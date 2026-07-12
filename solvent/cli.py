#!/usr/bin/env python3
"""Run SOLVENT's offline batch demo or interactive terminal flow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solvent import dashboard
from solvent.agent import Solvent
from solvent.config import (
    SolventConfig,
    apply_config,
    config_exists,
    default_config,
    load_config,
)
from solvent.jobs import SAMPLE_JOBS
from solvent.onboarding import run_wizard, should_skip_onboarding, wants_reconfigure
from solvent.treasury import fmt


C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_CYAN = "\033[36m"
C_GREY = "\033[90m"

BAR = f"{C_GREY}{'─' * 66}{C_RESET}"


def print_step(label: str) -> None:
    """Print a completed demo step without artificial delays or animation."""
    print(f"   {C_GREY}{label}{C_RESET} {C_GREEN}Done!{C_RESET}")


def print_event(event: dict) -> None:
    """Render a StageRunner event for the terminal demo."""
    stage = event.get("stage")
    job_id = event.get("job_id", "")
    prefix = f"{C_CYAN}[{job_id}]{C_RESET}"

    if stage == "quote":
        verdict = f"{C_GREEN}ACCEPT{C_RESET}" if event["accept"] else f"{C_RED}DECLINE{C_RESET}"
        print(
            f"   ⚖️  {prefix} Margin Gate: price {C_BOLD}{fmt(event['price'])}{C_RESET} "
            f"| est cost {fmt(event['est_cost'])} | projected margin "
            f"{event['margin_pct']}% → {verdict}"
        )
    elif stage == "declined":
        print(f"   ✋  {prefix} {C_RED}Declined:{C_RESET} {event['reason']}")
    elif stage == "invoice":
        print_step(f"Generating Stripe Payment Link for {job_id}...")
        tag = "simulated" if event["simulated"] else "LIVE"
        print(f"   📄  {prefix} Issued checkout link [{tag}]")
        print(f"       {C_GREY}URL:{C_RESET} {C_BLUE}{event['url']}{C_RESET}")
    elif stage == "paid":
        print_step(f"Confirming Stripe payment for {job_id}...")
        print(
            f"   💵  {prefix} {C_GREEN}Stripe Confirmed:{C_RESET} "
            f"Payment of {C_GREEN}+{fmt(event['amount'])}{C_RESET} received"
        )
    elif stage == "fulfilled":
        print_step(f"Compiling research brief for {job_id}...")
        filename = Path(event["deliverable"]).name
        print(
            f"   📝  {prefix} {C_GREEN}Fulfillment Finished:{C_RESET} "
            f"Deliverable saved to {C_BLUE}data/reports/{filename}{C_RESET} "
            f"({event['tokens']} tokens)"
        )
    elif stage == "spend":
        print(
            f"   🛡️   {C_GREY}↳ Spend Approved:{C_RESET} Scoped payment "
            f"{C_RED}−{fmt(event['amount'])}{C_RESET} to "
            f"{C_BOLD}{event['vendor']}{C_RESET} ({event['memo']})"
        )
    elif stage == "spend_blocked":
        print(
            f"   🛑  {C_RED}↳ Spend BLOCKED:{C_RESET} Guardrail rejected "
            f"transaction of {fmt(event['amount'])} to {event['vendor']} "
            f"({event['memo']})"
        )
    elif stage == "refunded":
        print(
            f"   ↩️  {prefix} {C_RED}Escrow Refunded:{C_RESET} Returned "
            f"{C_RED}{fmt(event['amount'])}{C_RESET} to customer "
            f"({event['reason']})"
        )
    elif stage == "booked":
        tag = f"{C_GREEN}profit{C_RESET}" if event["job_pnl"] >= 0 else f"{C_RED}loss{C_RESET}"
        print(
            f"   ✓   {prefix} booked P&L: {tag} "
            f"{C_BOLD}{fmt(event['job_pnl'])}{C_RESET} | treasury cash balance: "
            f"{C_YELLOW}{fmt(event['balance'])}{C_RESET}\n"
        )


def print_results(snapshot: dict) -> None:
    """Print the financial result of a demo session."""
    print(BAR)
    print(f"📊  {C_BOLD}SESSION SUMMARY & BALANCE SHEET{C_RESET}")
    print(f"   Revenue         {C_GREEN}{fmt(snapshot['revenue_cents'])}{C_RESET}")
    print(f"   Operating spend {C_RED}{fmt(snapshot['expense_cents'])}{C_RESET}")
    profit_color = C_GREEN if snapshot["net_profit_cents"] >= 0 else C_RED
    print(
        f"   Net profit      {profit_color}{fmt(snapshot['net_profit_cents'])}{C_RESET}  "
        f"({snapshot['margin_pct']}% margin)"
    )
    print(
        f"   Cash balance    {C_YELLOW}{fmt(snapshot['balance_cents'])}{C_RESET}  "
        f"(seed was {fmt(snapshot['capital_cents'])})"
    )

    growth = snapshot["balance_cents"] - snapshot["capital_cents"]
    if growth > 0:
        print(f"   {C_GREEN}→ The agent grew its treasury by {fmt(growth)}.{C_RESET}")
    elif growth < 0:
        print(f"   {C_RED}→ The agent ran at a loss of {fmt(abs(growth))}.{C_RESET}")
    else:
        print("   → The agent broke even.")


def _finish_session(agent: Solvent) -> None:
    snapshot = agent.t.snapshot()
    print_results(snapshot)
    path = dashboard.render(snapshot, agent.log)
    print(f"\n   Dashboard: {C_BLUE}{path}{C_RESET}\n{BAR}\n")


def run_batch_demo(seed_cents: int = 10_000, fresh: bool = True) -> None:
    """Run the four predefined jobs through the complete money loop."""
    print(f"\n🪙  {C_BOLD}SOLVENT — Standard Batch Run (4 Inbound Jobs){C_RESET}")
    print(BAR)

    agent = Solvent(seed_cents=seed_cents, fresh=fresh, on_event=print_event)
    print(f"   Seed capital: {C_YELLOW}{fmt(agent.t.capital_cents())}{C_RESET}\n")

    for job in SAMPLE_JOBS:
        print(f"{C_BOLD}■ {job['id']}: {job['topic']}{C_RESET}")
        print_step("Analyzing inbound job specifications...")
        agent.handle_job(job)

    _finish_session(agent)


def _read_positive_cents(prompt: str) -> int | None:
    raw = input(prompt).strip()
    try:
        cents = int(float(raw) * 100)
    except (ValueError, OverflowError):
        print(f"{C_RED}Invalid numeric entry. Use a value such as 49.00.{C_RESET}\n")
        return None
    if cents <= 0:
        print(f"{C_RED}Amount must be greater than zero.{C_RESET}\n")
        return None
    return cents


def _fund(agent: Solvent, command: str) -> bool:
    """Handle /fund and return True when the input was a funding command."""
    if not command.startswith("/fund"):
        return False
    parts = command.split()
    if len(parts) != 2:
        print(f"{C_RED}Usage: /fund <amount_in_usd> (for example, /fund 150.00){C_RESET}\n")
        return True
    try:
        cents = int(float(parts[1]) * 100)
    except (ValueError, OverflowError):
        cents = 0
    if cents <= 0:
        print(f"{C_RED}Fund amount must be positive.{C_RESET}\n")
        return True

    agent.t.seed(cents, memo="User injected operating capital")
    print(
        f"   💵  {C_GREEN}Deposit Confirmed:{C_RESET} Added "
        f"{C_GREEN}+{fmt(cents)}{C_RESET} of operating capital."
    )
    agent._emit(
        stage="booked",
        job_id="SYSTEM",
        job_pnl=0,
        balance=agent.t.balance_cents(),
        status="completed",
        title="User Capital Injection",
    )
    return True


def run_interactive_mode(seed_cents: int = 10_000, fresh: bool = True) -> None:
    """Accept custom research jobs from stdin until the user stops."""
    print(f"\n🪙  {C_BOLD}SOLVENT — Interactive Agent Terminal{C_RESET}")
    print(BAR)

    agent = Solvent(seed_cents=seed_cents, fresh=fresh, on_event=print_event)
    print(f"   Current balance: {C_YELLOW}{fmt(agent.t.balance_cents())}{C_RESET}")
    print(f"   {C_GREY}Tip: type /fund 100 to add operating capital.{C_RESET}\n")

    job_index = 1
    while True:
        print(f"{C_BOLD}--- Enter New Research Request ---{C_RESET}")
        topic = input(f"{C_CYAN}Topic (or /fund <amount>):{C_RESET} ").strip()
        if not topic:
            print(f"{C_RED}Request topic cannot be blank.{C_RESET}\n")
            continue
        if _fund(agent, topic):
            continue

        budget_cents = _read_positive_cents(
            f"{C_CYAN}Client Budget in USD (for example, 50.00):{C_RESET} $"
        )
        if budget_cents is None:
            continue

        is_large = budget_cents >= 5_000
        job = {
            "id": f"I{job_index}",
            "topic": topic,
            "context": "Interactive customer-submitted request.",
            "customer_email": "interactive@user.example",
            "budget_cents": budget_cents,
            "est_tokens": 12_000 if is_large else 7_500,
            "market_data_calls": 3 if is_large else 2,
            "web_search_calls": 9 if is_large else 6,
        }
        job_index += 1

        print(f"\n{C_BOLD}■ {job['id']}: {topic}{C_RESET}")
        print_step("Analyzing inbound job specifications...")
        agent.handle_job(job)

        again = input(f"Submit another research request? ({C_BOLD}y/N{C_RESET}): ").strip().lower()
        print()
        if again != "y":
            break

    _finish_session(agent)


def resolve_config() -> SolventConfig:
    """Load saved preferences, onboarding when necessary."""
    if wants_reconfigure():
        return run_wizard()
    if not config_exists() and not should_skip_onboarding():
        return run_wizard()
    return load_config() or default_config()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run SOLVENT — a self-funding analyst agent.",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="submit custom research jobs from the terminal (overrides saved mode)",
    )
    parser.add_argument(
        "--onboard",
        action="store_true",
        help="run (or re-run) the first-run setup wizard",
    )
    parser.add_argument(
        "--no-onboard",
        action="store_true",
        help="skip onboarding wizard; use defaults if no config exists",
    )
    parser.add_argument(
        "--seed",
        type=float,
        default=100.0,
        help="initial operating seed capital in USD (default: 100.0)",
    )
    parser.add_argument(
        "--keep-balance",
        action="store_true",
        help="keep the existing database balance",
    )
    args = parser.parse_args()

    config = resolve_config()
    apply_config(config)

    seed_cents = int(args.seed * 100)
    fresh = not args.keep_balance
    if args.interactive:
        run_interactive_mode(seed_cents=seed_cents, fresh=fresh)
    elif config.interaction_mode == "programmatic":
        from solvent.onboarding import _print_programmatic_guidance

        _print_programmatic_guidance()
        raise SystemExit(0)
    elif config.interaction_mode == "interactive":
        run_interactive_mode(seed_cents=seed_cents, fresh=fresh)
    else:
        run_batch_demo(seed_cents=seed_cents, fresh=fresh)


if __name__ == "__main__":
    main()
