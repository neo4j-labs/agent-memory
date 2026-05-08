"""MCP tool implementations for Neo4j Agent Memory.

Tools are organized into two profiles:
- Core (6 tools): Essential read/write cycle for memory operations.
- Extended (16 tools): Full surface including reasoning traces, entity
  management, graph export, and advanced queries.

Each tool follows goal-oriented design: high-level tools that orchestrate
extraction, resolution, and graph operations internally.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from fastmcp import Context

from neo4j_agent_memory.mcp._common import get_client, get_integration

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ── Read-only query validation ────────────────────────────────────────

WRITE_PATTERNS = [
    r"\bCREATE\b",
    r"\bMERGE\b",
    r"\bDELETE\b",
    r"\bDETACH\s+DELETE\b",
    r"\bSET\b",
    r"\bREMOVE\b",
    r"\bDROP\b",
    r"\bLOAD\s+CSV\b",
    r"\bFOREACH\b",
    r"\bCALL\s+\{",
    r"\bIN\s+TRANSACTIONS\b",
]


def _is_read_only_query(query: str) -> bool:
    """Check if a Cypher query is read-only."""
    query_upper = query.upper()
    return all(not re.search(pattern, query_upper) for pattern in WRITE_PATTERNS)


# ── Tool annotations ──────────────────────────────────────────────────

READ_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
}

WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
}


# ── Registration dispatcher ──────────────────────────────────────────


def register_tools(mcp: FastMCP, *, profile: str = "extended") -> None:
    """Register MCP tools on the server based on the selected profile.

    Args:
        mcp: FastMCP server instance.
        profile: Tool profile - 'core' (6 tools) or 'extended' (16 tools).
    """
    _register_core_tools(mcp)
    if profile == "extended":
        _register_extended_tools(mcp)


# ── Core Profile (6 tools) ───────────────────────────────────────────


def _register_core_tools(mcp: FastMCP) -> None:
    """Register the 6 core profile tools."""

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def memory_search(
        ctx: Context,
        query: str,
        limit: int = 10,
        memory_types: list[str] | None = None,
        session_id: str | None = None,
        threshold: float = 0.7,
    ) -> str:
        """Search across all memory types using hybrid vector + graph search.

        Finds relevant messages, entities, preferences, and reasoning traces.
        Use this to recall stored information or find related context.

        Args:
            query: Natural language search query.
            limit: Maximum results per memory type (default: 10).
            memory_types: Types to search. Options: 'messages', 'entities',
                'preferences', 'traces'. Defaults to messages, entities, preferences.
            session_id: Filter message search to a specific session.
            threshold: Similarity threshold 0.0-1.0 (default: 0.7).
        """
        integration = get_integration(ctx)
        result = await integration.search(
            query,
            memory_types=memory_types,
            session_id=session_id,
            limit=limit,
            threshold=threshold,
        )
        return json.dumps(result, default=str)

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def memory_get_context(
        ctx: Context,
        session_id: str | None = None,
        query: str | None = None,
        max_items: int = 10,
        include_short_term: bool = True,
        include_long_term: bool = True,
        include_reasoning: bool = True,
    ) -> str:
        """Get assembled context from all memory types for the current session.

        Returns conversation history, relevant entities/preferences, and similar
        past reasoning traces, formatted for LLM consumption. Call this at the
        start of each conversation to load relevant memories.

        Args:
            session_id: Session to get context for (uses current session if not set).
            query: Optional search query to focus context retrieval.
            max_items: Maximum items per memory type (default: 10).
            include_short_term: Include conversation history (default: true).
            include_long_term: Include entities and preferences (default: true).
            include_reasoning: Include similar reasoning traces (default: true).
        """
        integration = get_integration(ctx)
        result = await integration.get_context(
            session_id=session_id,
            query=query,
            max_items=max_items,
            include_short_term=include_short_term,
            include_long_term=include_long_term,
            include_reasoning=include_reasoning,
        )
        return json.dumps(result, default=str)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    async def memory_store_message(
        ctx: Context,
        content: str,
        role: str = "user",
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a message in conversation memory.

        Automatically triggers entity extraction, preference detection,
        and fact extraction from the message content. Use this to persist
        important user messages, assistant responses, or system events.

        Args:
            content: The message text content.
            role: Message role - 'user', 'assistant', or 'system' (default: 'user').
            session_id: Session ID (uses current session if not set).
            metadata: Optional metadata to attach (e.g., model, source).
        """
        integration = get_integration(ctx)
        result = await integration.store_message(
            role=role,
            content=content,
            session_id=session_id,
            metadata=metadata,
        )
        return json.dumps(result, default=str)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    async def memory_add_entity(
        ctx: Context,
        name: str,
        entity_type: str,
        subtype: str | None = None,
        description: str | None = None,
        aliases: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create or update an entity in the knowledge graph.

        Runs entity resolution against existing entities to avoid duplicates.
        If a close match is found, the entity may be merged or flagged.

        Entity types follow POLE+O: PERSON, OBJECT, LOCATION, EVENT, ORGANIZATION.
        Each supports optional subtypes (e.g., OBJECT:VEHICLE, LOCATION:ADDRESS).

        Args:
            name: Entity name (e.g., 'John Smith', 'Acme Corp').
            entity_type: POLE+O type in UPPER_CASE.
            subtype: Optional subtype (e.g., 'VEHICLE', 'COMPANY').
            description: Entity description.
            aliases: Alternative names for the entity.
            metadata: Additional metadata.
        """
        integration = get_integration(ctx)
        result = await integration.add_entity(
            name=name,
            entity_type=entity_type,
            subtype=subtype,
            description=description,
            aliases=aliases,
            metadata=metadata,
        )
        return json.dumps(result, default=str)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    async def memory_add_preference(
        ctx: Context,
        category: str,
        preference: str,
        context: str | None = None,
        confidence: float = 1.0,
    ) -> str:
        """Record a user preference for personalization.

        Store explicit or inferred user preferences with categorization.
        These are used to personalize future interactions.

        Args:
            category: Preference category (e.g., 'food', 'music', 'communication_style').
            preference: The preference text (e.g., 'Prefers dark mode').
            context: Optional context about when/why the preference was expressed.
            confidence: Confidence score 0.0-1.0 (default: 1.0).
        """
        integration = get_integration(ctx)
        result = await integration.add_preference(
            category=category,
            preference=preference,
            context=context,
            confidence=confidence,
        )
        return json.dumps(result, default=str)

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    async def memory_add_fact(
        ctx: Context,
        subject: str,
        predicate: str,
        object_value: str,
        confidence: float = 1.0,
        valid_from: str | None = None,
        valid_until: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a subject-predicate-object fact triple.

        Records declarative knowledge as a structured triple with optional
        temporal validity bounds and arbitrary metadata.

        Args:
            subject: The subject of the fact (e.g., 'Earth').
            predicate: The relationship (e.g., 'has_radius_km').
            object_value: The object/value (e.g., '6371').
            confidence: Confidence score 0.0-1.0 (default: 1.0).
            valid_from: ISO date string for when this fact becomes valid.
            valid_until: ISO date string for when this fact expires.
            metadata: Additional metadata (e.g., scope, stack_tags, temporality).
        """
        integration = get_integration(ctx)
        result = await integration.add_fact(
            subject=subject,
            predicate=predicate,
            object_value=object_value,
            confidence=confidence,
            valid_from=valid_from,
            valid_until=valid_until,
            metadata=metadata,
        )
        return json.dumps(result, default=str)


# ── Extended Profile (9 additional tools, 15 total) ──────────────────


def _register_extended_tools(mcp: FastMCP) -> None:
    """Register the 9 extended profile tools."""

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def memory_get_conversation(
        ctx: Context,
        session_id: str,
        limit: int = 50,
        include_metadata: bool = True,
    ) -> str:
        """Retrieve full conversation history for a session.

        Returns messages in chronological order with role, content, and timestamps.

        Args:
            session_id: The session ID to retrieve.
            limit: Maximum number of messages to return (default: 50).
            include_metadata: Include message metadata (default: true).
        """
        client = get_client(ctx)

        try:
            conversation = await client.short_term.get_conversation(
                session_id=session_id,
                limit=limit,
            )

            messages = []
            for msg in conversation.messages:
                msg_data: dict[str, Any] = {
                    "id": str(msg.id),
                    "role": msg.role.value if hasattr(msg.role, "value") else str(msg.role),
                    "content": msg.content,
                    "timestamp": msg.created_at.isoformat() if msg.created_at else None,
                }
                if include_metadata and msg.metadata:
                    msg_data["metadata"] = msg.metadata
                messages.append(msg_data)

            return json.dumps(
                {
                    "session_id": session_id,
                    "message_count": len(messages),
                    "messages": messages,
                },
                default=str,
            )

        except Exception as e:
            logger.error(f"Error in memory_get_conversation: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def memory_list_sessions(
        ctx: Context,
        limit: int = 20,
        offset: int = 0,
    ) -> str:
        """List available conversation sessions with previews.

        Returns session IDs, message counts, and first/last message previews.
        Useful for browsing stored conversations.

        Args:
            limit: Maximum sessions to return (default: 20).
            offset: Offset for pagination (default: 0).
        """
        client = get_client(ctx)

        try:
            sessions = await client.short_term.list_sessions(
                limit=limit,
                offset=offset,
            )

            session_list = []
            for session in sessions:
                session_list.append(
                    {
                        "session_id": session.session_id,
                        "title": session.title,
                        "message_count": session.message_count,
                        "created_at": (
                            session.created_at.isoformat() if session.created_at else None
                        ),
                        "updated_at": (
                            session.updated_at.isoformat() if session.updated_at else None
                        ),
                        "first_message_preview": session.first_message_preview,
                        "last_message_preview": session.last_message_preview,
                    }
                )

            return json.dumps(
                {
                    "session_count": len(session_list),
                    "offset": offset,
                    "sessions": session_list,
                },
                default=str,
            )

        except Exception as e:
            logger.error(f"Error in memory_list_sessions: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def memory_get_entity(
        ctx: Context,
        name: str,
        entity_type: str | None = None,
        include_neighbors: bool = True,
        max_hops: int = 1,
    ) -> str:
        """Get detailed entity information with graph relationships.

        Searches the knowledge graph for an entity by name and optionally
        traverses relationships to find connected entities.

        Args:
            name: Entity name to look up.
            entity_type: Filter by POLE+O type (optional).
            include_neighbors: Traverse graph for related entities (default: true).
            max_hops: Relationship traversal depth, 1-3 (default: 1).
        """
        client = get_client(ctx)

        try:
            entities = await client.long_term.search_entities(
                query=name,
                entity_types=[entity_type] if entity_type else None,
                limit=1,
            )

            if not entities:
                return json.dumps({"found": False, "name": name})

            entity = entities[0]
            result: dict[str, Any] = {
                "found": True,
                "entity": {
                    "id": str(entity.id),
                    "name": entity.display_name,
                    "type": (
                        entity.type.value if hasattr(entity.type, "value") else str(entity.type)
                    ),
                    "subtype": entity.subtype if hasattr(entity, "subtype") else None,
                    "description": entity.description,
                    "aliases": entity.aliases if hasattr(entity, "aliases") else [],
                },
            }

            if include_neighbors:
                neighbors = await _get_entity_neighbors(client, str(entity.id), max_hops)
                result["neighbors"] = neighbors

            return json.dumps(result, default=str)

        except Exception as e:
            logger.error(f"Error in memory_get_entity: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def memory_export_graph(
        ctx: Context,
        session_id: str | None = None,
        memory_types: list[str] | None = None,
        limit: int = 500,
    ) -> str:
        """Export a subgraph as JSON for visualization or debugging.

        Returns nodes and relationships from the memory graph, formatted
        for visualization libraries.

        Args:
            session_id: Filter to a specific session (optional).
            memory_types: Types to include: 'short_term', 'long_term', 'reasoning'.
                Defaults to all types.
            limit: Maximum nodes per memory type (default: 500).
        """
        client = get_client(ctx)

        try:
            graph = await client.get_graph(
                memory_types=memory_types,
                session_id=session_id,
                limit=limit,
                include_embeddings=False,
            )

            return json.dumps(
                {
                    "node_count": len(graph.nodes),
                    "relationship_count": len(graph.relationships),
                    "nodes": [n.model_dump() for n in graph.nodes],
                    "relationships": [r.model_dump() for r in graph.relationships],
                    "metadata": graph.metadata,
                },
                default=str,
            )

        except Exception as e:
            logger.error(f"Error in memory_export_graph: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    async def memory_create_relationship(
        ctx: Context,
        source_name: str,
        target_name: str,
        relationship_type: str,
        description: str | None = None,
        confidence: float = 1.0,
    ) -> str:
        """Create a typed relationship between two entities.

        Both entities are looked up by name. If either is not found,
        an error is returned.

        Args:
            source_name: Name of the source entity.
            target_name: Name of the target entity.
            relationship_type: Relationship type in UPPER_SNAKE_CASE
                (e.g., 'WORKS_AT', 'LIVES_IN', 'FOUNDED').
            description: Optional description of the relationship.
            confidence: Confidence score 0.0-1.0 (default: 1.0).
        """
        client = get_client(ctx)

        try:
            # Resolve source entity
            source_entities = await client.long_term.search_entities(query=source_name, limit=1)
            if not source_entities:
                return json.dumps(
                    {"error": f"Source entity '{source_name}' not found in knowledge graph."}
                )

            # Resolve target entity
            target_entities = await client.long_term.search_entities(query=target_name, limit=1)
            if not target_entities:
                return json.dumps(
                    {"error": f"Target entity '{target_name}' not found in knowledge graph."}
                )

            source = source_entities[0]
            target = target_entities[0]

            rel = await client.long_term.add_relationship(
                source=source,
                target=target,
                relationship_type=relationship_type,
                description=description,
                confidence=confidence,
            )

            return json.dumps(
                {
                    "stored": True,
                    "type": "relationship",
                    "id": str(rel.id) if hasattr(rel, "id") else None,
                    "source": source.display_name,
                    "target": target.display_name,
                    "relationship_type": relationship_type,
                },
                default=str,
            )

        except Exception as e:
            logger.error(f"Error in memory_create_relationship: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    async def memory_start_trace(
        ctx: Context,
        session_id: str,
        task: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Begin recording a reasoning trace for a complex task.

        Creates a new trace that captures the step-by-step reasoning
        process. Use memory_record_step to add steps and
        memory_complete_trace when finished.

        Args:
            session_id: Session ID for the trace.
            task: Description of the task being solved.
            metadata: Optional metadata (e.g., model name, complexity).
        """
        client = get_client(ctx)

        try:
            trace = await client.reasoning.start_trace(
                session_id=session_id,
                task=task,
                metadata=metadata or {},
            )
            return json.dumps(
                {
                    "started": True,
                    "trace_id": str(trace.id),
                    "session_id": session_id,
                    "task": task,
                },
                default=str,
            )

        except Exception as e:
            logger.error(f"Error in memory_start_trace: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    async def memory_record_step(
        ctx: Context,
        trace_id: str,
        thought: str | None = None,
        action: str | None = None,
        observation: str | None = None,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        tool_result: str | None = None,
    ) -> str:
        """Record a reasoning step within a trace.

        Captures a thought-action-observation cycle. Optionally records
        an associated tool call.

        Args:
            trace_id: ID of the trace to add the step to.
            thought: The reasoning/thinking for this step.
            action: The action taken or decided upon.
            observation: The result or observation after the action.
            tool_name: Name of tool called in this step (optional).
            tool_args: Arguments passed to the tool (optional).
            tool_result: Result from the tool call (optional).
        """
        client = get_client(ctx)

        try:
            step = await client.reasoning.add_step(
                trace_id=trace_id,
                thought=thought,
                action=action,
                observation=observation,
            )

            tool_call_id = None
            if tool_name:
                tc = await client.reasoning.record_tool_call(
                    step_id=step.id,
                    tool_name=tool_name,
                    arguments=tool_args or {},
                    result=tool_result,
                )
                tool_call_id = str(tc.id) if hasattr(tc, "id") else None

            return json.dumps(
                {
                    "recorded": True,
                    "step_id": str(step.id),
                    "trace_id": trace_id,
                    "has_tool_call": tool_name is not None,
                    "tool_call_id": tool_call_id,
                },
                default=str,
            )

        except Exception as e:
            logger.error(f"Error in memory_record_step: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool(annotations=WRITE_ANNOTATIONS)
    async def memory_complete_trace(
        ctx: Context,
        trace_id: str,
        outcome: str | None = None,
        success: bool = True,
    ) -> str:
        """Complete a reasoning trace with the final outcome.

        Marks the trace as finished and records whether the task
        was completed successfully.

        Args:
            trace_id: ID of the trace to complete.
            outcome: Final outcome or result description.
            success: Whether the task was completed successfully (default: true).
        """
        client = get_client(ctx)

        try:
            await client.reasoning.complete_trace(
                trace_id=trace_id,
                outcome=outcome,
                success=success,
            )
            return json.dumps(
                {
                    "completed": True,
                    "trace_id": trace_id,
                    "outcome": outcome,
                    "success": success,
                },
                default=str,
            )

        except Exception as e:
            logger.error(f"Error in memory_complete_trace: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def memory_get_observations(
        ctx: Context,
        session_id: str,
    ) -> str:
        """Get observations and extracted insights for a session.

        Returns the three-tier context hierarchy:
        - Reflections: high-level session summaries (generated when token
          threshold is exceeded)
        - Observations: extracted facts, decisions, and preferences
          accumulated during the conversation
        - Session stats: message count, approximate token usage

        Args:
            session_id: Session ID to get observations for.
        """
        try:
            # Try to get observer from lifespan context
            observer = ctx.request_context.lifespan_context.get("observer")
            if observer is not None:
                result = await observer.get_observations(session_id)
                return json.dumps(result, default=str)

            # Fallback: return basic stats if no observer available
            client = get_client(ctx)
            conversation = await client.short_term.get_conversation(
                session_id=session_id,
                limit=100,
            )
            return json.dumps(
                {
                    "session_id": session_id,
                    "message_count": len(conversation.messages),
                    "approximate_tokens": 0,
                    "threshold_exceeded": False,
                    "reflections": [],
                    "observations": [],
                    "entity_names": [],
                    "topics": [],
                },
                default=str,
            )

        except Exception as e:
            logger.error(f"Error in memory_get_observations: {e}")
            return json.dumps({"error": str(e)})

    @mcp.tool(annotations=READ_ANNOTATIONS)
    async def graph_query(
        ctx: Context,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> str:
        """Execute a read-only Cypher query against the knowledge graph.

        MATCH/RETURN queries and read-only CALL procedures (e.g., CALL db.*,
        CALL apoc.*) are allowed. Write operations (CREATE, MERGE, DELETE,
        SET, REMOVE) are blocked for safety.

        Args:
            query: Cypher query string (read-only).
            parameters: Query parameters as key-value pairs.
        """
        if not _is_read_only_query(query):
            return json.dumps(
                {
                    "error": "Only read-only queries are allowed. "
                    "Write operations (CREATE, MERGE, DELETE, SET, REMOVE) are not permitted."
                }
            )

        client = get_client(ctx)

        try:
            records = await client.graph.execute_read(query, parameters or {})
            return json.dumps(
                {
                    "success": True,
                    "row_count": len(records),
                    "rows": records,
                },
                default=str,
            )

        except Exception as e:
            logger.error(f"Error in graph_query: {e}")
            return json.dumps({"error": str(e)})


# ── Helpers ───────────────────────────────────────────────────────────


async def _get_entity_neighbors(
    client: Any,
    entity_id: str,
    max_hops: int = 1,
) -> list[dict[str, Any]]:
    """Get neighboring entities via graph traversal.

    Args:
        client: MemoryClient instance.
        entity_id: Starting entity ID.
        max_hops: Maximum relationship depth (clamped to 1-3).

    Returns:
        List of neighboring entities with relationships.
    """
    max_hops = min(max(max_hops, 1), 3)
    query = f"""
    MATCH (e:Entity {{id: $entity_id}})-[r*1..{max_hops}]-(neighbor:Entity)
    WHERE neighbor.id <> $entity_id
    WITH DISTINCT neighbor, r
    RETURN neighbor.id AS id,
           neighbor.name AS name,
           neighbor.type AS type,
           neighbor.description AS description
    LIMIT 20
    """

    try:
        records = await client.graph.execute_read(
            query,
            {"entity_id": entity_id},
        )
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "description": r["description"],
            }
            for r in records
        ]
    except Exception as e:
        logger.debug(f"Error getting neighbors: {e}")
        return []
