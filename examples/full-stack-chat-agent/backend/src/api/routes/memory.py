"""Memory management API endpoints.

Reads go through ``client.query.cypher()`` — the supported, backend-portable
read path (it works on bolt *and* on the hosted NAMS backend) rather than the
private ``client._client.execute_read``. The one genuine write uses the public
``client.graph.execute_write``.

The only remaining bolt-only call in this module is ``client.get_graph()``,
which the NAMS backend does not implement by design.
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from neo4j_agent_memory.memory.long_term import Preference as LongTermPreference
from src.api.schemas import (
    Entity,
    GraphNode,
    GraphRelationship,
    MemoryContext,
    MemoryGraph,
    Preference,
    PreferenceRequest,
    RecentMessage,
)
from src.memory.client import get_memory_client

router = APIRouter()
logger = logging.getLogger(__name__)

LIST_PREFERENCES = """
MATCH (p:Preference)
WHERE $category IS NULL OR p.category = $category
RETURN p
ORDER BY p.created_at DESC
LIMIT $limit
"""

# `search_entities()` is pure vector search, so it cannot answer "list the
# entities" (an empty query embedding matches nothing above the threshold).
# These two list/scope the entities instead.
LIST_ENTITIES = """
MATCH (e:Entity)
WHERE $type IS NULL OR e.type = $type
RETURN e
ORDER BY e.created_at DESC
LIMIT $limit
"""

THREAD_ENTITIES = """
MATCH (:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity)
WITH e, count(m) AS mention_count
RETURN e, mention_count
ORDER BY mention_count DESC
LIMIT $limit
"""

# TODO(neo4j-agent-memory): replace with long_term.delete_preference() when the
# library ships it — tracked as a follow-up from the examples review.
DELETE_PREFERENCE = "MATCH (p:Preference {id: $id}) DETACH DELETE p RETURN p.id AS id"

# list_traces() returns traces *without* steps by design, so step/tool-call
# counts are aggregated here in one extra round trip rather than N+1
# get_trace_with_steps() calls.
TRACE_STEP_COUNTS = """
MATCH (rt:ReasoningTrace) WHERE rt.id IN $ids
OPTIONAL MATCH (rt)-[:HAS_STEP]->(s:ReasoningStep)
OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
RETURN rt.id AS id,
       count(DISTINCT s) AS step_count,
       count(DISTINCT tc) AS tool_call_count
"""

NODE_BY_ID = """
MATCH (n) WHERE n.id = $node_id
RETURN n.id AS id, labels(n) AS labels, properties(n) AS props
LIMIT 1
"""

NEIGHBORS_DEPTH_1 = """
MATCH (n)-[r]-(neighbor) WHERE n.id = $node_id
RETURN neighbor.id AS neighbor_id,
       labels(neighbor) AS neighbor_labels,
       properties(neighbor) AS neighbor_props,
       type(r) AS rel_type,
       elementId(r) AS rel_id,
       properties(r) AS rel_props,
       startNode(r).id AS start_id,
       endNode(r).id AS end_id
LIMIT $limit
"""

NEIGHBORS_DEPTH_2 = """
MATCH path = (n)-[*1..2]-(neighbor) WHERE n.id = $node_id AND neighbor <> n
WITH neighbor, relationships(path) AS rels
UNWIND rels AS r
RETURN DISTINCT neighbor.id AS neighbor_id,
       labels(neighbor) AS neighbor_labels,
       properties(neighbor) AS neighbor_props,
       type(r) AS rel_type,
       elementId(r) AS rel_id,
       properties(r) AS rel_props,
       startNode(r).id AS start_id,
       endNode(r).id AS end_id
LIMIT $limit
"""


def _entity_from_node(node: Any) -> Entity:
    """Build the API Entity schema from a raw Neo4j node map."""
    data = dict(node)
    return Entity(
        id=str(data.get("id", "")),
        name=data.get("name", ""),
        type=data.get("type", "UNKNOWN"),
        subtype=data.get("subtype"),
        description=data.get("description"),
    )


def _preference_from_node(node: Any) -> LongTermPreference:
    """Rebuild a Preference model from a raw Neo4j node map."""
    data = dict(node)
    return LongTermPreference(
        id=UUID(data["id"]),
        category=data.get("category", "general"),
        preference=data.get("preference", ""),
        context=data.get("context"),
        confidence=data.get("confidence", 1.0),
    )


@router.get("/memory/context", response_model=MemoryContext)
async def get_memory_context(
    thread_id: str | None = None,
    query: str | None = None,
) -> MemoryContext:
    """Get memory context for display.

    Args:
        thread_id: Optional thread ID to scope the context.
        query: Optional query to find relevant memories.
    """
    preferences: list[Preference] = []
    entities: list[Entity] = []
    recent_topics: list[str] = []
    recent_messages: list[RecentMessage] = []

    memory = get_memory_client()
    if memory is None:
        return MemoryContext(
            preferences=preferences,
            entities=entities,
            recent_topics=recent_topics,
            recent_messages=recent_messages,
        )

    try:
        if thread_id:
            conversation = await memory.short_term.get_conversation(
                session_id=thread_id,
                limit=10,
            )
            for msg in conversation.messages[-10:]:
                recent_messages.append(
                    RecentMessage(
                        id=str(msg.id),
                        role=msg.role.value,
                        content=msg.content[:200] + ("..." if len(msg.content) > 200 else ""),
                        created_at=msg.created_at.isoformat() if msg.created_at else None,
                    )
                )

        if query:
            pref_results = await memory.long_term.search_preferences(query, limit=10)
        else:
            rows = await memory.query.cypher(LIST_PREFERENCES, {"category": None, "limit": 10})
            pref_results = [_preference_from_node(row["p"]) for row in rows]

        for pref in pref_results:
            preferences.append(
                Preference(
                    id=str(pref.id),
                    category=pref.category,
                    preference=pref.preference,
                    context=pref.context,
                    confidence=pref.confidence,
                    created_at=pref.created_at,
                )
            )

        # Scope entities to the thread when we have one (the MENTIONS edges the
        # extractor wrote), fall back to semantic search for an explicit query,
        # and to a plain listing otherwise.
        if query:
            entities.extend(
                Entity(
                    id=str(ent.id),
                    name=ent.name,
                    type=ent.type,
                    subtype=ent.subtype,
                    description=ent.description,
                )
                for ent in await memory.long_term.search_entities(query, limit=10)
            )
        elif thread_id:
            rows = await memory.query.cypher(
                THREAD_ENTITIES, {"session_id": thread_id, "limit": 10}
            )
            entities.extend(_entity_from_node(row["e"]) for row in rows)
        else:
            rows = await memory.query.cypher(LIST_ENTITIES, {"type": None, "limit": 10})
            entities.extend(_entity_from_node(row["e"]) for row in rows)

    except Exception as e:
        logger.warning("Failed to get memory context: %s", e)

    return MemoryContext(
        preferences=preferences,
        entities=entities,
        recent_topics=recent_topics,
        recent_messages=recent_messages,
    )


@router.get("/preferences", response_model=list[Preference])
async def list_preferences(category: str | None = None) -> list[Preference]:
    """List user preferences, optionally filtered by category.

    Preferences come from two places: explicit ``POST /api/preferences`` calls
    and the ``PreferenceDetector`` that ``MemoryIntegration`` runs in the
    background on every user message (``auto_preferences=True``).
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        rows = await memory.query.cypher(LIST_PREFERENCES, {"category": category, "limit": 50})
    except Exception as e:
        logger.warning("Failed to list preferences: %s", e)
        return []

    preferences: list[Preference] = []
    for row in rows:
        data = dict(row["p"])
        preferences.append(
            Preference(
                id=data["id"],
                category=data.get("category", "general"),
                preference=data.get("preference", ""),
                context=data.get("context"),
                confidence=data.get("confidence", 1.0),
                created_at=None,  # Neo4j datetime needs conversion
            )
        )
    return preferences


@router.post("/preferences", response_model=Preference)
async def add_preference(request: PreferenceRequest) -> Preference:
    """Add a new user preference."""
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    try:
        pref = await memory.long_term.add_preference(
            category=request.category,
            preference=request.preference,
            context=request.context or "Added via API",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return Preference(
        id=str(pref.id),
        category=pref.category,
        preference=pref.preference,
        context=pref.context,
        confidence=pref.confidence,
        created_at=pref.created_at,
    )


@router.delete("/preferences/{preference_id}")
async def delete_preference(preference_id: str) -> dict[str, object]:
    """Delete a preference by ID."""
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    try:
        rows = await memory.graph.execute_write(DELETE_PREFERENCE, {"id": preference_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not rows:
        raise HTTPException(status_code=404, detail="Preference not found")
    return {"status": "deleted", "preference_id": preference_id}


@router.get("/entities", response_model=list[Entity])
async def list_entities(
    type: str | None = None,
    query: str | None = None,
) -> list[Entity]:
    """List extracted entities, optionally filtered by type or search query."""
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        if query:
            # A query means semantic search over entity embeddings.
            results = await memory.long_term.search_entities(
                query,
                entity_types=[type] if type else None,
                limit=50,
            )
            return [
                Entity(
                    id=str(ent.id),
                    name=ent.name,
                    type=ent.type,
                    subtype=ent.subtype,
                    description=ent.description,
                )
                for ent in results
            ]
        rows = await memory.query.cypher(LIST_ENTITIES, {"type": type, "limit": 50})
    except Exception as e:
        logger.warning("Failed to list entities: %s", e)
        return []

    return [_entity_from_node(row["e"]) for row in rows]


@router.get("/memory/graph", response_model=MemoryGraph)
async def get_memory_graph(
    session_id: str | None = None,
    include_embeddings: bool = False,
) -> MemoryGraph:
    """Get the memory graph for visualization.

    Uses the ``get_graph()`` API for efficient graph export. **Bolt only** —
    the hosted NAMS backend raises ``NotSupportedError`` here by design.

    Args:
        session_id: Optional session ID to filter the graph.
        include_embeddings: Whether to include embedding vectors (can be large).
    """
    memory = get_memory_client()
    if memory is None:
        return MemoryGraph(nodes=[], relationships=[])

    try:
        graph = await memory.get_graph(
            memory_types=["short_term", "long_term", "reasoning"],
            session_id=session_id,
            include_embeddings=include_embeddings,
            limit=500,
        )
    except Exception as e:
        logger.warning("Error fetching memory graph: %s", e)
        return MemoryGraph(nodes=[], relationships=[])

    nodes = [
        GraphNode(id=node.id, labels=node.labels, properties=node.properties)
        for node in graph.nodes
    ]
    relationships = [
        GraphRelationship(
            id=rel.id,
            from_node=rel.from_node,
            to_node=rel.to_node,
            type=rel.type,
            properties=rel.properties,
        )
        for rel in graph.relationships
    ]
    return MemoryGraph(nodes=nodes, relationships=relationships)


@router.get("/memory/traces")
async def list_traces(
    session_id: str | None = None,
    success_only: bool | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    """List reasoning traces.

    ``step_count`` is non-zero because the chat route records one
    ``ReasoningStep`` per tool call as the stream events arrive.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        traces = await memory.reasoning.list_traces(
            session_id=session_id,
            success_only=success_only,
            limit=limit,
        )
        counts = {
            row["id"]: row
            for row in await memory.query.cypher(
                TRACE_STEP_COUNTS, {"ids": [str(trace.id) for trace in traces]}
            )
        }
    except Exception as e:
        logger.warning("Error listing traces: %s", e)
        return []

    return [
        {
            "id": str(trace.id),
            "session_id": trace.session_id,
            "task": trace.task,
            "success": trace.success,
            "outcome": trace.outcome,
            "started_at": trace.started_at.isoformat() if trace.started_at else None,
            "completed_at": trace.completed_at.isoformat() if trace.completed_at else None,
            "step_count": counts.get(str(trace.id), {}).get("step_count", 0),
            "tool_call_count": counts.get(str(trace.id), {}).get("tool_call_count", 0),
        }
        for trace in traces
    ]


@router.get("/memory/tool-stats")
async def get_tool_stats() -> list[dict[str, object]]:
    """Get tool usage statistics (pre-aggregated on the Tool nodes)."""
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        # get_tool_stats() is on the bolt ReasoningMemory but not yet on
        # ReasoningProtocol — see the follow-up in the examples review.
        stats = await memory.reasoning.get_tool_stats()  # type: ignore[attr-defined]
    except Exception as e:
        logger.warning("Error getting tool stats: %s", e)
        return []

    return [
        {
            "name": stat.name,
            "description": stat.description,
            "total_calls": stat.total_calls,
            "successful_calls": stat.successful_calls,
            "failed_calls": stat.failed_calls,
            "success_rate": stat.success_rate,
            "avg_duration_ms": stat.avg_duration_ms,
            "last_used_at": stat.last_used_at.isoformat() if stat.last_used_at else None,
        }
        for stat in stats
    ]


@router.delete("/memory/messages/{message_id}")
async def delete_message(message_id: str, cascade: bool = True) -> dict[str, object]:
    """Delete a specific message from short-term memory.

    Args:
        message_id: The ID of the message to delete.
        cascade: Whether to also delete related MENTIONS relationships.
    """
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    try:
        # `cascade` is on the bolt ShortTermMemory but not on ShortTermProtocol.
        deleted = await memory.short_term.delete_message(  # type: ignore[call-arg]
            message_id, cascade=cascade
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "deleted", "message_id": message_id}


def serialize_neo4j_value(value: Any) -> Any:
    """Serialize Neo4j values to JSON-compatible format."""
    if value is None:
        return None

    # Handle Neo4j Integer
    if hasattr(value, "__class__") and value.__class__.__name__ == "Integer":
        return int(value)

    # Handle Neo4j DateTime
    if hasattr(value, "iso_format"):
        return value.iso_format()

    # Handle datetime objects
    if hasattr(value, "isoformat"):
        return value.isoformat()

    if isinstance(value, list):
        return [serialize_neo4j_value(v) for v in value]

    if isinstance(value, dict):
        return {k: serialize_neo4j_value(v) for k, v in value.items()}

    return value


@router.get("/memory/graph/neighbors/{node_id}", response_model=MemoryGraph)
async def get_node_neighbors(
    node_id: str,
    depth: int = Query(default=1, ge=1, le=2),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryGraph:
    """Get neighbors of a specific node for graph expansion.

    Args:
        node_id: The ID of the node to expand.
        depth: How many hops away to retrieve (1 or 2). Default is 1.
        limit: Maximum number of neighbors to return. Default is 50.

    Returns:
        MemoryGraph containing the source node, its neighbors, and
        the relationships connecting them.
    """
    memory = get_memory_client()
    if memory is None:
        return MemoryGraph(nodes=[], relationships=[])

    nodes: list[GraphNode] = []
    relationships: list[GraphRelationship] = []
    seen_node_ids: set[str] = set()
    seen_rel_ids: set[str] = set()

    try:
        source_results = await memory.query.cypher(NODE_BY_ID, {"node_id": node_id})

        for row in source_results:
            if row["id"] and row["id"] not in seen_node_ids:
                seen_node_ids.add(row["id"])
                props = row["props"] or {}
                nodes.append(
                    GraphNode(
                        id=row["id"],
                        labels=row["labels"] or [],
                        properties={
                            k: serialize_neo4j_value(v)
                            for k, v in props.items()
                            if k != "embedding"
                        },
                    )
                )

        neighbor_query = NEIGHBORS_DEPTH_1 if depth == 1 else NEIGHBORS_DEPTH_2
        neighbor_results = await memory.query.cypher(
            neighbor_query, {"node_id": node_id, "limit": limit}
        )

        for row in neighbor_results:
            neighbor_id = row["neighbor_id"]
            if neighbor_id and neighbor_id not in seen_node_ids:
                seen_node_ids.add(neighbor_id)
                props = row["neighbor_props"] or {}
                nodes.append(
                    GraphNode(
                        id=neighbor_id,
                        labels=row["neighbor_labels"] or [],
                        properties={
                            k: serialize_neo4j_value(v)
                            for k, v in props.items()
                            if k != "embedding"
                        },
                    )
                )

            rel_id = row["rel_id"]
            if rel_id and rel_id not in seen_rel_ids:
                seen_rel_ids.add(rel_id)
                rel_props = row["rel_props"] or {}
                relationships.append(
                    GraphRelationship(
                        id=rel_id,
                        from_node=row["start_id"],
                        to_node=row["end_id"],
                        type=row["rel_type"],
                        properties={k: serialize_neo4j_value(v) for k, v in rel_props.items()},
                    )
                )

    except Exception as e:
        logger.warning("Error fetching node neighbors: %s", e)
        return MemoryGraph(nodes=[], relationships=[])

    return MemoryGraph(nodes=nodes, relationships=relationships)
