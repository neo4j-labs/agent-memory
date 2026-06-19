"""Long-term memory retrieval and context-block formatting.

One concern: given a long-term memory layer and a retrieval config,
produce the ``<context_tag>`` block injected into a user turn. Knows
nothing about Strands sessions, hooks, or buffering.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory.memory.long_term import (
        Entity,
        Fact,
        LongTermMemory,
        Preference,
    )

logger = logging.getLogger(__name__)


@dataclass
class Neo4jRetrievalConfig:
    """Opt-in per-turn long-term memory injection settings.

    When passed to :class:`Neo4jSessionManager`, each user message
    triggers concurrent long-term searches and the results are prepended
    to the message in-memory inside a ``<context_tag>`` block. The stored
    message is always the user's original.
    """

    top_k: int = 10
    min_score: float = 0.2  # (bolt only; not enforced on NAMS)
    include_entities: bool = True
    include_preferences: bool = True
    include_facts: bool = False
    context_tag: str = "user_context"


def _format_entity(entity: Entity) -> str:
    desc = entity.description
    suffix = f" — {desc}" if desc else ""
    entity_type = entity.full_type or entity.type
    return f"[entity] {entity.display_name} ({entity_type}){suffix}"


def _format_preference(preference: Preference) -> str:
    return f"[preference] {preference.category}: {preference.preference}"


def _format_fact(fact: Fact) -> str:
    return f"[fact] {fact.subject} {fact.predicate} {fact.object}"


async def _retrieve_context(
    long_term: LongTermMemory, query: str, cfg: Neo4jRetrievalConfig, *, nams: bool
) -> str:
    """Run the configured long-term searches concurrently and format the block.

    Returns ``""`` when nothing relevant is found (no empty tags).
    Individual search failures are logged and skipped — a memory lookup
    must never break the agent's turn.
    """
    # A heterogeneous dispatch table: each row pairs a long-term search with the
    # formatter for its result type. The per-type formatters above are precisely
    # typed; the table itself can only be typed at the loose ``Callable[...]``
    # supertype because the rows hold different concrete signatures.
    # NAMS has no preference/fact search endpoints — skip rather than warn every turn.
    wanted: list[tuple[bool, Callable[..., Awaitable[list[Any]]], Callable[..., str]]] = [
        (cfg.include_entities, long_term.search_entities, _format_entity),
        (cfg.include_preferences and not nams, long_term.search_preferences, _format_preference),
        (cfg.include_facts and not nams, long_term.search_facts, _format_fact),
    ]
    searches = [s(query, limit=cfg.top_k, threshold=cfg.min_score) for on, s, _ in wanted if on]
    formatters = [f for on, _, f in wanted if on]
    results = await asyncio.gather(*searches, return_exceptions=True)
    lines: list[str] = []
    for formatter, result in zip(formatters, results):
        if isinstance(result, BaseException):
            logger.warning("Long-term memory search failed: %s", result)
            continue
        lines.extend(formatter(item) for item in result)
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"<{cfg.context_tag}>\nRelevant memory:\n{body}\n</{cfg.context_tag}>"
