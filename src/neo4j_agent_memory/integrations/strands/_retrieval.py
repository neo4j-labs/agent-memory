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


def _preference_search(
    long_term: LongTermProtocol, user_id: str | None
) -> Callable[..., Awaitable[list[Any]]]:
    """The preference lookup that is safe for this store's tenancy.

    ``search_preferences`` takes no user identifier and applies no ``:User``
    filter, so on a user-scoped construct it can return another tenant's
    preferences. ``get_preferences_for`` is the only user-scoped primitive on
    ``LongTermProtocol``; it is a listing rather than a search, so a scoped
    lookup trades query relevance for tenancy correctness and returns the
    user's active preferences up to ``limit``.
    """
    if user_id is None:
        return long_term.search_preferences

    async def _scoped(query: str, *, limit: int, threshold: float) -> list[Any]:
        preferences = await long_term.get_preferences_for(user_identifier=user_id, active_only=True)
        return preferences[:limit]

    return _scoped


async def _retrieve_context(
    long_term: LongTermProtocol,
    query: str,
    cfg: Neo4jRetrievalConfig,
    *,
    nams: bool,
    user_id: str | None = None,
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
        (
            cfg.include_preferences and not nams,
            _preference_search(long_term, user_id),
            _format_preference,
        ),
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
    *,
    kind: str,
    entry_id: Any,
    entry_type: str,
    source_metadata: dict[str, Any] | None,
    content: str,
) -> _EntryRow:
    """Build one row. Keyword-only: three of five params are plain strings,
    so a transposition would otherwise type-check silently."""
    metadata: dict[str, Any] = {"kind": kind, "id": str(entry_id), "type": entry_type}
    score = (source_metadata or {}).get("similarity")
    if score is not None:
        # Bolt sets "similarity" on entities, preferences, and facts alike; NAMS
        # sets it on none. Omit rather than default to 0, which would misrepresent
        # an unscored hit as a bad match.
        metadata["score"] = score
    return _EntryRow(content=content, metadata=metadata)


def _entity_row(entity: Entity) -> _EntryRow:
    return _row(
        kind="entity",
        entry_id=entity.id,
        entry_type=entity.full_type or entity.type,
        source_metadata=entity.metadata,
        content=_format_entity(entity),
    )


def _preference_row(preference: Preference) -> _EntryRow:
    return _row(
        kind="preference",
        entry_id=preference.id,
        entry_type=preference.category,
        source_metadata=preference.metadata,
        content=_format_preference(preference),
    )


def _fact_row(fact: Fact) -> _EntryRow:
    return _row(
        kind="fact",
        entry_id=fact.id,
        entry_type=fact.predicate,
        source_metadata=fact.metadata,
        content=_format_fact(fact),
    )


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
    user_id: str | None = None,
) -> list[_EntryRow]:
    """Sibling of ``_retrieve_context``: same fan-out, rows instead of a string.

    ``limit`` caps the *total* rows returned, not each kind: Strands treats a
    store's result count as a per-store cap (``MemoryManager.search``) and
    injection then slices the concatenation to the same number, so returning
    ``limit`` rows per kind would let a saturated entity search crowd
    preferences and facts out of the model's context entirely. The budget is
    handed out round-robin, so every enabled kind with a hit is represented
    before any kind takes a second row, and unused capacity flows to whoever
    has more hits.

    Per-kind failures are logged and skipped so one dead index doesn't lose
    the others' hits. NAMS has no preference/fact search endpoints, so those
    are skipped rather than raised on every call.
    """
    wanted: list[
        tuple[str, bool, Callable[..., Awaitable[list[Any]]], Callable[..., _EntryRow]]
    ] = [
        ("entity", include_entities, long_term.search_entities, _entity_row),
        (
            "preference",
            include_preferences and not nams,
            _preference_search(long_term, user_id),
            _preference_row,
        ),
        ("fact", include_facts and not nams, long_term.search_facts, _fact_row),
    ]
    active = [(kind, search, row) for kind, on, search, row in wanted if on]
    results = await asyncio.gather(
        *(search(query, limit=limit, threshold=min_score) for _, search, _ in active),
        return_exceptions=True,
    )
    per_kind: list[list[_EntryRow]] = []
    for (kind, _, to_row), result in zip(active, results):
        if isinstance(result, BaseException):
            logger.warning("Long-term %s search failed: %s", kind, result)
            per_kind.append([])
            continue
        per_kind.append([to_row(item) for item in result])

    rows: list[_EntryRow] = []
    for share, hits in zip(_share_budget([len(hits) for hits in per_kind], limit), per_kind):
        rows.extend(hits[:share])
    return rows


def _share_budget(counts: list[int], limit: int) -> list[int]:
    """Split ``limit`` rows across kinds, round-robin, so none is starved.

    One row to each kind that still has hits, then a second to each, and so
    on until the budget or the hits run out — which also means a kind with
    more hits absorbs whatever the others leave unused.
    """
    take = [0] * len(counts)
    remaining = min(limit, sum(counts))
    while remaining > 0:
        for index, available in enumerate(counts):
            if remaining == 0:
                break
            if take[index] < available:
                take[index] += 1
                remaining -= 1
    return take
