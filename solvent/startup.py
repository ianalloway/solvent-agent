"""Shared, non-destructive treasury options for long-running service commands."""

from __future__ import annotations

import argparse
from decimal import DecimalException

from .money import dollars_to_cents


def _seed_cents(value: str) -> int:
    try:
        amount = dollars_to_cents(value)
    except (ValueError, DecimalException, OverflowError):
        raise argparse.ArgumentTypeError("seed must be a finite dollar amount") from None
    if not 0 <= amount <= 2**63 - 1:
        raise argparse.ArgumentTypeError("seed must be between 0 and 92233720368547758.07 dollars")
    return amount


def add_treasury_arguments(parser: argparse.ArgumentParser) -> None:
    """Require an explicit reset before service startup can clear history.

    Keep the old preservation flag for existing service definitions. A seed is
    used only by the reset path; ordinary starts never add operating capital.
    Validate it during argument parsing, before opening or resetting a treasury.
    """
    parser.add_argument(
        "--seed",
        dest="seed_cents",
        type=_seed_cents,
        default=10_000,
        metavar="DOLLARS",
        help="starting capital after --reset-state only (default: 100); ignored otherwise",
    )
    state = parser.add_mutually_exclusive_group()
    state.add_argument(
        "--reset-state",
        action="store_true",
        help=(
            "DESTRUCTIVE: clear treasury history, jobs, reservations and chat/pairing records "
            "before starting; back up data and stop other processes first"
        ),
    )
    state.add_argument(
        "--keep-balance",
        action="store_true",
        help="preserve all existing treasury state (the default; retained for compatibility)",
    )
