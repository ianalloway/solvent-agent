"""Tests for solvent.money.dollars_to_cents (no more int(x * 100) truncation)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from solvent.money import dollars_to_cents


@pytest.mark.parametrize(
    ("amount", "cents"),
    [
        (19.99, 1999),  # int(19.99 * 100) == 1998
        (0.29, 29),  # int(0.29 * 100) == 28
        (4.35, 435),  # int(4.35 * 100) == 434
        (1.15, 115),
        (8.2, 820),
        (0, 0),
        (0.0, 0),
        (50, 5000),
        (100.0, 10_000),
        ("19.99", 1999),
        ("  49.00 ", 4900),
        ("0.29", 29),
        (Decimal("19.99"), 1999),
        (1.005, 101),  # sub-cent amounts round half up
        (1.004, 100),
        ("2.675", 268),
        (0.001, 0),
        (1e-07, 0),
        (-19.99, -1999),
        ("-0.29", -29),
        (-1.005, -101),  # half away from zero, mirroring positives
        (1_000_000.01, 100_000_001),
    ],
)
def test_dollars_to_cents(amount, cents):
    assert dollars_to_cents(amount) == cents
    assert isinstance(dollars_to_cents(amount), int)


def test_old_truncation_was_wrong():
    # Documents why the helper exists.
    assert int(19.99 * 100) == 1998
    assert dollars_to_cents(19.99) == 1999


@pytest.mark.parametrize(
    "bad", ["abc", "", "   ", "nan", "inf", "-inf", float("nan"), float("inf"), None, True]
)
def test_bad_input_raises_value_error(bad):
    with pytest.raises(ValueError):
        dollars_to_cents(bad)


def test_callers_use_helper():
    """The fixed call sites parse user dollar strings correctly."""
    from solvent.chat import _BUDGET_RE

    m = _BUDGET_RE.search("budget: $19.99")
    assert m and dollars_to_cents(m.group(1)) == 1999


def test_no_truncating_conversion_left_in_package():
    import re
    from pathlib import Path

    import solvent

    pattern = re.compile(r"\bint\(.*\*\s*100\b")
    offenders = []
    for path in Path(solvent.__file__).parent.rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and "round(" not in line and "money.py" not in str(path):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, offenders
