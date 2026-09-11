"""Memory management API endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.memory.long_term import Entity as LongTermEntity
from neo4j_agent_memory.memory.long_term import Preference as LongTermPreference
from src.agent.tools import PREFERENCE_CATEGORIES
from src.api.schemas import (
    ConversationRef,
    Entity,
    GraphNode,
    GraphRelationship,
    LocationEntity,
    MemoryContext,
    MemoryGraph,
    Preference,
    PreferenceRequest,
    ReasoningStepResponse,
    ReasoningTraceResponse,
    RecentMessage,
    ToolCallResponse,
    ToolStatsResponse,
)
from src.memory.client import get_memory_client

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================================================
# Reasoning Memory Endpoints
# ============================================================================


@router.get("/memory/traces", response_model=list[ReasoningTraceResponse])
async def list_traces(
    thread_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    success_only: bool | None = None,
) -> list[ReasoningTraceResponse]:
    """List reasoning traces, optionally filtered by thread/session.

    Uses the new list_traces() API from neo4j-agent-memory for efficient
    trace listing with filtering and pagination.

    Args:
        thread_id: Optional thread ID to filter traces by session.
        limit: Maximum number of traces to return.
        offset: Number of traces to skip (for pagination).
        success_only: Filter by success status (True/False/None for all).
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        # Use the new list_traces() API for efficient listing
        traces = await memory.reasoning.list_traces(
            session_id=thread_id,
            success_only=success_only,
            limit=limit,
            offset=offset,
            order_by="started_at",
            order_dir="desc",
        )

        return [
            ReasoningTraceResponse(
                id=str(trace.id),
                session_id=trace.session_id,
                task=trace.task,
                outcome=trace.outcome,
                success=trace.success,
                started_at=trace.started_at.isoformat() if trace.started_at else None,
                completed_at=trace.completed_at.isoformat() if trace.completed_at else None,
                step_count=len(trace.steps) if trace.steps else 0,
            )
            for trace in traces
        ]
    except Exception:
        logger.exception("Memory route failed")
        return []


@router.get("/memory/traces/{trace_id}", response_model=ReasoningTraceResponse)
async def get_trace(trace_id: str) -> ReasoningTraceResponse:
    """Get a specific reasoning trace with all steps and tool calls.

    Args:
        trace_id: The trace ID to retrieve.
    """
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    try:
        trace = await memory.reasoning.get_trace(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="Trace not found")

        # Convert steps to response format
        steps = []
        for step in trace.steps or []:
            tool_calls = [
                ToolCallResponse(
                    id=str(tc.id),
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                    result=tc.result,
                    status=tc.status.value,
                    duration_ms=tc.duration_ms,
                    error=tc.error,
                )
                for tc in (step.tool_calls or [])
            ]
            steps.append(
                ReasoningStepResponse(
                    id=str(step.id),
                    step_number=step.step_number,
                    thought=step.thought,
                    action=step.action,
                    observation=step.observation,
                    tool_calls=tool_calls,
                )
            )

        return ReasoningTraceResponse(
            id=str(trace.id),
            session_id=trace.session_id,
            task=trace.task,
            outcome=trace.outcome,
            success=trace.success,
            started_at=trace.started_at.isoformat() if trace.started_at else None,
            completed_at=trace.completed_at.isoformat() if trace.completed_at else None,
            step_count=len(steps),
            steps=steps,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/tool-stats", response_model=list[ToolStatsResponse])
async def get_tool_stats() -> list[ToolStatsResponse]:
    """Get usage statistics for all tools.

    Uses the new optimized get_tool_stats() API from neo4j-agent-memory
    which uses pre-aggregated statistics on Tool nodes for fast retrieval.

    Returns aggregated statistics for each tool including success rate,
    average duration, and total call count.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        # Use the new optimized get_tool_stats() API
        stats = await memory.reasoning.get_tool_stats()
        return [
            ToolStatsResponse(
                name=tool.name,
                description=tool.description,
                success_rate=tool.success_rate,
                avg_duration_ms=tool.avg_duration_ms,
                total_calls=tool.total_calls,
            )
            for tool in stats
        ]
    except Exception:
        return []


@router.get("/memory/similar-traces")
async def get_similar_traces(
    task: str,
    limit: int = 5,
    success_only: bool = True,
) -> list[ReasoningTraceResponse]:
    """Find similar past reasoning traces for a given task.

    Args:
        task: The task description to find similar traces for.
        limit: Maximum number of traces to return.
        success_only: Only return successful traces.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        traces = await memory.reasoning.get_similar_traces(
            task=task,
            limit=limit,
            success_only=success_only,
        )
        return [
            ReasoningTraceResponse(
                id=str(trace.id),
                session_id=trace.session_id,
                task=trace.task,
                outcome=trace.outcome,
                success=trace.success,
                started_at=trace.started_at.isoformat() if trace.started_at else None,
                completed_at=trace.completed_at.isoformat() if trace.completed_at else None,
                similarity=trace.metadata.get("similarity") if trace.metadata else None,
            )
            for trace in traces
        ]
    except Exception:
        return []


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
    preferences = []
    entities = []
    recent_topics = []
    recent_messages = []

    memory = get_memory_client()
    if memory is None:
        return MemoryContext(
            preferences=preferences,
            entities=entities,
            recent_topics=recent_topics,
            recent_messages=recent_messages,
        )

    try:
        # Get recent messages from short-term memory
        if thread_id:
            # No ``limit=``: the Cypher LIMIT applies to the OLDEST messages.
            conversation = await memory.short_term.get_conversation(session_id=thread_id)
            if conversation and conversation.messages:
                for msg in conversation.messages[-10:]:
                    recent_messages.append(
                        RecentMessage(
                            id=str(msg.id),
                            role=msg.role.value,
                            content=(msg.content[:200] + ("..." if len(msg.content) > 200 else "")),
                            created_at=msg.created_at.isoformat() if msg.created_at else None,
                        )
                    )

        # Get preferences
        if query:
            pref_results = await memory.long_term.search_preferences(query, limit=10)
        else:
            # No query: enumerate the categories this app writes rather than
            # running a vector search against the empty string.
            pref_results = []
            for pref_category in PREFERENCE_CATEGORIES:
                pref_results.extend(
                    await memory.long_term.get_preferences_by_category(pref_category, limit=10)
                )
            pref_results = sorted(pref_results, key=lambda p: p.created_at, reverse=True)[:10]

        for pref in pref_results:
            preferences.append(
                Preference(
                    id=str(pref.id),
                    category=pref.category,
                    preference=pref.preference,
                    context=pref.context,
                    confidence=pref.confidence,
                    created_at=getattr(pref, "created_at", None),
                )
            )

        # Get entities - filter by current thread if thread_id provided
        if thread_id:
            # Get entities mentioned in the current conversation, prioritizing enriched ones
            entity_query = """
            MATCH (c:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)-[:MENTIONS]->(e:Entity)
            WITH e, count(m) AS mention_count
            // Filter out low-quality entities (single chars, common words)
            WHERE size(e.name) > 2
            // Prioritize enriched entities and those with more mentions
            RETURN e,
                   mention_count,
                   CASE WHEN e.enriched_description IS NOT NULL THEN 1 ELSE 0 END AS is_enriched
            ORDER BY is_enriched DESC, mention_count DESC
            LIMIT 15
            """
            try:
                entity_results = await memory.query.cypher(entity_query, {"session_id": thread_id})
                for row in entity_results:
                    ent = dict(row["e"])
                    entities.append(
                        Entity(
                            id=ent.get("id", ""),
                            name=ent.get("name", ""),
                            type=ent.get("type", "Entity"),
                            subtype=ent.get("subtype"),
                            description=ent.get("description"),
                            enriched_description=ent.get("enriched_description"),
                            wikipedia_url=ent.get("wikipedia_url"),
                            image_url=ent.get("image_url"),
                        )
                    )
            except Exception:
                pass
        elif query:
            # Search entities by query
            entity_results = await memory.long_term.search_entities(query, limit=10)
            for ent in entity_results:
                attrs = getattr(ent, "attributes", {}) or {}
                entities.append(
                    Entity(
                        id=str(ent.id),
                        name=ent.name,
                        type=ent.type if isinstance(ent.type, str) else ent.type.value,
                        subtype=getattr(ent, "subtype", None),
                        description=ent.description,
                        enriched_description=attrs.get("enriched_description")
                        or getattr(ent, "enriched_description", None),
                        wikipedia_url=attrs.get("wikipedia_url")
                        or getattr(ent, "wikipedia_url", None),
                        image_url=attrs.get("image_url") or getattr(ent, "image_url", None),
                    )
                )

    except Exception:
        pass

    return MemoryContext(
        preferences=preferences,
        entities=entities,
        recent_topics=recent_topics,
        recent_messages=recent_messages,
    )


@router.get("/preferences", response_model=list[Preference])
async def list_preferences(
    category: str | None = None,
) -> list[Preference]:
    """List user preferences, optionally filtered by category."""
    preferences = []

    memory = get_memory_client()
    if memory is None:
        return preferences

    try:
        categories = [category] if category else list(PREFERENCE_CATEGORIES)
        stored: list[LongTermPreference] = []
        for name in categories:
            stored.extend(await memory.long_term.get_preferences_by_category(name, limit=50))

        for pref in sorted(stored, key=lambda p: p.created_at, reverse=True)[:50]:
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

    except Exception:
        logger.exception("Failed to list preferences")

    return preferences


@router.post("/preferences", response_model=Preference)
async def add_preference(
    request: PreferenceRequest,
) -> Preference:
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

        return Preference(
            id=str(pref.id),
            category=pref.category,
            preference=pref.preference,
            context=pref.context,
            confidence=pref.confidence,
            created_at=getattr(pref, "created_at", None),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/preferences/{preference_id}")
async def delete_preference(
    preference_id: str,
) -> dict:
    """Delete a preference by ID.

    Deletes are bolt-only: the library has no ``delete_preference`` writer, so
    this goes through ``client.graph.execute_write``. Prefer
    ``long_term.supersede_preference(old_id, new_id)`` when the preference was
    replaced rather than retracted -- that keeps the history queryable.
    """
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    rows = await memory.query.cypher(
        "MATCH (p:Preference {id: $id}) RETURN p.id AS id",
        {"id": preference_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Preference not found")

    await memory.graph.execute_write(
        "MATCH (p:Preference {id: $id}) DETACH DELETE p",
        {"id": preference_id},
    )
    return {"status": "deleted", "preference_id": preference_id}


@router.get("/entities", response_model=list[Entity])
async def list_entities(
    type: str | None = None,
    query: str | None = None,
) -> list[Entity]:
    """List extracted entities, optionally filtered by type or search query."""
    entities = []

    memory = get_memory_client()
    if memory is None:
        return entities

    try:
        search_query = query or type or ""
        results = await memory.long_term.search_entities(search_query, limit=50)

        for ent in results:
            ent_type = ent.type if isinstance(ent.type, str) else ent.type.value
            if type is None or ent_type == type:
                attrs = getattr(ent, "attributes", {}) or {}
                entities.append(
                    Entity(
                        id=str(ent.id),
                        name=ent.name,
                        type=ent_type,
                        subtype=getattr(ent, "subtype", None),
                        description=ent.description,
                        enriched_description=attrs.get("enriched_description")
                        or getattr(ent, "enriched_description", None),
                        wikipedia_url=attrs.get("wikipedia_url")
                        or getattr(ent, "wikipedia_url", None),
                        image_url=attrs.get("image_url") or getattr(ent, "image_url", None),
                    )
                )

    except Exception:
        pass

    return entities


@router.get("/entities/top")
async def get_top_entities(
    entity_type: str | None = Query(default=None, description="Filter by entity type"),
    limit: int = Query(default=10, ge=1, le=100),
) -> list[dict]:
    """Get the most mentioned entities across all podcasts.

    Args:
        entity_type: Optional filter by PERSON, ORGANIZATION, CONCEPT, LOCATION.
        limit: Number of results to return (default 10).

    Returns:
        List of entities with mention counts.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        query = """
        MATCH (e:Entity)<-[r:MENTIONS]-()
        WHERE $type IS NULL OR e.type = $type
        WITH e, count(r) AS mentions
        ORDER BY mentions DESC
        LIMIT $limit
        RETURN e.id AS id, e.name AS name, e.type AS type,
               e.subtype AS subtype,
               e.enriched_description AS enriched_description,
               e.wikipedia_url AS wikipedia_url,
               e.image_url AS image_url,
               mentions
        """
        results = await memory.query.cypher(query, {"type": entity_type, "limit": limit})

        return [
            {
                "id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "subtype": r["subtype"],
                "enriched_description": r["enriched_description"],
                "wikipedia_url": r["wikipedia_url"],
                "image_url": r["image_url"],
                "mentions": r["mentions"],
            }
            for r in results
        ]
    except Exception:
        logger.exception("Memory route failed")
        return []


@router.get("/entities/{entity_name}/context")
async def get_entity_full_context(
    entity_name: str,
) -> dict:
    """Get full context for an entity including enrichment and mentions.

    Args:
        entity_name: Name of the entity to retrieve.

    Returns:
        Entity details with Wikipedia data and podcast mentions.
    """
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    try:
        # Get entity by name
        entity = await memory.long_term.get_entity_by_name(entity_name)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        # Get mention context
        query = """
        MATCH (e:Entity {name: $name})<-[:MENTIONS]-(m:Message)<-[:HAS_MESSAGE]-(c:Conversation)
        RETURN m.content AS content, m.metadata AS metadata, c.session_id AS session_id
        LIMIT 5
        """
        mentions = await memory.query.cypher(query, {"name": entity_name})

        import json as json_lib

        mention_list = []
        for m in mentions:
            metadata = m.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json_lib.loads(metadata)
                except json_lib.JSONDecodeError:
                    metadata = {}
            mention_list.append(
                {
                    "content": m["content"][:300] + "..."
                    if len(m["content"]) > 300
                    else m["content"],
                    "speaker": metadata.get("speaker", "Unknown"),
                    "session_id": m["session_id"],
                }
            )

        attrs = getattr(entity, "attributes", {}) or {}
        return {
            "entity": {
                "id": str(entity.id),
                "name": entity.name,
                "type": entity.type if isinstance(entity.type, str) else entity.type.value,
                "subtype": getattr(entity, "subtype", None),
                "description": entity.description,
                "enriched_description": attrs.get("enriched_description")
                or getattr(entity, "enriched_description", None),
                "wikipedia_url": attrs.get("wikipedia_url")
                or getattr(entity, "wikipedia_url", None),
                "image_url": attrs.get("image_url") or getattr(entity, "image_url", None),
            },
            "mentions": mention_list,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/entities/related/{entity_name}")
async def get_related_entities(
    entity_name: str,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[dict]:
    """Get entities that co-occur with the given entity.

    Args:
        entity_name: Name of the entity to find relations for.
        limit: Maximum number of related entities to return.

    Returns:
        List of related entities with co-occurrence counts.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        query = """
        MATCH (e1:Entity {name: $name})<-[:MENTIONS]-(m:Message)-[:MENTIONS]->(e2:Entity)
        WHERE e1 <> e2
        WITH e2, count(m) AS co_occurrences
        ORDER BY co_occurrences DESC
        LIMIT $limit
        RETURN e2.id AS id, e2.name AS name, e2.type AS type, e2.subtype AS subtype,
               e2.enriched_description AS enriched_description, co_occurrences
        """
        results = await memory.query.cypher(query, {"name": entity_name, "limit": limit})

        return [
            {
                "id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "subtype": r["subtype"],
                "enriched_description": r["enriched_description"],
                "co_occurrences": r["co_occurrences"],
            }
            for r in results
        ]
    except Exception:
        logger.exception("Memory route failed")
        return []


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

    # Handle lists
    if isinstance(value, list):
        return [serialize_neo4j_value(v) for v in value]

    # Handle dicts
    if isinstance(value, dict):
        return {k: serialize_neo4j_value(v) for k, v in value.items()}

    return value


@router.get("/memory/graph", response_model=MemoryGraph)
async def get_memory_graph(
    memory_types: str | None = None,
    session_id: str | None = None,
    episode_session_ids: str | None = None,
    limit: int = 1000,
) -> MemoryGraph:
    """Get the memory graph for visualization.

    When a session_id is provided, returns a conversation-centric view showing:
    - All messages in the conversation
    - NEXT_MESSAGE chain connecting them
    - Entities mentioned in each message via MENTIONS relationships

    When episode_session_ids is also provided, additionally includes:
    - Full conversations from those podcast episodes
    - All entities mentioned in those episodes

    Without session_id, uses the standard get_graph() API for a broader view.

    Args:
        memory_types: Comma-separated list of memory types to include
                     (short_term, long_term, reasoning). Defaults to all.
        session_id: Optional session ID to filter by. When provided, shows
                   conversation message chain with connected entities.
        episode_session_ids: Comma-separated list of podcast episode session IDs
                            to include in the graph (e.g., from tool call results).
        limit: Maximum number of nodes to return.

    Returns all nodes and relationships from the memory graph database.
    """
    memory = get_memory_client()
    if memory is None:
        return MemoryGraph(nodes=[], relationships=[])

    try:
        # Parse episode session IDs if provided
        episode_ids_list: list[str] = []
        if episode_session_ids:
            episode_ids_list = [s.strip() for s in episode_session_ids.split(",") if s.strip()]

        # If session_id provided, use custom query for conversation view
        if session_id:
            return await _get_conversation_graph(memory, session_id, limit, episode_ids_list)

        # Otherwise use the standard get_graph() API
        types_list = None
        if memory_types:
            types_list = [t.strip() for t in memory_types.split(",")]

        graph = await memory.get_graph(
            memory_types=types_list,
            session_id=session_id,
            include_embeddings=False,
            limit=limit,
        )

        nodes = [
            GraphNode(
                id=node.id,
                labels=node.labels,
                properties={k: serialize_neo4j_value(v) for k, v in node.properties.items()},
            )
            for node in graph.nodes
        ]

        relationships = [
            GraphRelationship(
                id=rel.id,
                from_node=rel.from_node,
                to_node=rel.to_node,
                type=rel.type,
                properties={k: serialize_neo4j_value(v) for k, v in rel.properties.items()},
            )
            for rel in graph.relationships
        ]

        return MemoryGraph(nodes=nodes, relationships=relationships)

    except Exception:
        logger.exception("Error fetching memory graph")
        return MemoryGraph(nodes=[], relationships=[])


async def _get_conversation_graph(
    memory, session_id: str, limit: int = 1000, episode_session_ids: list[str] | None = None
) -> MemoryGraph:
    """Get conversation-centric graph with messages, NEXT_MESSAGE chain, and entities.

    This provides a visualization showing:
    1. The Conversation node
    2. All Message nodes connected via HAS_MESSAGE
    3. NEXT_MESSAGE relationships forming the message chain
    4. Entity nodes connected via MENTIONS relationships

    When episode_session_ids is provided, also includes:
    5. Full conversations from those podcast episodes
    6. All entities mentioned in those episodes
    """
    nodes: list[GraphNode] = []
    relationships: list[GraphRelationship] = []
    node_ids_seen: set[str] = set()

    # Combine all session IDs to query
    all_session_ids = [session_id]
    if episode_session_ids:
        all_session_ids.extend(episode_session_ids)

    # Query 1: Get conversations, messages, and NEXT_MESSAGE chain for all sessions
    messages_query = """
    MATCH (c:Conversation)
    WHERE c.session_id IN $session_ids
    OPTIONAL MATCH (c)-[hm:HAS_MESSAGE]->(m:Message)
    OPTIONAL MATCH (m)-[nm:NEXT_MESSAGE]->(m2:Message)
    RETURN c, hm, m, nm, m2
    ORDER BY c.session_id, m.timestamp
    LIMIT $limit
    """

    results = await memory.query.cypher(
        messages_query, {"session_ids": all_session_ids, "limit": limit}
    )

    for row in results:
        # Add Conversation node
        if row.get("c"):
            conv = dict(row["c"])
            conv_id = conv.get("id") or conv.get("session_id")
            if conv_id and conv_id not in node_ids_seen:
                props = {k: serialize_neo4j_value(v) for k, v in conv.items() if v is not None}
                props.pop("embedding", None)
                nodes.append(
                    GraphNode(
                        id=str(conv_id),
                        labels=["Conversation"],
                        properties=props,
                    )
                )
                node_ids_seen.add(str(conv_id))

        # Add Message node
        if row.get("m"):
            msg = dict(row["m"])
            msg_id = msg.get("id")
            if msg_id and msg_id not in node_ids_seen:
                props = {k: serialize_neo4j_value(v) for k, v in msg.items() if v is not None}
                props.pop("embedding", None)
                # Truncate content for display
                if props.get("content") and len(props["content"]) > 200:
                    props["content"] = props["content"][:200] + "..."
                nodes.append(
                    GraphNode(
                        id=str(msg_id),
                        labels=["Message"],
                        properties=props,
                    )
                )
                node_ids_seen.add(str(msg_id))

            # Add HAS_MESSAGE relationship
            conv = row.get("c")
            if conv and msg_id:
                conv_id = conv.get("id") or conv.get("session_id")
                if conv_id:
                    rel_id = f"hm-{conv_id}-{msg_id}"
                    relationships.append(
                        GraphRelationship(
                            id=rel_id,
                            from_node=str(conv_id),
                            to_node=str(msg_id),
                            type="HAS_MESSAGE",
                            properties={},
                        )
                    )

        # Add second Message node (for NEXT_MESSAGE)
        if row.get("m2"):
            msg2 = dict(row["m2"])
            msg2_id = msg2.get("id")
            if msg2_id and msg2_id not in node_ids_seen:
                props = {k: serialize_neo4j_value(v) for k, v in msg2.items() if v is not None}
                props.pop("embedding", None)
                if props.get("content") and len(props["content"]) > 200:
                    props["content"] = props["content"][:200] + "..."
                nodes.append(
                    GraphNode(
                        id=str(msg2_id),
                        labels=["Message"],
                        properties=props,
                    )
                )
                node_ids_seen.add(str(msg2_id))

        # Add NEXT_MESSAGE relationship
        if row.get("nm") and row.get("m") and row.get("m2"):
            msg = row["m"]
            msg2 = row["m2"]
            msg_id = msg.get("id")
            msg2_id = msg2.get("id")
            if msg_id and msg2_id:
                rel_id = f"nm-{msg_id}-{msg2_id}"
                relationships.append(
                    GraphRelationship(
                        id=rel_id,
                        from_node=str(msg_id),
                        to_node=str(msg2_id),
                        type="NEXT_MESSAGE",
                        properties={},
                    )
                )

    # Query 2: Get entities connected to messages via MENTIONS for all sessions
    entities_query = """
    MATCH (c:Conversation)
    WHERE c.session_id IN $session_ids
    MATCH (c)-[:HAS_MESSAGE]->(m:Message)
    MATCH (m)-[mentions:MENTIONS]->(e:Entity)
    RETURN m.id AS message_id, mentions, e
    LIMIT $limit
    """

    entity_results = await memory.query.cypher(
        entities_query, {"session_ids": all_session_ids, "limit": limit}
    )

    for row in entity_results:
        # Add Entity node
        if row.get("e"):
            entity = dict(row["e"])
            entity_id = entity.get("id")
            if entity_id and entity_id not in node_ids_seen:
                props = {k: serialize_neo4j_value(v) for k, v in entity.items() if v is not None}
                props.pop("embedding", None)
                # Get entity type labels
                entity_type = entity.get("type", "Entity")
                labels = ["Entity"]
                if entity_type and entity_type != "Entity":
                    labels.append(entity_type)
                nodes.append(
                    GraphNode(
                        id=str(entity_id),
                        labels=labels,
                        properties=props,
                    )
                )
                node_ids_seen.add(str(entity_id))

        # Add MENTIONS relationship
        msg_id = row.get("message_id")
        entity = row.get("e")
        if msg_id and entity:
            entity_id = entity.get("id")
            if entity_id and str(msg_id) in node_ids_seen:
                rel_id = f"mentions-{msg_id}-{entity_id}"
                relationships.append(
                    GraphRelationship(
                        id=rel_id,
                        from_node=str(msg_id),
                        to_node=str(entity_id),
                        type="MENTIONS",
                        properties={},
                    )
                )

    # Query 3: Get reasoning memory (traces, steps, tool calls, tools) for the sessions
    # Only query for the main session_id (not podcast episodes) since reasoning traces
    # are associated with the current conversation, not the podcast content
    reasoning_query = """
    MATCH (rt:ReasoningTrace {session_id: $session_id})
    OPTIONAL MATCH (rt)-[hs:HAS_STEP]->(rs:ReasoningStep)
    OPTIONAL MATCH (rs)-[ut:USES_TOOL]->(tc:ToolCall)
    OPTIONAL MATCH (tc)-[io:INSTANCE_OF]->(t:Tool)
    RETURN rt, hs, rs, ut, tc, io, t
    ORDER BY rs.step_number
    LIMIT $limit
    """

    reasoning_results = await memory.query.cypher(
        reasoning_query, {"session_id": session_id, "limit": limit}
    )

    for row in reasoning_results:
        # Add ReasoningTrace node
        if row.get("rt"):
            trace = dict(row["rt"])
            trace_id = trace.get("id")
            if trace_id and trace_id not in node_ids_seen:
                props = {k: serialize_neo4j_value(v) for k, v in trace.items() if v is not None}
                props.pop("task_embedding", None)
                nodes.append(
                    GraphNode(
                        id=str(trace_id),
                        labels=["ReasoningTrace"],
                        properties=props,
                    )
                )
                node_ids_seen.add(str(trace_id))

        # Add ReasoningStep node
        if row.get("rs"):
            step = dict(row["rs"])
            step_id = step.get("id")
            if step_id and step_id not in node_ids_seen:
                props = {k: serialize_neo4j_value(v) for k, v in step.items() if v is not None}
                nodes.append(
                    GraphNode(
                        id=str(step_id),
                        labels=["ReasoningStep"],
                        properties=props,
                    )
                )
                node_ids_seen.add(str(step_id))

            # Add HAS_STEP relationship
            trace = row.get("rt")
            if trace and step_id:
                trace_id = trace.get("id")
                if trace_id:
                    rel_id = f"hs-{trace_id}-{step_id}"
                    hs_rel = row.get("hs")
                    rel_props = {}
                    if hs_rel:
                        # Handle Neo4j relationship object - use _properties or items()
                        try:
                            if hasattr(hs_rel, "_properties"):
                                rel_props = {
                                    k: serialize_neo4j_value(v)
                                    for k, v in hs_rel._properties.items()
                                }
                            elif hasattr(hs_rel, "items"):
                                rel_props = {k: serialize_neo4j_value(v) for k, v in hs_rel.items()}
                        except Exception:
                            rel_props = {}
                    relationships.append(
                        GraphRelationship(
                            id=rel_id,
                            from_node=str(trace_id),
                            to_node=str(step_id),
                            type="HAS_STEP",
                            properties=rel_props,
                        )
                    )

        # Add ToolCall node
        if row.get("tc"):
            tc = dict(row["tc"])
            tc_id = tc.get("id")
            if tc_id and tc_id not in node_ids_seen:
                props = {k: serialize_neo4j_value(v) for k, v in tc.items() if v is not None}
                # Truncate result if too long
                if (
                    props.get("result")
                    and isinstance(props["result"], str)
                    and len(props["result"]) > 500
                ):
                    props["result"] = props["result"][:500] + "..."
                nodes.append(
                    GraphNode(
                        id=str(tc_id),
                        labels=["ToolCall"],
                        properties=props,
                    )
                )
                node_ids_seen.add(str(tc_id))

            # Add USES_TOOL relationship
            step = row.get("rs")
            if step and tc_id:
                step_id = step.get("id")
                if step_id:
                    rel_id = f"ut-{step_id}-{tc_id}"
                    relationships.append(
                        GraphRelationship(
                            id=rel_id,
                            from_node=str(step_id),
                            to_node=str(tc_id),
                            type="USES_TOOL",
                            properties={},
                        )
                    )

        # Add Tool node
        if row.get("t"):
            tool = dict(row["t"])
            tool_name = tool.get("name")
            if tool_name and tool_name not in node_ids_seen:
                props = {k: serialize_neo4j_value(v) for k, v in tool.items() if v is not None}
                nodes.append(
                    GraphNode(
                        id=str(tool_name),  # Use name as ID for Tool nodes
                        labels=["Tool"],
                        properties=props,
                    )
                )
                node_ids_seen.add(str(tool_name))

            # Add INSTANCE_OF relationship
            tc = row.get("tc")
            if tc and tool_name:
                tc_id = tc.get("id")
                if tc_id:
                    rel_id = f"io-{tc_id}-{tool_name}"
                    relationships.append(
                        GraphRelationship(
                            id=rel_id,
                            from_node=str(tc_id),
                            to_node=str(tool_name),
                            type="INSTANCE_OF",
                            properties={},
                        )
                    )

    return MemoryGraph(nodes=nodes, relationships=relationships)


@router.get("/locations", response_model=list[LocationEntity])
async def get_locations(
    session_id: str | None = Query(default=None, description="Filter by conversation session ID"),
    limit: int = Query(default=500, ge=1, le=2000),
    has_coordinates: bool = Query(default=True),
) -> list[LocationEntity]:
    """Get Location entities with coordinates for map display.

    Args:
        session_id: Optional session ID to filter locations to those mentioned
                   in a specific conversation.
        limit: Maximum number of locations to return (default 500, max 2000).
        has_coordinates: If True, only return locations with coordinates.

    Returns:
        List of location entities with coordinates and related conversations.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        # Use the new get_locations() API from MemoryClient
        locations_data = await memory.get_locations(
            session_id=session_id,
            has_coordinates=has_coordinates,
            limit=limit,
        )

        locations = []
        for loc in locations_data:
            # Skip if no coordinates
            if loc.get("latitude") is None or loc.get("longitude") is None:
                continue

            # Parse conversations
            convs = []
            for c in loc.get("conversations") or []:
                if c and isinstance(c, dict) and c.get("id"):
                    convs.append(
                        ConversationRef(
                            id=c.get("session_id") or c.get("id", ""),
                            title=c.get("title"),
                        )
                    )

            locations.append(
                LocationEntity(
                    id=loc["id"],
                    name=loc["name"],
                    subtype=loc.get("subtype"),
                    description=loc.get("description"),
                    enriched_description=loc.get("enriched_description"),
                    wikipedia_url=loc.get("wikipedia_url"),
                    latitude=float(loc["latitude"]),
                    longitude=float(loc["longitude"]),
                    conversations=convs,
                )
            )

        return locations

    except Exception:
        logger.exception("Error fetching locations")
        return []


async def resolve_coordinates(
    memory: MemoryClient,
    entity: LongTermEntity,
) -> tuple[float, float] | None:
    """Return ``(latitude, longitude)`` for a LOCATION entity, or None.

    Entities created with ``add_entity(coordinates=...)`` carry the pair in
    ``attributes["coordinates"]``; entities geocoded in bulk by
    ``scripts/geocode_locations.py`` only have the Neo4j Point property
    ``e.location``. ``get_location_coordinates()`` reads that property, so this
    helper covers both shapes.
    """
    coords = entity.attributes.get("coordinates")
    if isinstance(coords, dict):
        lat, lon = coords.get("latitude"), coords.get("longitude")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    point = await memory.long_term.get_location_coordinates(entity.id)
    if point is None:
        return None
    return float(point[0]), float(point[1])


@router.get("/locations/nearby", response_model=list[LocationEntity])
async def get_locations_nearby(
    lat: float = Query(..., description="Latitude of center point"),
    lon: float = Query(..., description="Longitude of center point"),
    radius_km: float = Query(default=10.0, ge=0.1, le=500, description="Search radius in km"),
    session_id: str | None = Query(default=None, description="Filter by conversation session"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[LocationEntity]:
    """Find locations within a radius of a point.

    Uses the geospatial query capabilities of the memory system to find
    Location entities near a specified point, optionally filtered by
    conversation session.

    Args:
        lat: Latitude of the center point.
        lon: Longitude of the center point.
        radius_km: Search radius in kilometers (default 10km, max 500km).
        session_id: Optional session ID to filter to conversation-specific locations.
        limit: Maximum number of results to return.

    Returns:
        List of location entities sorted by distance, with distance_km in metadata.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        entities = await memory.long_term.search_locations_near(
            latitude=lat,
            longitude=lon,
            radius_km=radius_km,
            session_id=session_id,
            limit=limit,
        )

        locations = []
        for ent in entities:
            # Coordinates live on the Neo4j Point property ``e.location``, which
            # the bulk geocoder writes; they are NOT mirrored into the entity's
            # serialized metadata blob, so ``attributes["coordinates"]`` is empty
            # for bulk-geocoded entities. Resolve them with the public accessor.
            coords = await resolve_coordinates(memory, ent)
            if coords is None:
                continue
            lat_val, lon_val = coords
            locations.append(
                LocationEntity(
                    id=str(ent.id),
                    name=ent.name,
                    subtype=getattr(ent, "subtype", None),
                    description=ent.description,
                    enriched_description=ent.attributes.get("enriched_description"),
                    wikipedia_url=ent.attributes.get("wikipedia_url"),
                    latitude=lat_val,
                    longitude=lon_val,
                    conversations=[],
                    distance_km=ent.metadata.get("distance_km") if ent.metadata else None,
                )
            )

        return locations

    except Exception:
        logger.exception("Error fetching nearby locations")
        return []


@router.get("/locations/bounds", response_model=list[LocationEntity])
async def get_locations_in_bounds(
    min_lat: float = Query(..., description="Minimum latitude (south)"),
    max_lat: float = Query(..., description="Maximum latitude (north)"),
    min_lon: float = Query(..., description="Minimum longitude (west)"),
    max_lon: float = Query(..., description="Maximum longitude (east)"),
    session_id: str | None = Query(default=None, description="Filter by conversation session"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[LocationEntity]:
    """Find locations within a bounding box.

    Useful for fetching locations visible in the current map viewport.

    Args:
        min_lat: Minimum latitude (south boundary).
        max_lat: Maximum latitude (north boundary).
        min_lon: Minimum longitude (west boundary).
        max_lon: Maximum longitude (east boundary).
        session_id: Optional session ID to filter to conversation-specific locations.
        limit: Maximum number of results to return.

    Returns:
        List of location entities within the bounding box.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        entities = await memory.long_term.search_locations_in_bounding_box(
            min_lat=min_lat,
            min_lon=min_lon,
            max_lat=max_lat,
            max_lon=max_lon,
            session_id=session_id,
            limit=limit,
        )

        locations = []
        for ent in entities:
            coords = await resolve_coordinates(memory, ent)
            if coords is None:
                continue
            lat_val, lon_val = coords
            locations.append(
                LocationEntity(
                    id=str(ent.id),
                    name=ent.name,
                    subtype=getattr(ent, "subtype", None),
                    description=ent.description,
                    enriched_description=ent.attributes.get("enriched_description"),
                    wikipedia_url=ent.attributes.get("wikipedia_url"),
                    latitude=lat_val,
                    longitude=lon_val,
                    conversations=[],
                )
            )

        return locations

    except Exception:
        logger.exception("Error fetching locations in bounds")
        return []


@router.get("/locations/clusters")
async def get_location_clusters(
    session_id: str | None = Query(default=None, description="Filter by conversation session"),
) -> list[dict]:
    """Analyze geographic clusters of locations mentioned in podcasts.

    Returns location density by country/region, useful for heatmap visualization.

    Args:
        session_id: Optional session ID to filter to conversation-specific locations.

    Returns:
        List of clusters with country, location count, and center coordinates.
    """
    memory = get_memory_client()
    if memory is None:
        return []

    try:
        # Get locations with coordinates
        locations_data = await memory.get_locations(
            session_id=session_id,
            has_coordinates=True,
            limit=500,
        )

        # Group by country
        country_clusters: dict[str, list[dict]] = {}
        for loc in locations_data:
            country = loc.get("country") or "Unknown"
            if country not in country_clusters:
                country_clusters[country] = []
            if loc.get("latitude") is not None and loc.get("longitude") is not None:
                country_clusters[country].append(
                    {
                        "name": loc["name"],
                        "latitude": loc["latitude"],
                        "longitude": loc["longitude"],
                    }
                )

        # Calculate cluster centers
        clusters = []
        for country, locs in sorted(country_clusters.items(), key=lambda x: -len(x[1])):
            if not locs:
                continue
            center_lat = sum(loc["latitude"] for loc in locs) / len(locs)
            center_lon = sum(loc["longitude"] for loc in locs) / len(locs)
            clusters.append(
                {
                    "country": country,
                    "location_count": len(locs),
                    "locations": locs[:5],  # Top 5 locations per country
                    "center_lat": center_lat,
                    "center_lon": center_lon,
                }
            )

        return clusters

    except Exception:
        logger.exception("Memory route failed")
        return []


@router.get("/locations/path")
async def get_shortest_path(
    from_location_id: str = Query(..., description="Source location ID"),
    to_location_id: str = Query(..., description="Target location ID"),
) -> dict:
    """Get the shortest path between two locations in the knowledge graph.

    Finds the shortest path through the graph relationships connecting
    two Location entities, useful for visualization overlays.

    Args:
        from_location_id: Source location entity ID.
        to_location_id: Target location entity ID.

    Returns:
        Dictionary with path nodes, relationships, and total hops.
    """
    memory = get_memory_client()
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory service unavailable")

    try:
        # Find shortest path between two entities
        if from_location_id == to_location_id:
            raise HTTPException(
                status_code=400,
                detail="from_location_id and to_location_id must differ",
            )

        query = """
        MATCH (from:Entity {id: $from_id}), (to:Entity {id: $to_id})
        // Neo4j raises Neo.DatabaseError...ExecutionFailed when shortestPath's
        // endpoints are the same node, hence the guard above.
        MATCH path = shortestPath((from)-[*..10]-(to))
        WITH path, nodes(path) AS pathNodes, relationships(path) AS pathRels
        RETURN [n IN pathNodes | {
            id: n.id,
            name: n.name,
            type: n.type,
            labels: labels(n),
            latitude: n.location.latitude,
            longitude: n.location.longitude
        }] AS nodes,
        [r IN pathRels | {
            type: type(r),
            from_id: startNode(r).id,
            to_id: endNode(r).id
        }] AS relationships,
        length(path) AS hops
        """

        results = await memory.query.cypher(
            query,
            {"from_id": from_location_id, "to_id": to_location_id},
        )

        if not results:
            return {"nodes": [], "relationships": [], "hops": 0, "found": False}

        row = results[0]
        return {
            "nodes": row["nodes"],
            "relationships": row["relationships"],
            "hops": row["hops"],
            "found": True,
        }

    except Exception as e:
        logger.exception("Error finding shortest path")
        return {"nodes": [], "relationships": [], "hops": 0, "found": False, "error": str(e)}


@router.get("/memory/graph/neighbors/{node_id}", response_model=MemoryGraph)
async def get_node_neighbors(
    node_id: str,
    depth: int = Query(default=1, ge=1, le=2),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryGraph:
    """Get neighbors of a specific node for graph expansion.

    This endpoint allows incremental graph exploration by fetching
    the neighbors of a specific node.

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

    try:
        # First get the source node with explicit scalar values
        source_query = """
        MATCH (n) WHERE n.id = $node_id
        RETURN n.id AS id, labels(n) AS labels, properties(n) AS props
        LIMIT 1
        """
        source_results = await memory.query.cypher(source_query, {"node_id": node_id})

        nodes = []
        relationships = []
        seen_node_ids = set()
        seen_rel_ids = set()

        # Add the source node
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

        # Now get neighbors and relationships with explicit scalar values
        if depth == 1:
            neighbor_query = """
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
        else:
            # depth == 2: get 2-hop neighbors
            neighbor_query = """
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

        neighbor_results = await memory.query.cypher(
            neighbor_query, {"node_id": node_id, "limit": limit}
        )

        for row in neighbor_results:
            # Add neighbor node
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

            # Add relationship
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

        return MemoryGraph(nodes=nodes, relationships=relationships)

    except Exception:
        logger.exception("Error fetching node neighbors")
        return MemoryGraph(nodes=[], relationships=[])
