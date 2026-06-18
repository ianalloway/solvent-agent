#!/usr/bin/env python3
"""Compatibility wrapper for running the SOLVENT demo from a source checkout."""

from solvent.cli import *  # noqa: F403
from solvent.cli import main


if __name__ == "__main__":
    main()
