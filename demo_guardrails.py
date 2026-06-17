#!/usr/bin/env python3
"""
demo_guardrails.py — prove the agent cannot spend unsafely.

Screens five payments through the NemoClaw-style policy layer, demonstrating
the guardrail rejecting four distinct policy violations before hitting Stripe.
"""

from solvent.treasury import Treasury
from solvent.guardrails import Guardrails, GuardrailError

# ANSI colors
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_GREY = "\033[90m"

BAR = f"{C_GREY}{'─' * 66}{C_RESET}"

t = Treasury()
t.reset()
t.seed(3_000)  # $30 operating cash on hand
g = Guardrails(t)

attempts = [
    ("Pay a vendor NOT on the allowlist",      900,  "sketchy-vendor",  None),
    ("Single payment above the $50 cap",     6_000,  "market-data-api", None),
    ("Spend that breaks the $20 cash reserve", 2_000, "nvidia-nemotron", None),
    ("Spend on a job projected to lose money",  500,  "nvidia-nemotron", -200),
    ("A normal, in-policy payment",             240,  "market-data-api", 4_000),
]

print(f"\n🛡️  {C_BOLD}SOLVENT Spend Guardrails (NemoClaw-Style Spend Screening){C_RESET}")
print(BAR)

for label, amt, vendor, margin in attempts:
    try:
        g.check_spend(amt, vendor, projected_job_margin_cents=margin)
        print(f"  {C_GREEN}✅ ALLOWED{C_RESET}  ${amt/100:>6.2f} → {vendor:<16} | {C_BOLD}{label}{C_RESET}")
    except GuardrailError as e:
        print(f"  {C_RED}🛑 BLOCKED{C_RESET}  ${amt/100:>6.2f} → {vendor:<16} | {label}\n               {C_GREY}reason:{C_RESET} {C_RED}{e}{C_RESET}")
        
print(BAR)
print("  Every outbound payment is screened by guardrails before reaching Stripe.\n")
