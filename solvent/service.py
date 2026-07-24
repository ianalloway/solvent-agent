"""
service.py — the actual product SOLVENT sells: an on-demand research brief.

Fulfilling a job consumes real resources (Nemotron inference, data pulls,
PDF render, email delivery). The service returns the finished deliverable plus
an itemized list of resources consumed, which the agent then pays for via
Stripe on the SPEND side. This is what ties COGS to each unit of revenue.
"""

from __future__ import annotations

import time

from . import nemotron
from .paths import reports_dir
from .pricing import get_resource_costs
from .security import (
    InputValidationError,
    PromptInjectionError,
    safe_report_path,
    sanitise_prompt_input,
)

OUTPUT_DIR = reports_dir()


def _resources_from_usage(
    usage: dict,
    tool_ctx,
    *,
    include_delivery: bool = True,
) -> list[tuple[str, int, str]]:
    costs = get_resource_costs()
    tokens = usage.get("total_tokens", 0)
    resources = [
        (
            "nvidia-nemotron",
            round((tokens / 1000) * costs["nemotron_tokens_per_1k"]),
            f"Nemotron inference ({tokens} tokens)",
        ),
        (
            "market-data-api",
            tool_ctx.market_data_calls * costs["market_data_call"],
            f"{tool_ctx.market_data_calls} market-data pulls",
        ),
        (
            "web-search-api",
            tool_ctx.web_search_calls * costs["web_search_call"],
            f"{tool_ctx.web_search_calls} web searches",
        ),
        ("pdf-render-saas", costs["pdf_render"], "Render brief to PDF"),
    ]
    if include_delivery:
        resources.append(
            ("email-delivery-saas", costs["email_send"], "Deliver to customer")
        )
    return resources


def fulfill(job: dict) -> dict:
    """Produce the report and return deliverable metadata + resources_used."""
    try:
        topic = sanitise_prompt_input(job.get("topic", ""), field_name="topic", max_len=500)
        context = sanitise_prompt_input(
            job.get("context", "n/a") or "n/a", field_name="context", max_len=2_000
        ) if job.get("context") else "n/a"
    except (PromptInjectionError, InputValidationError) as exc:
        raise ValueError(f"job input rejected by security layer: {exc}") from exc

    started = time.time()
    text, usage, tool_ctx = nemotron.research_brief(topic, context)
    fulfillment_seconds = time.time() - started

    resources = _resources_from_usage(usage, tool_ctx)
    actual_cost = sum(r[1] for r in resources)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = safe_report_path(OUTPUT_DIR, job["id"])
    path.write_text(text)

    from .delivery import markdown_to_html
    html_content = markdown_to_html(text, job_id=job["id"], topic=job.get("topic"))
    html_path = path.with_suffix(".html")
    html_path.write_text(html_content, encoding="utf-8")

    return {
        "deliverable_path": str(path),
        "text": text,
        "resources_used": resources,
        "tokens": usage.get("total_tokens", 0),
        "usage": usage,
        "tool_ctx": tool_ctx,
        "actual_cost_cents": actual_cost,
        "fulfillment_seconds": fulfillment_seconds,
    }


def reconcile_cogs(quote, result: dict) -> dict:
    """Compare estimated vs actual COGS after fulfillment."""
    actual = result.get("actual_cost_cents", 0)
    est = quote.est_cost_cents
    price = quote.price_cents
    actual_margin = price - actual
    actual_margin_pct = round(100 * actual_margin / price, 1) if price else 0.0
    drift = actual - est
    warning = drift > est * 0.15 if est > 0 else False
    return {
        "est_cost_cents": est,
        "actual_cost_cents": actual,
        "est_margin_pct": quote.margin_pct,
        "actual_margin_pct": actual_margin_pct,
        "margin_drift_cents": drift,
        "cost_warning": warning,
        "fulfillment_seconds": result.get("fulfillment_seconds", 0),
        "tool_calls": result.get("tool_ctx").total_calls if result.get("tool_ctx") else 0,
    }
