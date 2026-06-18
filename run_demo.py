#!/usr/bin/env python3
"""
run_demo.py — watch SOLVENT run as a business.

Runs four inbound jobs through the agent or starts an interactive session.
You'll see the agent quote jobs, decline unprofitable ones, collect Stripe payments,
run Nemotron reasoning, screen payments through guardrails, and book the P&L.
Outputs a premium, interactive treasury_dashboard.html at the end.

First run: interactive onboarding wizard (see ONBOARDING.md).
Skip wizard: --no-onboard or SOLVENT_SKIP_ONBOARD=1
Reconfigure: --onboard
"""

import sys
import time
import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from solvent.agent import Solvent
from solvent.jobs import SAMPLE_JOBS
from solvent.treasury import fmt
from solvent import dashboard
from solvent.config import (
    apply_config,
    config_exists,
    default_config,
    load_config,
)
from solvent.onboarding import run_wizard, should_skip_onboarding, wants_reconfigure

# ANSI terminal colors (zero dependencies)
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_GREY = "\033[90m"

BAR = f"{C_GREY}{'─' * 66}{C_RESET}"

SQLITE_MAX_INT = 2**63 - 1


def parse_usd_cents(amount: str) -> int:
    """Parse a positive finite USD amount into SQLite-safe cents."""
    try:
        dollars = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise ValueError("amount must be a finite positive number") from None

    if not dollars.is_finite():
        raise ValueError("amount must be finite")

    try:
        cents = int((dollars * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise ValueError("amount exceeds the maximum supported value") from None

    if cents <= 0:
        raise ValueError("amount must be positive")
    if cents > SQLITE_MAX_INT:
        raise ValueError("amount exceeds the maximum supported value")
    return cents


def seed_usd_arg(value: str) -> int:
    """Argparse type for --seed that returns validated cents."""
    try:
        return parse_usd_cents(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def show_spinner(duration: float, label: str):
    """Render a clean animated terminal spinner."""
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    end_time = time.time() + duration
    i = 0
    sys.stdout.write(f"   {C_GREY}{label}{C_RESET} ")
    sys.stdout.flush()
    while time.time() < end_time:
        sys.stdout.write(f"\r   {C_GREY}{label}{C_RESET} {C_CYAN}{frames[i % len(frames)]}{C_RESET}")
        sys.stdout.flush()
        time.sleep(0.08)
        i += 1
    sys.stdout.write(f"\r   {C_GREY}{label}{C_RESET} {C_GREEN}Done!{C_RESET}\n")
    sys.stdout.flush()


def print_event(e: dict):
    """Callback function mapping Agent logs to beautifully colorized terminal stdout."""
    st = e.get("stage")
    jid = e.get("job_id", "")
    prefix = f"{C_CYAN}[{jid}]{C_RESET}"
    
    if st == "quote":
        verdict = f"{C_GREEN}ACCEPT{C_RESET}" if e["accept"] else f"{C_RED}DECLINE{C_RESET}"
        print(f"   ⚖️  {prefix} Margin Gate: price {C_BOLD}{fmt(e['price'])}{C_RESET} | est cost {fmt(e['est_cost'])} | projected margin {e['margin_pct']}% → {verdict}")
        time.sleep(0.4)
        
    elif st == "declined":
        print(f"   ✋  {prefix} {C_RED}Declined:{C_RESET} {e['reason']}")
        time.sleep(0.4)
        
    elif st == "invoice":
        show_spinner(0.6, f"Generating Stripe Payment Link for {jid}...")
        tag = "simulated" if e["simulated"] else "LIVE"
        print(f"   📄  {prefix} Issued checkout link [{tag}]")
        print(f"       {C_GREY}URL:{C_RESET} {C_BLUE}{e['url']}{C_RESET}")
        time.sleep(0.4)
        
    elif st == "paid":
        show_spinner(0.8, f"Polling Stripe invoice confirmation for {jid}...")
        print(f"   💵  {prefix} {C_GREEN}Stripe Confirmed:{C_RESET} Payment of {C_GREEN}+{fmt(e['amount'])}{C_RESET} received")
        time.sleep(0.4)
        
    elif st == "fulfilled":
        show_spinner(1.2, f"Nemotron compiling sell-side research brief for {jid}...")
        filename = Path(e['deliverable']).name
        print(f"   📝  {prefix} {C_GREEN}Fulfillment Finished:{C_RESET} Deliverable saved to {C_BLUE}data/reports/{filename}{C_RESET} ({e['tokens']} tokens)")
        time.sleep(0.4)
        
    elif st == "spend":
        time.sleep(0.3)
        print(f"   🛡️   {C_GREY}↳ Spend Approved:{C_RESET} Scoped payment {C_RED}−{fmt(e['amount'])}{C_RESET} to {C_BOLD}{e['vendor']}{C_RESET} ({e['memo']})")
        
    elif st == "spend_blocked":
        time.sleep(0.3)
        print(f"   🛑  {C_RED}↳ Spend BLOCKED:{C_RESET} Guardrail rejected transaction of {fmt(e['amount'])} to {e['vendor']} ({e['memo']})")
        
    elif st == "refunded":
        time.sleep(0.3)
        print(f"   ↩️  {prefix} {C_RED}Escrow Refunded:{C_RESET} Returned {C_RED}{fmt(e['amount'])}{C_RESET} to customer ({e['reason']})")

    elif st == "booked":

        tag = f"{C_GREEN}profit{C_RESET}" if e["job_pnl"] >= 0 else f"{C_RED}loss{C_RESET}"
        time.sleep(0.4)
        print(f"   ✓   {prefix} booked P&L: {tag} {C_BOLD}{fmt(e['job_pnl'])}{C_RESET} | treasury cash balance: {C_YELLOW}{fmt(e['balance'])}{C_RESET}")
        print()


def print_results(snap: dict):
    """Print overall financial results summary."""
    print(BAR)
    print(f"📊  {C_BOLD}SESSION SUMMARY & BALANCE SHEET{C_RESET}")
    print(f"   Revenue        {C_GREEN}{fmt(snap['revenue_cents'])}{C_RESET}")
    print(f"   Operating spend {C_RED}{fmt(snap['expense_cents'])}{C_RESET}")
    print(f"   Net profit     {C_GREEN if snap['net_profit_cents']>=0 else C_RED}{fmt(snap['net_profit_cents'])}{C_RESET}  ({snap['margin_pct']}% margin)")
    print(f"   Cash balance   {C_YELLOW}{fmt(snap['balance_cents'])}{C_RESET}  (seed was {fmt(snap['capital_cents'])})")
    
    grew = snap['balance_cents'] - snap['capital_cents']
    if grew > 0:
        print(f"   {C_GREEN}→ The agent grew its own treasury by {fmt(grew)} this session!{C_RESET}")
    elif grew < 0:
        print(f"   {C_RED}→ The agent ran at a loss of {fmt(abs(grew))} this session.{C_RESET}")
    else:
        print("   → The agent broke perfectly even.")


def run_batch_demo(seed_cents: int = 10_000, fresh: bool = True):
    """Run the standard batch demo of 4 predefined jobs."""
    print(f"\n🪙  {C_BOLD}SOLVENT — Standard Batch Run (4 Inbound Jobs){C_RESET}")
    print(BAR)
    
    agent = Solvent(seed_cents=seed_cents, fresh=fresh, on_event=print_event)
    print(f"   Seed capital: {C_YELLOW}{fmt(agent.t.capital_cents())}{C_RESET}\n")
    
    for job in SAMPLE_JOBS:
        print(f"{C_BOLD}■ {job['id']}: {job['topic']}{C_RESET}")
        show_spinner(0.4, "Analyzing inbound job specifications...")
        agent.handle_job(job)
        
    snap = agent.t.snapshot()
    print_results(snap)
    path = dashboard.render(snap, agent.log)
    print(f"\n   Dashboard: {C_BLUE}{path}{C_RESET}\n{BAR}\n")


def run_interactive_mode(seed_cents: int = 10_000, fresh: bool = True):
    """Run interactive CLI mode letting the user test custom briefs and pricing."""
    print(f"\n🪙  {C_BOLD}SOLVENT — Interactive Agent Terminal{C_RESET}")
    print(BAR)
    
    agent = Solvent(seed_cents=seed_cents, fresh=fresh, on_event=print_event)
    print(f"   Current balance: {C_YELLOW}{fmt(agent.t.balance_cents())}{C_RESET}")
    print(f"   {C_GREY}Tip: Type /fund <amount> (e.g. /fund 100) to add funds to the treasury.{C_RESET}\n")
    
    job_index = 1
    while True:
        print(f"{C_BOLD}--- Enter New Research Request ---{C_RESET}")
        topic = input(f"{C_CYAN}Topic (or /fund <amount>):{C_RESET} ").strip()
        if not topic:
            print(f"{C_RED}Request topic cannot be blank.{C_RESET}\n")
            continue
            
        if topic.startswith("/fund"):
            parts = topic.split()
            if len(parts) < 2:
                print(f"{C_RED}Usage: /fund <amount_in_usd> (e.g., /fund 150.00){C_RESET}\n")
                continue
            try:
                fund_cents = parse_usd_cents(parts[1])
                # Add capital to treasury
                agent.t.seed(fund_cents, memo="User injected operating capital")
                print(f"   💵  {C_GREEN}Deposit Confirmed:{C_RESET} Added {C_GREEN}+{fmt(fund_cents)}{C_RESET} of operating capital to treasury.")
                # Emit booked event to trigger dashboard update
                agent._emit(
                    stage="booked",
                    job_id="SYSTEM",
                    job_pnl=0,
                    balance=agent.t.balance_cents(),
                    status="completed",
                    title="User Capital Injection"
                )
                print(f"       Current Capital Balance: {C_YELLOW}{fmt(agent.t.balance_cents())}{C_RESET}\n")
                continue
            except ValueError as exc:
                print(f"{C_RED}Invalid amount: {exc}. Usage: /fund <amount_in_usd>{C_RESET}\n")
                continue
            
        budget_str = input(f"{C_CYAN}Client Budget in USD (e.g. 50.00):{C_RESET} $").strip()
        try:
            budget_cents = int(float(budget_str) * 100)
            if budget_cents <= 0:
                print(f"{C_RED}Budget must be greater than 0.{C_RESET}\n")
                continue
        except ValueError:
            print(f"{C_RED}Invalid numeric entry. Use standard formats like 49.00.{C_RESET}\n")
            continue
            
        job_id = f"I{job_index}"
        job_index += 1
        
        # Scale resource specifications based on budget
        is_large = budget_cents >= 5000
        est_tokens = 12000 if is_large else 7500
        market_calls = 3 if is_large else 2
        search_calls = 9 if is_large else 6
        
        custom_job = {
            "id": job_id,
            "topic": topic,
            "context": "Interactive customer-submitted request.",
            "customer_email": "interactive@user.example",
            "budget_cents": budget_cents,
            "est_tokens": est_tokens,
            "market_data_calls": market_calls,
            "web_search_calls": search_calls
        }
        
        print(f"\n{C_BOLD}■ {job_id}: {topic}{C_RESET}")
        show_spinner(0.4, "Analyzing inbound job specifications...")
        agent.handle_job(custom_job)
        
        cont = input(f"Submit another research request? ({C_BOLD}y/N{C_RESET}): ").strip().lower()
        print()
        if cont != "y":
            break
            
    snap = agent.t.snapshot()
    print_results(snap)
    path = dashboard.render(snap, agent.log)
    print(f"\n   Dashboard: {C_BLUE}{path}{C_RESET}\n{BAR}\n")


def resolve_config(args) -> "SolventConfig":
    """Load, onboard, or default user preferences."""
    from solvent.config import SolventConfig

    if wants_reconfigure():
        return run_wizard()

    if not config_exists() and not should_skip_onboarding():
        return run_wizard()

    cfg = load_config()
    if cfg is None:
        cfg = default_config()
    return cfg


def main():
    parser = argparse.ArgumentParser(
        description="Run SOLVENT — a self-funding analyst agent.",
    )
    parser.add_argument(
        "-i", "--interactive",
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
        type=seed_usd_arg,
        default=10_000,
        help="initial operating seed capital in USD (default: 100.0)",
    )
    parser.add_argument(
        "--keep-balance",
        action="store_true",
        help="keep existing database balance (do not reset treasury)",
    )
    parser.add_argument(
        "--async",
        action="store_true",
        dest="async_mode",
        help="enqueue jobs and run background worker (non-blocking payment)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="start HTTP server + worker (requires requirements-serve.txt)",
    )
    args = parser.parse_args()

    cfg = resolve_config(args)
    apply_config(cfg)

    seed_cents = args.seed
    fresh = not args.keep_balance

    if args.serve:
        import threading
        from solvent.worker import run_worker
        from solvent.server import main as serve_main
        t = threading.Thread(
            target=run_worker,
            kwargs={"poll_interval": 2.0, "seed_cents": seed_cents, "fresh": fresh},
            daemon=True,
        )
        t.start()
        serve_main()
        return

    if args.async_mode:
        os.environ["SOLVENT_ASYNC"] = "1"

    if args.interactive:
        run_interactive_mode(seed_cents=seed_cents, fresh=fresh)
    elif cfg.interaction_mode == "programmatic":
        from solvent.onboarding import _print_programmatic_guidance
        _print_programmatic_guidance()
        sys.exit(0)
    elif cfg.interaction_mode == "interactive":
        run_interactive_mode(seed_cents=seed_cents, fresh=fresh)
    else:
        run_batch_demo(seed_cents=seed_cents, fresh=fresh)


if __name__ == "__main__":
    main()
