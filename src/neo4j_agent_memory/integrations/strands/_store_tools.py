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
        # expand_graph is NAMS-only, excluded from LongTermProtocol. Narrow with
        # a real check rather than a cast: inside this branch httpx is installed
        # by definition, so importing the NAMS module here is safe -- the same
        # nested-import shape `for_nams` uses for `build_nams_settings`.
        from neo4j_agent_memory.nams.long_term import NamsLongTermMemory

        long_term = client.long_term
        if not isinstance(long_term, NamsLongTermMemory):
            raise TypeError(
                f"Neo4jMemoryStore: the client reports the NAMS backend, but its "
                f"long_term layer is {type(long_term).__name__}, not "
                f"NamsLongTermMemory -- expand_graph exists only on NAMS."
            )
        expansion = await long_term.expand_graph(str(centre.id))
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
        # Report the orientation the library reports rather than inventing one:
        # GET_ENTITY_RELATIONSHIPS matches undirected and get_related_entities
        # sets source_id to the centre for every hit, so these ids are the only
        # direction available. Likewise `type` is what the library resolved --
        # today always "RELATED_TO", because execute_read's result.data()
        # flattens a relationship to (start, type, end) and drops its
        # properties, so the property-level type never survives the round trip.
        # A library-side fix would flow through here unchanged.
        names = {
            str(centre.id): centre.display_name,
            str(other.id): other.display_name,
        }
        edges.append(
            {
                "from": names.get(str(relationship.source_id), centre.display_name),
                "relationship": relationship.type,
                "to": names.get(str(relationship.target_id), other.display_name),
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
    risk). It *is* on ``LongTermProtocol`` (``core/protocols.py``), keyword-only
    ``user_identifier`` and all, so no cast is needed here.
    """
    preferences = await client.long_term.get_preferences_for(
        user_identifier=user_id, active_only=True
    )
    if category:
        preferences = [p for p in preferences if p.category.lower() == category.lower()]
    return [
        {"category": p.category, "preference": p.preference, "context": p.context}
        for p in preferences[:limit]
    ]


def _tool_prefix(name: str) -> str:
    """The store's name, reduced to something legal in a tool name.

    Tool names are namespaced per store so they can coexist both with
    ``context_graph_tools``' identically-named tools and with a second
    store's (``dataclasses.replace(config, name="team")`` is the documented
    way to run personal / team / org stores side by side).
    """
    slug = "".join(char if char.isalnum() else "_" for char in name.lower()).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "store"


def build_store_tools(store: Neo4jMemoryStore) -> list[AgentTool]:
    """Build the store's graph tools, gated by what the backend exposes.

    Names are prefixed with the store's own name. ``ToolRegistry`` silently
    *overwrites* a duplicate name for ``@tool`` functions (its duplicate
    check is skipped whenever ``supports_hot_reload`` is true, which it
    always is for decorated functions), so unprefixed ``get_entity_graph``
    / ``get_user_preferences`` would replace the factory's tools of those
    names -- which take different arguments -- with no warning.
    """
    from strands import tool

    client = store._client
    nams = store.is_nams
    prefix = _tool_prefix(store.name)

    async def _get_entity_graph(entity_name: str, depth: int = 2) -> dict[str, Any]:
        """Explore the graph neighbourhood of an entity.

        Use this to find how an entity connects to others — who works where,
        what happened at which location.

        Args:
            entity_name: The entity to start from.
            depth: How many hops to traverse. Hosted backends traverse one hop
                regardless.
        """
        return await _entity_graph(client, entity_name, depth=max(1, min(depth, 3)), nams=nams)

    # Annotated explicitly as AgentTool: @tool's overloads resolve this correctly
    # under mypy --strict and ty, but some IDEs' inference falls back to the
    # undecorated function's callable type instead of the decorator's declared
    # return type. The explicit annotation on the assignment target sidesteps
    # that inference gap without a cast/ignore/Any.
    get_entity_graph: AgentTool = tool(name=f"{prefix}_get_entity_graph")(_get_entity_graph)

    tools: list[AgentTool] = [get_entity_graph]

    # get_preferences_for requires a user identifier and is bolt-only (NAMS
    # has no preferences endpoint); an unscoped variant is exactly what
    # risked leaking another tenant's preferences, so ship the tool only
    # when both conditions hold. Unscoped recall still reaches the model
    # through the manager's own search_memory, which includes preferences
    # on bolt regardless.
    if not nams and store.user_id:
        user_id = store.user_id

        async def _get_user_preferences(category: str | None = None, limit: int = 20) -> Any:
            """Retrieve the configured user's preferences, optionally filtered by category.

            Returns only preferences belonging to this store's configured
            user -- not a global listing across all users.

            Args:
                category: Optional category such as "food" or "ui".
                limit: Maximum preferences to return.
            """
            return await _user_preferences(client, user_id, category, limit=limit)

        get_user_preferences: AgentTool = tool(name=f"{prefix}_get_user_preferences")(
            _get_user_preferences
        )
        tools.append(get_user_preferences)

    return tools
