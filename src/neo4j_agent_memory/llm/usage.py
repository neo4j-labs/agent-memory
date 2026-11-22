"""A process-wide count of tokens used, by model.

The OpenAI adapters add to it on every request, chat and embedding alike, so
a caller can say what a job cost without holding on to every provider object
the client built along the way::

    from neo4j_agent_memory.llm import usage

    before = usage.snapshot()
    await client.distillation.distill_session(session_id)
    spent = usage.since(before)
    # {"gpt-5-2025-08-07": {"requests": 412, "input_tokens": ..., ...}}

The counts are per process and are not persisted. Reasoning tokens are part
of the output tokens; they are also counted on their own because they are the
part of a reasoning model's bill that the visible answers do not explain.
"""

from __future__ import annotations

from typing import Any

FIELDS = ("requests", "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens")

_TOTALS: dict[str, dict[str, int]] = {}


def record(
    model: str,
    *,
    input_tokens: int = 0,
    cached_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    requests: int = 1,
) -> None:
    """Add one request's tokens to the count for ``model``."""
    totals = _TOTALS.setdefault(model, dict.fromkeys(FIELDS, 0))
    totals["requests"] += requests
    totals["input_tokens"] += input_tokens
    totals["cached_tokens"] += cached_tokens
    totals["output_tokens"] += output_tokens
    totals["reasoning_tokens"] += reasoning_tokens


def record_response(model: str, usage: Any) -> None:
    """Add an OpenAI ``usage`` object (chat or embedding) to the count.

    A response without usage still counts as a request.
    """
    if usage is None:
        record(model)
        return
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    record(
        model,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        cached_tokens=getattr(prompt_details, "cached_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", 0) or 0,
    )


def snapshot() -> dict[str, dict[str, int]]:
    """The counts so far, by model, as a copy."""
    return {model: dict(totals) for model, totals in _TOTALS.items()}


def since(before: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """What was used after ``before`` (a :func:`snapshot`), by model."""
    spent: dict[str, dict[str, int]] = {}
    for model, totals in _TOTALS.items():
        earlier = before.get(model, {})
        delta = {field: totals[field] - earlier.get(field, 0) for field in FIELDS}
        if any(delta.values()):
            spent[model] = delta
    return spent


def reset() -> None:
    """Forget every count. For tests."""
    _TOTALS.clear()


__all__ = ["FIELDS", "record", "record_response", "reset", "since", "snapshot"]
