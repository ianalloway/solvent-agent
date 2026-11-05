"""Quote-owned resource ceilings, reserved before any metered side effect.

The token allowance covers all prompts, completions, nested summaries and
retries together. Failed/ambiguous requests keep their reservation. ContextVar
keeps concurrent jobs isolated while carrying the budget into provider sinks.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass


class BudgetExceeded(RuntimeError):
    """The quoted allowance cannot fund another operation."""


def scope_for(job: dict) -> dict[str, int]:
    defaults = {"est_tokens": 8_000, "market_data_calls": 2, "web_search_calls": 6}
    floors = {"est_tokens": 4_000, "market_data_calls": 0, "web_search_calls": 3}
    scope = {}
    for key, default in defaults.items():
        value = job.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        scope[key] = max(floors[key], value)
    return scope


@dataclass(frozen=True)
class ResourceContract:
    tokens: int
    web_search: int
    market_data: int
    token_rate: int
    search_rate: int
    data_rate: int
    pdf_rate: int
    email_rate: int
    attempts: int = 2
    tool_calls: int = 20

    @classmethod
    def for_job(cls, job: dict, costs: dict) -> ResourceContract:
        scope = scope_for(job)
        return cls(
            scope["est_tokens"],
            scope["web_search_calls"],
            scope["market_data_calls"],
            costs["nemotron_tokens_per_1k"],
            costs["web_search_call"],
            costs["market_data_call"],
            costs["pdf_render"],
            costs["email_send"],
        )

    def breakdown(self) -> dict[str, int]:
        return {
            "nemotron_inference": round(self.tokens * self.token_rate / 1000),
            "market_data": self.market_data * self.data_rate,
            "web_search": self.web_search * self.search_rate,
            "pdf_render": self.pdf_rate,
            "email_send": self.email_rate,
        }

    def limits(self) -> dict[str, int]:
        return {
            key: getattr(self, key)
            for key in ("tokens", "web_search", "market_data", "attempts", "tool_calls")
        }

    def to_dict(self) -> dict:
        return asdict(self)


def reserve(limits: dict, used: dict, increments: dict) -> dict:
    updated = dict(used)
    for key, amount in increments.items():
        if amount < 0 or used.get(key, 0) + amount > limits[key]:
            raise BudgetExceeded(f"quoted {key} budget exhausted")
        updated[key] = used.get(key, 0) + amount
    return updated


class ResourceBudget:
    def __init__(self, contract: ResourceContract, persist=None):
        self.contract = contract
        self.used: dict[str, int] = {}
        self.persist = persist

    def reserve(self, **increments: int) -> None:
        limits = self.contract.limits()
        self.used = (
            self.persist(limits, increments)
            if self.persist
            else reserve(limits, self.used, increments)
        )

    def completion(self, system: str, user: str) -> int:
        # Byte-level tokenizers cannot produce more text tokens than UTF-8
        # bytes. Reserve a further 256 tokens for the two-message chat template.
        # Do not use the display-only len(text)/4 estimate for admission.
        prompt_bound = len(system.encode("utf-8")) + len(user.encode("utf-8")) + 256
        self.reserve(tokens=prompt_bound + 1200)
        return 1200


_active: ContextVar[ResourceBudget | None] = ContextVar("resource_budget", default=None)


def current_budget() -> ResourceBudget | None:
    return _active.get()


@contextmanager
def using_budget(budget: ResourceBudget):
    token = _active.set(budget)
    try:
        yield budget
    finally:
        _active.reset(token)
