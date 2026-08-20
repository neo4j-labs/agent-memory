"""Graph-native @tool functions bound to one memory store's client.

The tools a ``MemoryManager`` cannot provide: multi-hop traversal and
scoped preference lookup. Deliberately excludes search/add, which the
manager owns as ``search_memory`` / ``add_memory`` — and ``add_memory`` is
already the name ``context_graph_tools`` uses, so re-exposing it here would
collide.

Unlike ``tools.py``, these bind to the store's own client instead of the
factory's per-call cached clients, so nothing can close a transport the store
is still using.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from strands.types.tools import AgentTool

    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.integrations.strands.memory_store import Neo4jMemoryStore
    from neo4j_agent_memory.memory.long_term import LongTermMemory
    from neo4j_agent_memory.nams.long_term import NamsLongTermMemory

_MAX_EDGES = 50


async def _entity_graph(
    client: MemoryClient, entity_name: str, *, depth: int, nams: bool
) -> dict[str, Any]:
    """Return the neighbourhood of a named entity.

    bolt traverses ``get_related_entities`` to ``depth``; NAMS exposes only a
    1-hop ``expand_graph`` keyed by node id, so the name is resolved through
    entity search first and the reported depth is 1.
    """
    matches = await client.long_term.search_entities(entity_name, limit=1)
    if not matches:
        return {"error": f"entity not found: {entity_name}"}
    centre = matches[0]

    if nams:
        # expand_graph is NAMS-only, excluded from LongTermProtocol -- cast to
        # the concrete class so the call stays checked instead of untyped.
        nams_long_term = cast("NamsLongTermMemory", client.long_term)
        expansion = await nams_long_term.expand_graph(str(centre.id))
        return {
            "center": centre.display_name,
            "depth": 1,
            "nodes": list(expansion.get("nodes") or [])[:_MAX_EDGES],
            "edges": list(expansion.get("edges") or [])[:_MAX_EDGES],
        }

    # get_related_entities' depth kwarg diverges from LongTermProtocol's
    # portable, no-depth signature (core/protocols.py documents this as
    # deliberate) -- cast to the concrete class so the call stays checked.
    bolt_long_term = cast("LongTermMemory", client.long_term)
    related = await bolt_long_term.get_related_entities(centre, depth=depth)
    nodes = [{"name": centre.display_name, "type": centre.type, "is_center": True}]
    edges: list[dict[str, str]] = []
    for other, relationship in related[:_MAX_EDGES]:
        nodes.append({"name": other.display_name, "type": other.type, "is_center": False})
        edges.append(
            {
                "from": other.display_name,
                "relationship": getattr(relationship, "relationship_type", "RELATED_TO"),
                "to": centre.display_name,
            }
        )
    return {"center": centre.display_name, "depth": depth, "nodes": nodes, "edges": edges}


async def _user_preferences(
    client: MemoryClient, user_id: str, category: str | None, *, limit: int
) -> list[dict[str, Any]]:
    """Return the configured user's preferences, optionally narrowed to one category.

    ``get_preferences_for`` is user-scoped and needs no embedder, unlike
    ``search_preferences`` (which returns ``[]`` with no embedder and no
    ``:User`` filter at all -- both a silent-empty and a cross-tenant-leak
    risk). It is not on ``LongTermProtocol`` either, so cast to the concrete
    class.
    """
    bolt_long_term = cast("LongTermMemory", client.long_term)
    preferences = await bolt_long_term.get_preferences_for(user_id, active_only=True)
    if category:
        preferences = [p for p in preferences if p.category.lower() == category.lower()]
    return [
        {"category": p.category, "preference": p.preference, "context": p.context}
        for p in preferences[:limit]
    ]


def build_store_tools(store: Neo4jMemoryStore) -> list[AgentTool]:
    """Build the store's graph tools, gated by what the backend exposes."""
    from strands import tool

    client = store._client
    nams = store.is_nams

    @tool
    async def get_entity_graph(entity_name: str, depth: int = 2) -> dict[str, Any]:
        """Explore the graph neighbourhood of an entity.

        Use this to find how an entity connects to others — who works where,
        what happened at which location.

        Args:
            entity_name: The entity to start from.
            depth: How many hops to traverse. Hosted backends traverse one hop
                regardless.
        """
        return await _entity_graph(client, entity_name, depth=max(1, min(depth, 3)), nams=nams)

    tools: list[AgentTool] = [get_entity_graph]

    # get_preferences_for requires a user identifier and is bolt-only (NAMS
    # has no preferences endpoint); an unscoped variant is exactly what
    # risked leaking another tenant's preferences, so ship the tool only
    # when both conditions hold. Unscoped recall still reaches the model
    # through the manager's own search_memory, which includes preferences
    # on bolt regardless.
    if not nams and store.user_id:
        user_id = store.user_id

        @tool
        async def get_user_preferences(category: str | None = None, limit: int = 20) -> Any:
            """Retrieve the configured user's preferences, optionally filtered by category.

            Returns only preferences belonging to this store's configured
            user -- not a global listing across all users.

            Args:
                category: Optional category such as "food" or "ui".
                limit: Maximum preferences to return.
            """
            return await _user_preferences(client, user_id, category, limit=limit)

        tools.append(get_user_preferences)

    return tools
