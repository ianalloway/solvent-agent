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
from .security import sanitise_prompt_input, safe_report_path, PromptInjectionError, InputValidationError

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "reports"


def fulfill(job: dict) -> dict:
    """Produce the report and return {deliverable_path, text, resources_used}.

    Security:
    - topic and context are sanitised for LLM prompt injection before the
      Nemotron call (raises PromptInjectionError / InputValidationError on
      detected attacks).
    - The output file path is validated against OUTPUT_DIR to prevent path
      traversal via a crafted job ID.
    """
    # PROMPT INJECTION — sanitise all untrusted strings before they enter the LLM
    try:
        topic = sanitise_prompt_input(job.get("topic", ""), field_name="topic", max_len=500)
        context = sanitise_prompt_input(
            job.get("context", "n/a") or "n/a", field_name="context", max_len=2_000
        ) if job.get("context") else "n/a"
    except (PromptInjectionError, InputValidationError) as exc:
        raise ValueError(f"job input rejected by security layer: {exc}") from exc

    system = (
        "You are SOLVENT, a disciplined sell-side research analyst. Produce a "
        "concise, decision-ready brief. Be specific and flag uncertainty."
    )
    user = f"{topic}\n\nClient context: {context}"

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

    # PATH TRAVERSAL — validate job ID before using it as a filename
    path = safe_report_path(OUTPUT_DIR, job["id"])
    path.write_text(text)

    return {"deliverable_path": str(path), "text": text, "resources_used": resources, "tokens": tokens}

