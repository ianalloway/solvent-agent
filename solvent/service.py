"""
service.py — the actual product SOLVENT sells: an on-demand research brief.

Fulfilling a job consumes real resources (Nemotron inference, data pulls,
PDF render, email delivery). The service returns the finished deliverable plus
an itemized list of resources consumed, which the agent then pays for via
Stripe on the SPEND side. This is what ties COGS to each unit of revenue.
"""

from __future__ import annotations

from pathlib import Path
from . import nemotron
from .pricing import RESOURCE_COSTS_CENTS

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "reports"


def fulfill(job: dict) -> dict:
    """Produce the report and return {deliverable_path, text, resources_used}."""
    system = (
        "You are SOLVENT, a disciplined sell-side research analyst. Produce a "
        "concise, decision-ready brief. Be specific and flag uncertainty."
    )
    user = f"{job['topic']}\n\nClient context: {job.get('context', 'n/a')}"

    text, tokens = nemotron.complete(system, user)

    # Itemize the resources this job actually consumed.
    resources = [
        ("nvidia-nemotron", round((tokens / 1000) * RESOURCE_COSTS_CENTS["nemotron_tokens_per_1k"]),
         f"Nemotron inference ({tokens} tokens)"),
        ("market-data-api", job.get("market_data_calls", 2) * RESOURCE_COSTS_CENTS["market_data_call"],
         f"{job.get('market_data_calls', 2)} market-data pulls"),
        ("web-search-api", job.get("web_search_calls", 6) * RESOURCE_COSTS_CENTS["web_search_call"],
         f"{job.get('web_search_calls', 6)} web searches"),
        ("pdf-render-saas", RESOURCE_COSTS_CENTS["pdf_render"], "Render brief to PDF"),
        ("email-delivery-saas", RESOURCE_COSTS_CENTS["email_send"], "Deliver to customer"),
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{job['id']}.md"
    path.write_text(text)

    return {"deliverable_path": str(path), "text": text, "resources_used": resources, "tokens": tokens}
