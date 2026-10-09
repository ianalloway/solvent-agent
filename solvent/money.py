"""money.py — one shared dollars -> cents conversion.

``int(dollars * 100)`` truncates binary-float error toward zero, so
``int(19.99 * 100)`` is ``1998``, one cent short of what the user typed.
Every place that turns a user-supplied dollar amount into integer cents should
call :func:`dollars_to_cents` instead.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def dollars_to_cents(amount: int | float | str | Decimal) -> int:
    """Convert a dollar amount to integer cents, rounding half away from zero.

    The value is read through its decimal string form (``Decimal(str(amount))``)
    rather than its binary float value, so ``19.99`` is ``1999`` and ``0.29`` is
    ``29``.  Sub-cent amounts round half up: ``1.005`` is ``101``.  Negative
    amounts mirror positive ones (``-1.005`` is ``-101``).

    Raises:
        ValueError: if *amount* is not a finite number (``nan``, ``inf``,
            garbage text, ``None``, a bool).  ``ValueError`` matches what
            ``float()`` raised for bad input, so existing ``except ValueError``
            handlers keep working.
    """
    if isinstance(amount, bool) or amount is None:
        raise ValueError(f"not a dollar amount: {amount!r}")
    try:
        value = amount if isinstance(amount, Decimal) else Decimal(str(amount).strip())
    except InvalidOperation:
        raise ValueError(f"not a dollar amount: {amount!r}") from None
    if not value.is_finite():
        raise ValueError(f"not a finite dollar amount: {amount!r}")
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
