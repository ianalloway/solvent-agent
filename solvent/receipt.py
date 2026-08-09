"""
receipt.py — formatted job receipts for delivery via Telegram / email / HTTP.
"""

from __future__ import annotations

_RULE = "━" * 33


def _margin_pct(revenue_cents: int, pnl_cents: int) -> float:
    """Return margin % or 0 if revenue is zero."""
    if revenue_cents == 0:
        return 0.0
    return pnl_cents / revenue_cents * 100


def build_receipt(job: dict, pnl_cents: int, balance_cents: int) -> str:
    """Build a plaintext/markdown receipt string for a completed or failed job.

    Args:
        job: Job dict containing at least ``id``, ``topic``, ``budget_cents``
             and ``status`` keys (as stored/returned by Treasury).
        pnl_cents: Net profit & loss for this job in cents (may be negative).
        balance_cents: Current treasury balance in cents after booking.

    Returns:
        Multi-line string suitable for Telegram Markdown or plain text.
    """
    job_id: str = job.get("id", "")
    topic: str = (job.get("topic") or "")[:80]
    status: str = job.get("status", "unknown")
    revenue_cents: int = int(job.get("revenue_cents", 0) or job.get("budget_cents", 0) or 0)
    cogs_cents: int = revenue_cents - pnl_cents
    margin_pct = _margin_pct(revenue_cents, pnl_cents)

    return (
        f"{_RULE}\n"
        f"SOLVENT Receipt — Job {job_id[:8]}\n"
        f"{_RULE}\n"
        f"Topic:    {topic}\n"
        f"Status:   {status}\n"
        f"Earned:   ${revenue_cents / 100:.2f}\n"
        f"COGS:     ${cogs_cents / 100:.2f}\n"
        f"Net P&L:  ${pnl_cents / 100:.2f} ({margin_pct:.0f}% margin)\n"
        f"Balance:  ${balance_cents / 100:.2f}\n"
        f"\n"
        f"Ref: {job_id}\n"
        f"{_RULE}"
    )


def build_html_receipt(job: dict, pnl_cents: int, balance_cents: int) -> str:
    """Return the same receipt content as styled HTML suitable for email.

    Args:
        job: Job dict (same shape as :func:`build_receipt`).
        pnl_cents: Net P&L in cents.
        balance_cents: Current treasury balance in cents.

    Returns:
        Full HTML document string.
    """
    job_id: str = job.get("id", "")
    topic: str = (job.get("topic") or "")[:80]
    status: str = job.get("status", "unknown")
    revenue_cents: int = int(job.get("revenue_cents", 0) or job.get("budget_cents", 0) or 0)
    cogs_cents: int = revenue_cents - pnl_cents
    margin_pct = _margin_pct(revenue_cents, pnl_cents)

    def row(label: str, value: str) -> str:
        return (
            f"<tr>"
            f"<td style='padding:4px 12px 4px 0;color:#888;font-weight:bold'>{label}</td>"
            f"<td style='padding:4px 0'>{value}</td>"
            f"</tr>"
        )

    rows = "".join(
        [
            row("Topic", topic),
            row("Status", status),
            row("Earned", f"${revenue_cents / 100:.2f}"),
            row("COGS", f"${cogs_cents / 100:.2f}"),
            row("Net P&amp;L", f"${pnl_cents / 100:.2f} ({margin_pct:.0f}% margin)"),
            row("Balance", f"${balance_cents / 100:.2f}"),
            row("Ref", job_id),
        ]
    )

    return (
        "<html>"
        "<body style='font-family:monospace;background:#f5f5f5;padding:24px'>"
        "<div style='max-width:480px;margin:0 auto;background:#fff;"
        "border-radius:8px;padding:24px;border:1px solid #ddd'>"
        f"<h2 style='margin-top:0;border-bottom:2px solid #333;padding-bottom:8px'>"
        f"SOLVENT Receipt — Job {job_id[:8]}</h2>"
        f"<table style='border-collapse:collapse;width:100%'>{rows}</table>"
        "</div>"
        "</body>"
        "</html>"
    )


def build_refund_receipt(job: dict, refund_cents: int) -> str:
    """Build a plaintext refund receipt.

    Args:
        job: Job dict.
        refund_cents: Amount refunded in cents.

    Returns:
        Multi-line receipt string mentioning the refund.
    """
    job_id: str = job.get("id", "")
    topic: str = (job.get("topic") or "")[:80]
    reason: str = (job.get("error_reason") or "fulfillment error")[:120]

    return (
        f"{_RULE}\n"
        f"SOLVENT Refund Receipt — Job {job_id[:8]}\n"
        f"{_RULE}\n"
        f"Topic:    {topic}\n"
        f"Status:   refunded\n"
        f"Refund:   ${refund_cents / 100:.2f}\n"
        f"Reason:   {reason}\n"
        f"\n"
        f"Ref: {job_id}\n"
        f"{_RULE}"
    )
