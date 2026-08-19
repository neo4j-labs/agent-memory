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
    from neo4j_agent_memory.core.protocols import LongTermProtocol
    from neo4j_agent_memory.memory.long_term import (
        Entity,
        Fact,
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
    long_term: LongTermProtocol, query: str, cfg: Neo4jRetrievalConfig, *, nams: bool
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


@dataclass
class _EntryRow:
    """One long-term hit, formatted for a Strands ``MemoryEntry``."""

    content: str
    metadata: dict[str, Any]


def _row(
    kind: str, entry_id: Any, entry_type: str, source_metadata: dict[str, Any] | None, content: str
) -> _EntryRow:
    metadata: dict[str, Any] = {"kind": kind, "id": str(entry_id), "type": entry_type}
    score = (source_metadata or {}).get("similarity")
    if score is not None:
        # Bolt sets "similarity" on entities, preferences, and facts alike; NAMS
        # sets it on none. Omit rather than default to 0, which would misrepresent
        # an unscored hit as a bad match.
        metadata["score"] = score
    return _EntryRow(content=content, metadata=metadata)


def _entity_row(entity: Entity) -> _EntryRow:
    return _row("entity", entity.id, entity.full_type or entity.type, entity.metadata, _format_entity(entity))


def _preference_row(preference: Preference) -> _EntryRow:
    return _row("preference", preference.id, preference.category, preference.metadata, _format_preference(preference))


def _fact_row(fact: Fact) -> _EntryRow:
    return _row("fact", fact.id, fact.predicate, fact.metadata, _format_fact(fact))


async def _retrieve_entries(
    long_term: LongTermProtocol,
    query: str,
    *,
    limit: int,
    min_score: float,
    include_entities: bool,
    include_preferences: bool,
    include_facts: bool,
    nams: bool,
) -> list[_EntryRow]:
    """Sibling of ``_retrieve_context``: same fan-out, rows instead of a string.

    Per-kind failures are logged and skipped so one dead index doesn't lose
    the others' hits. NAMS has no preference/fact search endpoints, so those
    are skipped rather than raised on every call.
    """
    wanted: list[tuple[str, bool, Callable[..., Awaitable[list[Any]]], Callable[..., _EntryRow]]] = [
        ("entity", include_entities, long_term.search_entities, _entity_row),
        ("preference", include_preferences and not nams, long_term.search_preferences, _preference_row),
        ("fact", include_facts and not nams, long_term.search_facts, _fact_row),
    ]
    active = [(kind, search, row) for kind, on, search, row in wanted if on]
    results = await asyncio.gather(
        *(search(query, limit=limit, threshold=min_score) for _, search, _ in active),
        return_exceptions=True,
    )
    rows: list[_EntryRow] = []
    for (kind, _, to_row), result in zip(active, results):
        if isinstance(result, BaseException):
            logger.warning("Long-term %s search failed: %s", kind, result)
            continue
        rows.extend(to_row(item) for item in result)
    return rows
