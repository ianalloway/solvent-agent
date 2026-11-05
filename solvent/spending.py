"""Pre-consumption authorization at live provider boundaries.

Each HTTP attempt reserves its maximum charge before I/O. Unknown outcomes
retain the full charge; only validated usage releases unused headroom. The
SQLite liability survives worker death, and settlement books the charge before
the caller can deliver anything. Prices use the operator's resource cost model.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from .guardrails import GuardrailError, Guardrails
from .pricing import get_resource_costs
from .resource_budget import MAX_COMPLETION_TOKENS, current_budget
from .treasury import Treasury

_scope = ContextVar("spend_scope", default=None)


@contextmanager
def scope(guard, job_id, margin):
    token = _scope.set((guard, job_id, margin))
    try:
        yield
    finally:
        _scope.reset(token)


def _authorize(amount, vendor, memo):
    guard, job_id, margin = _scope.get() or (Guardrails(Treasury()), None, None)
    # A deliberately configured free resource needs no monetary reservation.
    if not amount:
        return guard.t, None
    return guard.t, guard.reserve(amount, vendor, memo, job_id=job_id, margin=margin)


def metered_model(fn):
    """Wrap one request, not its retry loop (each attempt can incur a charge)."""
    @wraps(fn)
    def wrapped(system, user):
        budget = current_budget()
        if budget:
            # Refuse an exhausted quote before reserving money: no request
            # will run, so it must not become an ambiguous provider charge.
            budget.completion(system, user)
        # UTF-8 bytes upper-bound byte-tokenized input; include chat framing.
        # Both adapters send the same hard completion cap used here.
        max_tokens = len(system.encode("utf-8")) + len(user.encode("utf-8")) + 256
        max_tokens += MAX_COMPLETION_TOKENS
        rate = budget.contract.token_rate if budget else get_resource_costs()["nemotron_tokens_per_1k"]
        ceiling = (max_tokens * rate + 999) // 1000
        treasury, reservation = _authorize(ceiling, "nvidia-nemotron", "Live inference")
        actual = ceiling
        try:
            result = fn(system, user)
            usage = result.get("usage") if isinstance(result, dict) else result[1]
            if usage:
                counts = [usage.get(k) for k in (
                    "prompt_tokens", "completion_tokens", "total_tokens"
                )]
                if (
                    any(type(n) is not int or n < 0 for n in counts)
                    or counts[2] < counts[0] + counts[1]
                    or counts[2] > max_tokens
                    or counts[1] > MAX_COMPLETION_TOKENS
                ):
                    raise GuardrailError("provider usage exceeds or invalidates authorization")
                actual = (counts[2] * rate + 999) // 1000
            return result
        finally:
            # A timeout/invalid response may still have been billed. Never
            # release its headroom merely because the caller falls back/retries.
            if reservation:
                treasury.settle_spend(reservation, actual)
    return wrapped


def metered_tool(vendor, cost_key):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            budget = current_budget()
            if budget:
                field = {"web_search_call": "search_rate", "market_data_call": "data_rate"}[cost_key]
                amount = getattr(budget.contract, field)
            else:
                amount = get_resource_costs()[cost_key]
            treasury, reservation = _authorize(amount, vendor, "Live " + fn.__name__)
            try:
                return fn(*args, **kwargs)
            finally:
                if reservation:
                    treasury.settle_spend(reservation, amount)
        return wrapped
    return decorate
