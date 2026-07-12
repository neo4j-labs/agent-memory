"""Strands Agents SDK integration for neo4j-agent-memory.

This module provides @tool decorated functions for use with AWS Strands Agents.
These tools enable Strands agents to interact with Neo4j Context Graphs for
semantic memory, entity retrieval, and knowledge graph operations.

Example:
    from strands import Agent
    from neo4j_agent_memory.integrations.strands import context_graph_tools

    tools = context_graph_tools(
        neo4j_uri=os.environ["NEO4J_URI"],
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ["NEO4J_PASSWORD"],
        embedding_provider="bedrock",
    )

    agent = Agent(
        model="anthropic.claude-sonnet-4-20250514-v1:0",
        tools=tools,
    )
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar

_T = TypeVar("_T")

# Transport modes accepted by the NAMS backend (mirrors NamsConfig.transport_mode).
TransportMode = Literal["auto", "rest", "bridge"]

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient

logger = logging.getLogger(__name__)

# Module-level client cache for tool reuse
_client_cache: dict[str, MemoryClient] = {}
_nams_client_cache: dict[tuple[str, str], list[_CachedNamsClient]] = {}


@dataclass(repr=False)
class _CachedNamsClient:
    api_key: str
    client: MemoryClient


def _is_valid_hf_model_id(model_id: str) -> bool:
    """Return True when model_id looks like a full HuggingFace repo id."""
    return "/" in model_id and not model_id.startswith("/") and not model_id.endswith("/")


def _run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run an async coroutine synchronously.

    Strands tools are synchronous, but MemoryClient is async.
    This helper runs async code in the appropriate event loop.
    """
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # We're already in an async context - create a new thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(coro))
            return future.result()
    else:
        # No running loop - safe to use asyncio.run
        return asyncio.run(coro)


def _get_nams_cache_bucket(endpoint: str, transport_mode: TransportMode) -> tuple[str, str]:
    """Return the process-local cache bucket key for NAMS clients."""
    return (endpoint, transport_mode)


def _require_nams_api_key(api_key: str) -> str:
    """Validate that NAMS cache/client creation has a non-empty API key."""
    if not api_key:
        raise ValueError("NAMS cache key generation requires a non-empty api_key.")
    return api_key


def _get_or_create_client(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_database: str,
    embedding_provider: str,
    embedding_model: str | None,
    **kwargs: Any,
) -> MemoryClient:
    """Get or create a MemoryClient instance.

    Uses a cache keyed by connection URI to avoid creating multiple clients.
    """
    cache_key = f"{neo4j_uri}:{neo4j_user}:{neo4j_database}"

    if cache_key not in _client_cache:
        from pydantic import SecretStr

        from neo4j_agent_memory import MemoryClient, MemorySettings
        from neo4j_agent_memory.config.settings import Neo4jConfig
        from neo4j_agent_memory.llm import from_provider

        # Strands accepts provider-name strings ("bedrock", "openai", ...);
        # translate them to the canonical ``"<provider>/<model>"`` shape
        # consumed by :func:`from_provider`.
        # Fall back to a sensible Bedrock default when the user did not
        # specify a model — preserves the v0.2 behavior of this helper.
        model_id = embedding_model or "amazon.titan-embed-text-v2:0"
        if embedding_provider == "sentence_transformers":
            # Accept full HuggingFace ids ("BAAI/bge-small-en-v1.5") as-is,
            # but normalize bare model names ("all-MiniLM-L6-v2") to the
            # canonical provider-string format consumed by from_provider().
            has_full_hf_id = _is_valid_hf_model_id(model_id)
            model_string = model_id if has_full_hf_id else f"sentence-transformers/{model_id}"
        else:
            provider_prefixes = {
                "bedrock": "bedrock/",
                "openai": "openai/",
                "vertex_ai": "vertex_ai/",
            }
            prefix = provider_prefixes.get(embedding_provider, "bedrock/")
            model_string = f"{prefix}{model_id}"

        embed_kwargs: dict[str, Any] = {}
        is_bedrock = embedding_provider == "bedrock"
        if is_bedrock:
            aws_region = kwargs.get("aws_region")
            aws_profile = kwargs.get("aws_profile")
            if aws_region is not None:
                embed_kwargs["aws_region"] = aws_region
            if aws_profile is not None:
                embed_kwargs["aws_profile"] = aws_profile

        embedding_provider_instance = from_provider(model_string, kind="embedding", **embed_kwargs)

        neo4j_config = Neo4jConfig(
            uri=neo4j_uri,
            username=neo4j_user,
            password=SecretStr(neo4j_password),
            database=neo4j_database,
        )

        settings = MemorySettings(
            neo4j=neo4j_config,
            embedding=embedding_provider_instance,
        )

        client = MemoryClient(settings)
        _client_cache[cache_key] = client

    return _client_cache[cache_key]


def _create_search_context_tool(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_database: str,
    embedding_provider: str,
    embedding_model: str | None,
    **kwargs: Any,
) -> Any:
    """Create the search_context tool with bound configuration."""
    try:
        from strands import tool
    except ImportError as e:
        raise ImportError(
            "strands-agents is required for Strands integration. "
            "Install with: pip install strands-agents"
        ) from e

    @tool
    def search_context(
        query: str,
        user_id: str,
        top_k: int = 10,
        min_score: float = 0.5,
        include_relationships: bool = True,
    ) -> list[dict[str, Any]]:
        """Search the Context Graph for relevant memories and entities.

        Use this tool when the user asks about things you might know
        from previous conversations or when you need to understand
        how different entities are connected.

        Args:
            query: The search query to find relevant context.
            user_id: The user ID to scope the search.
            top_k: Maximum number of results to return (default: 10).
            min_score: Minimum similarity score threshold (default: 0.5).
            include_relationships: Whether to include entity relationships (default: True).

        Returns:
            A list of relevant context items including messages, entities, and preferences.
        """

        async def _search() -> list[dict[str, Any]]:
            client = _get_or_create_client(
                neo4j_uri=neo4j_uri,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_password,
                neo4j_database=neo4j_database,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                **kwargs,
            )

            async with client:
                results: list[dict[str, Any]] = []

                # Search messages
                try:
                    messages = await client.short_term.search_messages(
                        query=query,
                        limit=top_k,
                        threshold=min_score,
                    )
                    for msg in messages:
                        results.append(
                            {
                                "type": "message",
                                "role": (
                                    msg.role.value if hasattr(msg.role, "value") else str(msg.role)
                                ),
                                "content": msg.content,
                                "timestamp": (
                                    msg.created_at.isoformat() if msg.created_at else None
                                ),
                                "score": msg.metadata.get("similarity") if msg.metadata else None,
                            }
                        )
                except Exception as e:
                    logger.debug(f"Message search failed: {e}")

                # Search entities
                try:
                    entities = await client.long_term.search_entities(
                        query=query,
                        limit=top_k,
                    )
                    for entity in entities:
                        entity_data: dict[str, Any] = {
                            "type": "entity",
                            "entity_type": (
                                entity.type.value
                                if hasattr(entity.type, "value")
                                else str(entity.type)
                            ),
                            "name": entity.display_name,
                            "description": entity.description,
                        }

                        # Include relationships if requested
                        if include_relationships and hasattr(entity, "id"):
                            try:
                                # Get relationships via Cypher
                                rel_query = """
                                MATCH (e:Entity {id: $entity_id})-[r]-(other:Entity)
                                RETURN type(r) AS relationship,
                                       other.displayName AS related_entity,
                                       other.type AS related_type
                                LIMIT 10
                                """
                                # v0.4: portable read-only Cypher (works on
                                # bolt + NAMS).
                                rels = await client.query.cypher(
                                    rel_query,
                                    {"entity_id": str(entity.id)},
                                )
                                if rels:
                                    entity_data["relationships"] = [
                                        {
                                            "type": r["relationship"],
                                            "entity": r["related_entity"],
                                            "entity_type": r["related_type"],
                                        }
                                        for r in rels
                                    ]
                            except Exception as e:
                                logger.debug(f"Relationship fetch failed: {e}")

                        results.append(entity_data)
                except Exception as e:
                    logger.debug(f"Entity search failed: {e}")

                # Search preferences
                try:
                    preferences = await client.long_term.search_preferences(
                        query=query,
                        limit=top_k,
                    )
                    for pref in preferences:
                        results.append(
                            {
                                "type": "preference",
                                "category": pref.category,
                                "preference": pref.preference,
                                "context": pref.context,
                            }
                        )
                except Exception as e:
                    logger.debug(f"Preference search failed: {e}")

                return results

        return _run_async(_search())

    return search_context


def _create_get_entity_graph_tool(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_database: str,
    embedding_provider: str,
    embedding_model: str | None,
    **kwargs: Any,
) -> Any:
    """Create the get_entity_graph tool with bound configuration."""
    try:
        from strands import tool
    except ImportError as e:
        raise ImportError(
            "strands-agents is required for Strands integration. "
            "Install with: pip install strands-agents"
        ) from e

    @tool
    def get_entity_graph(
        entity_name: str,
        user_id: str,
        depth: int = 2,
        relationship_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Get the relationship graph around an entity.

        Use this tool to understand how an entity connects to other
        entities (customers, projects, team members, issues, etc.).

        Args:
            entity_name: The name of the entity to explore.
            user_id: The user ID for context.
            depth: How many relationship hops to traverse (default: 2, max: 3).
            relationship_types: Optional list of relationship types to filter.

        Returns:
            A dictionary containing the entity and its relationship graph.
        """

        async def _get_graph() -> dict[str, Any]:
            client = _get_or_create_client(
                neo4j_uri=neo4j_uri,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_password,
                neo4j_database=neo4j_database,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                **kwargs,
            )

            async with client:
                # Find the entity first
                entities = await client.long_term.search_entities(
                    query=entity_name,
                    limit=1,
                )

                if not entities:
                    return {
                        "found": False,
                        "entity_name": entity_name,
                        "message": f"Entity '{entity_name}' not found in the knowledge graph.",
                    }

                entity = entities[0]
                entity_id = str(entity.id)

                # Clamp depth to safe range
                safe_depth = min(max(depth, 1), 3)

                # Build relationship type filter
                rel_filter = ""
                if relationship_types:
                    rel_types = "|".join(relationship_types)
                    rel_filter = f":{rel_types}"

                # Get the subgraph
                query = f"""
                MATCH path = (start:Entity {{id: $entity_id}})-[r{rel_filter}*1..{safe_depth}]-(connected:Entity)
                WITH start, connected, relationships(path) AS rels, nodes(path) AS pathNodes
                UNWIND rels AS rel
                WITH start, connected,
                     startNode(rel) AS from_node,
                     endNode(rel) AS to_node,
                     type(rel) AS rel_type
                RETURN DISTINCT
                    from_node.displayName AS from_entity,
                    from_node.type AS from_type,
                    rel_type AS relationship,
                    to_node.displayName AS to_entity,
                    to_node.type AS to_type
                LIMIT 50
                """

                try:
                    # v0.4: portable read-only Cypher accessor.
                    records = await client.query.cypher(
                        query,
                        {"entity_id": entity_id},
                    )

                    # Build graph structure
                    nodes: dict[str, dict[str, Any]] = {
                        entity.display_name: {
                            "name": entity.display_name,
                            "type": (
                                entity.type.value
                                if hasattr(entity.type, "value")
                                else str(entity.type)
                            ),
                            "description": entity.description,
                            "is_center": True,
                        }
                    }

                    edges: list[dict[str, str]] = []

                    for record in records:
                        # Add nodes
                        from_name = record["from_entity"]
                        to_name = record["to_entity"]

                        if from_name and from_name not in nodes:
                            nodes[from_name] = {
                                "name": from_name,
                                "type": record["from_type"],
                                "is_center": False,
                            }

                        if to_name and to_name not in nodes:
                            nodes[to_name] = {
                                "name": to_name,
                                "type": record["to_type"],
                                "is_center": False,
                            }

                        # Add edge
                        if from_name and to_name:
                            edges.append(
                                {
                                    "from": from_name,
                                    "to": to_name,
                                    "relationship": record["relationship"],
                                }
                            )

                    return {
                        "found": True,
                        "center_entity": {
                            "name": entity.display_name,
                            "type": (
                                entity.type.value
                                if hasattr(entity.type, "value")
                                else str(entity.type)
                            ),
                            "description": entity.description,
                        },
                        "graph": {
                            "nodes": list(nodes.values()),
                            "edges": edges,
                            "node_count": len(nodes),
                            "edge_count": len(edges),
                        },
                    }

                except Exception as e:
                    logger.error(f"Graph traversal failed: {e}")
                    return {
                        "found": True,
                        "center_entity": {
                            "name": entity.display_name,
                            "type": (
                                entity.type.value
                                if hasattr(entity.type, "value")
                                else str(entity.type)
                            ),
                            "description": entity.description,
                        },
                        "graph": {"nodes": [], "edges": [], "error": str(e)},
                    }

        return _run_async(_get_graph())

    return get_entity_graph


def _create_add_memory_tool(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_database: str,
    embedding_provider: str,
    embedding_model: str | None,
    **kwargs: Any,
) -> Any:
    """Create the add_memory tool with bound configuration."""
    try:
        from strands import tool
    except ImportError as e:
        raise ImportError(
            "strands-agents is required for Strands integration. "
            "Install with: pip install strands-agents"
        ) from e

    @tool
    def add_memory(
        content: str,
        user_id: str,
        session_id: str | None = None,
        extract_entities: bool = True,
    ) -> dict[str, Any]:
        """Store a memory with automatic entity extraction.

        Use this tool to save important information from the conversation
        that should be remembered for future interactions.

        Args:
            content: The content to remember.
            user_id: The user ID this memory belongs to.
            session_id: Optional session ID to associate with.
            extract_entities: Whether to extract entities from the content (default: True).

        Returns:
            Confirmation of stored memory with extracted entities.
        """

        async def _add() -> dict[str, Any]:
            client = _get_or_create_client(
                neo4j_uri=neo4j_uri,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_password,
                neo4j_database=neo4j_database,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                **kwargs,
            )

            async with client:
                # Use session_id or generate one from user_id
                effective_session = session_id or f"strands-{user_id}"

                # Store the message
                message = await client.short_term.add_message(
                    session_id=effective_session,
                    role="user",
                    content=content,
                    extract_entities=extract_entities,
                    generate_embedding=True,
                )

                result: dict[str, Any] = {
                    "stored": True,
                    "message_id": str(message.id),
                    "session_id": effective_session,
                    "content_preview": content[:100] + "..." if len(content) > 100 else content,
                }

                # If entities were extracted, include them
                if extract_entities and message.metadata:
                    extracted = message.metadata.get("extracted_entities", [])
                    if extracted:
                        result["extracted_entities"] = extracted

                return result

        return _run_async(_add())

    return add_memory


def _create_get_user_preferences_tool(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_database: str,
    embedding_provider: str,
    embedding_model: str | None,
    **kwargs: Any,
) -> Any:
    """Create the get_user_preferences tool with bound configuration."""
    try:
        from strands import tool
    except ImportError as e:
        raise ImportError(
            "strands-agents is required for Strands integration. "
            "Install with: pip install strands-agents"
        ) from e

    @tool
    def get_user_preferences(
        user_id: str,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve user preference subgraph.

        Use this tool to get known preferences for a user, optionally
        filtered by category.

        Args:
            user_id: The user ID to get preferences for.
            category: Optional category to filter preferences (e.g., "food", "travel").

        Returns:
            A list of user preferences with categories and context.
        """

        async def _get_prefs() -> list[dict[str, Any]]:
            client = _get_or_create_client(
                neo4j_uri=neo4j_uri,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_password,
                neo4j_database=neo4j_database,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                **kwargs,
            )

            async with client:
                results: list[dict[str, Any]] = []

                if category:
                    # Search for specific category
                    preferences = await client.long_term.search_preferences(
                        query=category,
                        limit=20,
                    )
                    # Filter by exact category match
                    preferences = [p for p in preferences if p.category.lower() == category.lower()]
                else:
                    # Get all preferences (search with broad query)
                    preferences = await client.long_term.search_preferences(
                        query="preference",
                        limit=50,
                    )

                for pref in preferences:
                    results.append(
                        {
                            "id": str(pref.id),
                            "category": pref.category,
                            "preference": pref.preference,
                            "context": pref.context,
                            "confidence": pref.confidence,
                        }
                    )

                return results

        return _run_async(_get_prefs())

    return get_user_preferences


def context_graph_tools(
    neo4j_uri: str | None = None,
    neo4j_user: str = "neo4j",
    neo4j_password: str | None = None,
    neo4j_database: str = "neo4j",
    embedding_provider: str = "bedrock",
    embedding_model: str | None = None,
    **kwargs: Any,
) -> list[Any]:
    """Create all Context Graph tools configured for use with Strands agents.

    This factory function creates a list of @tool decorated functions that can
    be passed directly to a Strands Agent.

    Args:
        neo4j_uri: Neo4j connection URI. Defaults to NEO4J_URI env var.
        neo4j_user: Neo4j username. Defaults to "neo4j".
        neo4j_password: Neo4j password. Defaults to NEO4J_PASSWORD env var.
        neo4j_database: Neo4j database name. Defaults to "neo4j".
        embedding_provider: Embedding provider ("bedrock", "openai", "vertex_ai").
            Defaults to "bedrock".
        embedding_model: Optional model override for embeddings.
        **kwargs: Additional configuration (aws_region, aws_profile, etc.)

    Returns:
        A list of tool functions ready for use with Strands Agent.

    Example:
        from strands import Agent
        from neo4j_agent_memory.integrations.strands import context_graph_tools

        tools = context_graph_tools(
            neo4j_uri="neo4j+s://xxx.databases.neo4j.io",
            neo4j_password="password",
            embedding_provider="bedrock",
            aws_region="us-east-1",
        )

        agent = Agent(
            model="anthropic.claude-sonnet-4-20250514-v1:0",
            tools=tools,
        )

        response = agent("What do you know about our project timeline?")
    """
    import os

    # Get connection details from environment if not provided
    uri = neo4j_uri or os.environ.get("NEO4J_URI")
    password = neo4j_password or os.environ.get("NEO4J_PASSWORD")

    if not uri:
        raise ValueError(
            "neo4j_uri is required. Provide it directly or set NEO4J_URI environment variable."
        )
    if not password:
        raise ValueError(
            "neo4j_password is required. Provide it directly or set NEO4J_PASSWORD environment variable."
        )

    # Common config for all tools
    config: dict[str, Any] = {
        "neo4j_uri": uri,
        "neo4j_user": neo4j_user,
        "neo4j_password": password,
        "neo4j_database": neo4j_database,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        **kwargs,
    }

    return [
        _create_search_context_tool(**config),
        _create_get_entity_graph_tool(**config),
        _create_add_memory_tool(**config),
        _create_get_user_preferences_tool(**config),
    ]


def clear_client_cache() -> None:
    """Clear the cached MemoryClient instances.

    Call this when you want future tool invocations to create fresh clients.
    """
    global _client_cache, _nams_client_cache
    _client_cache.clear()
    _nams_client_cache.clear()


# =============================================================================
# NAMS-flavored Strands tools (v0.4)
# =============================================================================


def _get_or_create_nams_client(
    endpoint: str,
    api_key: str,
    transport_mode: TransportMode = "auto",
) -> MemoryClient:
    """Build (or retrieve cached) a NAMS-backed MemoryClient for Strands tools.

    Cached by endpoint and transport mode, with per-bucket API key matching,
    so repeat tool invocations in the same process can reuse the same configured client
    without exposing API key material in the cache key.
    Each tool invocation still opens and closes the client's underlying
    HTTP transport via ``async with client:``.
    """
    api_key = _require_nams_api_key(api_key)
    bucket = _get_nams_cache_bucket(endpoint, transport_mode)
    cached_clients = _nams_client_cache.setdefault(bucket, [])
    for cached in cached_clients:
        if cached.api_key == api_key:
            return cached.client

    from pydantic import SecretStr

    from neo4j_agent_memory import MemoryClient, MemorySettings, NamsConfig

    settings = MemorySettings(
        backend="nams",
        nams=NamsConfig(
            endpoint=endpoint,
            api_key=SecretStr(api_key),
            # Strands runs tools in short bursts via sync wrappers —
            # skipping probe avoids a round-trip on every call.
            validate_on_connect=False,
            transport_mode=transport_mode,
        ),
    )
    client = MemoryClient(settings)
    cached_clients.append(_CachedNamsClient(api_key=api_key, client=client))
    return client


def _nams_search_context_tool(endpoint: str, api_key: str, transport_mode: TransportMode) -> Any:
    """NAMS-backed search_context tool (Strands @tool)."""
    try:
        from strands import tool
    except ImportError as e:
        raise ImportError(
            "strands-agents is required for Strands integration. "
            "Install with: pip install strands-agents"
        ) from e

    @tool
    def search_context(
        query: str,
        user_id: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Search the NAMS Context Graph for relevant memories.

        Uses the hosted NAMS service rather than a direct Neo4j connection.

        Args:
            query: The search query.
            user_id: User identifier for per-user scoping.
            top_k: Maximum number of results.

        Returns:
            List of memory items (messages, entities, preferences).
        """

        async def _search() -> list[dict[str, Any]]:
            client = _get_or_create_nams_client(endpoint, api_key, transport_mode)
            async with client:
                results: list[dict[str, Any]] = []
                try:
                    messages = await client.short_term.search_messages(
                        query=query, session_id=user_id, limit=top_k
                    )
                    for msg in messages:
                        role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
                        results.append({"type": "message", "role": role, "content": msg.content})
                except Exception as e:
                    logger.debug(f"NAMS message search failed: {e}")
                try:
                    entities = await client.long_term.search_entities(query=query, limit=top_k)
                    for entity in entities:
                        results.append(
                            {
                                "type": "entity",
                                "name": entity.display_name,
                                "entity_type": entity.type,
                                "description": entity.description,
                            }
                        )
                except Exception as e:
                    logger.debug(f"NAMS entity search failed: {e}")
                return results

        return _run_async(_search())

    return search_context


def _nams_set_entity_feedback_tool(
    endpoint: str, api_key: str, transport_mode: TransportMode
) -> Any:
    """NAMS-only @tool — record positive/negative feedback on an entity."""
    try:
        from strands import tool
    except ImportError as e:
        raise ImportError("strands-agents is required for Strands integration.") from e

    @tool
    def set_entity_feedback(entity_id: str, feedback: str, user_id: str | None = None) -> str:
        """Record feedback on an entity in NAMS.

        Args:
            entity_id: Entity UUID.
            feedback: Feedback string ("positive" or "negative").
            user_id: Optional per-user scoping.

        Returns:
            Confirmation message.
        """

        async def _set() -> str:
            client = _get_or_create_nams_client(endpoint, api_key, transport_mode)
            async with client:
                # Platinum-tier — present on NamsLongTermMemory at runtime.
                await client.long_term.set_entity_feedback(  # type: ignore[attr-defined]
                    entity_id, feedback, user_identifier=user_id
                )
            return f"Recorded {feedback!r} feedback on entity {entity_id}."

        return str(_run_async(_set()))

    return set_entity_feedback


def _nams_get_entity_provenance_tool(
    endpoint: str, api_key: str, transport_mode: TransportMode
) -> Any:
    """NAMS-only @tool — fetch sources + extractors for an entity."""
    try:
        from strands import tool
    except ImportError as e:
        raise ImportError("strands-agents is required for Strands integration.") from e

    @tool
    def get_entity_provenance(entity_id: str) -> dict[str, Any]:
        """Get the provenance record for an entity (NAMS Platinum).

        Args:
            entity_id: Entity UUID.

        Returns:
            Dict with ``sources`` and ``extractors`` keys.
        """

        async def _get() -> dict[str, Any]:
            client = _get_or_create_nams_client(endpoint, api_key, transport_mode)
            async with client:
                return await client.long_term.get_entity_provenance(entity_id)  # type: ignore[arg-type]

        result = _run_async(_get())
        return result if isinstance(result, dict) else {}

    return get_entity_provenance


def _nams_cypher_tool(endpoint: str, api_key: str, transport_mode: TransportMode) -> Any:
    """NAMS-only @tool — read-only Cypher escape hatch (POST /v1/query)."""
    try:
        from strands import tool
    except ImportError as e:
        raise ImportError("strands-agents is required for Strands integration.") from e

    @tool
    def cypher_query(query: str) -> list[dict[str, Any]]:
        """Execute a read-only Cypher query via NAMS.

        Writes (CREATE/MERGE/DELETE/SET/REMOVE) are rejected client-side.

        Args:
            query: Read-only Cypher query string.

        Returns:
            Result rows as a list of dicts.
        """

        async def _q() -> list[dict[str, Any]]:
            client = _get_or_create_nams_client(endpoint, api_key, transport_mode)
            async with client:
                return await client.query.cypher(query)

        result = _run_async(_q())
        return result if isinstance(result, list) else []

    return cypher_query


def nams_context_graph_tools(
    endpoint: str | None = None,
    api_key: str | None = None,
    transport_mode: TransportMode = "auto",
) -> list[Any]:
    """Create Strands @tool functions backed by NAMS rather than direct Neo4j.

    Returns a focused tool set sized for NAMS Platinum semantics:

    * ``search_context`` — search messages + entities (works on both
      backends, but here scoped to a NAMS-backed client).
    * ``set_entity_feedback`` — NAMS Platinum tool.
    * ``get_entity_provenance`` — NAMS Gold tool.
    * ``cypher_query`` — read-only Cypher via NAMS REST.

    Bolt-flavored full graph traversal (``get_entity_graph``) is omitted
    because NAMS's hosted Cypher endpoint can't replicate the bespoke
    deep-traversal semantics one-to-one. Use ``cypher_query`` with a
    bounded ``MATCH (e)-[*1..2]-(n) RETURN ...`` instead.

    Args:
        endpoint: NAMS endpoint URL. Defaults to ``MEMORY_ENDPOINT`` env
            var; falls back to ``https://memory.neo4jlabs.com/v1``.
        api_key: NAMS API key. Defaults to ``MEMORY_API_KEY`` env var.
        transport_mode: ``"auto"`` (default), ``"rest"``, or ``"bridge"``.

    Returns:
        List of Strands @tool functions ready for ``Agent(tools=...)``.

    Example::

        from strands import Agent
        from neo4j_agent_memory.integrations.strands import (
            nams_context_graph_tools,
        )

        tools = nams_context_graph_tools()  # picks up MEMORY_API_KEY from env
        agent = Agent(model="anthropic.claude-sonnet-4-20250514-v1:0", tools=tools)
    """
    import os

    endpoint = endpoint or os.environ.get("MEMORY_ENDPOINT") or "https://memory.neo4jlabs.com/v1"
    api_key = api_key or os.environ.get("MEMORY_API_KEY")
    if not api_key:
        raise ValueError("api_key is required. Pass api_key= or set MEMORY_API_KEY env var.")

    return [
        _nams_search_context_tool(endpoint, api_key, transport_mode),
        _nams_set_entity_feedback_tool(endpoint, api_key, transport_mode),
        _nams_get_entity_provenance_tool(endpoint, api_key, transport_mode),
        _nams_cypher_tool(endpoint, api_key, transport_mode),
    ]
