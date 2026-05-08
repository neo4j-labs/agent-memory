"""Short-term memory for conversations and messages."""

import json
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from neo4j_agent_memory.core.memory import BaseMemory, MemoryEntry
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.graph.query_builder import build_create_entity_query


def _serialize_metadata(metadata: dict[str, Any] | None) -> str | None:
    """Serialize metadata dict to JSON string for Neo4j storage."""
    if metadata is None or metadata == {}:
        return None
    return json.dumps(metadata)


def _deserialize_metadata(metadata_str: str | None) -> dict[str, Any]:
    """Deserialize metadata from JSON string."""
    if metadata_str is None:
        return {}
    try:
        return json.loads(metadata_str)
    except (json.JSONDecodeError, TypeError):
        return {}


def _to_python_datetime(neo4j_datetime) -> datetime:
    """Convert Neo4j DateTime to Python datetime."""
    if neo4j_datetime is None:
        return datetime.utcnow()
    if isinstance(neo4j_datetime, datetime):
        return neo4j_datetime
    # Neo4j DateTime has to_native() method
    try:
        return neo4j_datetime.to_native()
    except AttributeError:
        return datetime.utcnow()


def _build_metadata_filter_clause_json(
    filters: dict[str, Any], param_prefix: str = "mf", metadata_prop: str = "m.metadata"
) -> tuple[str, dict[str, Any]]:
    """
    Build Cypher WHERE clause from metadata filters using JSON string matching.

    This version works without APOC by using string CONTAINS on the JSON metadata.
    Only supports simple equality filters for string values.

    Args:
        filters: Dictionary of filter conditions (only simple equality supported)
        param_prefix: Prefix for parameter names
        metadata_prop: Property path for the metadata JSON string

    Returns:
        Tuple of (WHERE clause string, parameters dict)
    """
    if not filters:
        return "", {}

    clauses = []
    params = {}

    for i, (key, value) in enumerate(filters.items()):
        param_name = f"{param_prefix}_{i}"

        if isinstance(value, dict):
            # Operator-based filters not supported without APOC
            # Fall back to simple equality if $eq operator
            if "$eq" in value:
                value = value["$eq"]
            else:
                # Skip unsupported operators
                continue

        if isinstance(value, str):
            # For string values, use CONTAINS on the JSON string
            # Match pattern like: "key": "value" or "key":"value"
            # Use a pattern that matches the JSON encoding
            json_pattern = f'"{key}": "{value}"'
            json_pattern_no_space = f'"{key}":"{value}"'
            params[param_name] = json_pattern
            params[f"{param_name}_alt"] = json_pattern_no_space
            clauses.append(
                f"({metadata_prop} CONTAINS ${param_name} OR {metadata_prop} CONTAINS ${param_name}_alt)"
            )
        elif isinstance(value, bool):
            # For boolean values
            json_pattern = f'"{key}": {str(value).lower()}'
            json_pattern_no_space = f'"{key}":{str(value).lower()}'
            params[param_name] = json_pattern
            params[f"{param_name}_alt"] = json_pattern_no_space
            clauses.append(
                f"({metadata_prop} CONTAINS ${param_name} OR {metadata_prop} CONTAINS ${param_name}_alt)"
            )
        elif isinstance(value, (int, float)):
            # For numeric values
            json_pattern = f'"{key}": {value}'
            json_pattern_no_space = f'"{key}":{value}'
            params[param_name] = json_pattern
            params[f"{param_name}_alt"] = json_pattern_no_space
            clauses.append(
                f"({metadata_prop} CONTAINS ${param_name} OR {metadata_prop} CONTAINS ${param_name}_alt)"
            )

    return " AND ".join(clauses) if clauses else "", params


def _build_metadata_filter_clause(
    filters: dict[str, Any], param_prefix: str = "mf", metadata_var: str = "md"
) -> tuple[str, dict[str, Any]]:
    """
    Build Cypher WHERE clause from metadata filters.

    Since metadata is stored as a JSON string, this function generates clauses
    that work with a pre-parsed metadata map variable (e.g., from apoc.convert.fromJsonMap).

    Supports:
    - Simple equality: {"key": "value"}
    - Comparison operators: {"key": {"$gt": 5}}
    - List membership: {"key": {"$in": [1, 2, 3]}}

    Args:
        filters: Dictionary of filter conditions
        param_prefix: Prefix for parameter names
        metadata_var: Variable name for the parsed metadata map

    Returns:
        Tuple of (WHERE clause string, parameters dict)
    """
    if not filters:
        return "", {}

    clauses = []
    params = {}

    for i, (key, value) in enumerate(filters.items()):
        param_name = f"{param_prefix}_{i}"

        if isinstance(value, dict):
            # Operator-based filter
            for op, op_value in value.items():
                op_param = f"{param_name}_{op.lstrip('$')}"
                params[op_param] = op_value

                if op == "$eq":
                    clauses.append(f"{metadata_var}.`{key}` = ${op_param}")
                elif op == "$ne":
                    clauses.append(f"{metadata_var}.`{key}` <> ${op_param}")
                elif op == "$gt":
                    clauses.append(f"{metadata_var}.`{key}` > ${op_param}")
                elif op == "$gte":
                    clauses.append(f"{metadata_var}.`{key}` >= ${op_param}")
                elif op == "$lt":
                    clauses.append(f"{metadata_var}.`{key}` < ${op_param}")
                elif op == "$lte":
                    clauses.append(f"{metadata_var}.`{key}` <= ${op_param}")
                elif op == "$in":
                    clauses.append(f"{metadata_var}.`{key}` IN ${op_param}")
                elif op == "$nin":
                    clauses.append(f"NOT {metadata_var}.`{key}` IN ${op_param}")
                elif op == "$exists":
                    if op_value:
                        clauses.append(f"{metadata_var}.`{key}` IS NOT NULL")
                    else:
                        clauses.append(f"{metadata_var}.`{key}` IS NULL")
                elif op == "$contains":
                    clauses.append(f"{metadata_var}.`{key}` CONTAINS ${op_param}")
                elif op == "$startswith":
                    clauses.append(f"{metadata_var}.`{key}` STARTS WITH ${op_param}")
                elif op == "$endswith":
                    clauses.append(f"{metadata_var}.`{key}` ENDS WITH ${op_param}")
        else:
            # Simple equality
            params[param_name] = value
            clauses.append(f"{metadata_var}.`{key}` = ${param_name}")

    return " AND ".join(clauses), params


if TYPE_CHECKING:
    from neo4j_agent_memory.embeddings.base import Embedder
    from neo4j_agent_memory.extraction.base import EntityExtractor
    from neo4j_agent_memory.graph.client import Neo4jClient


class MessageRole(str, Enum):
    """Message role in a conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Message(MemoryEntry):
    """A single message in a conversation."""

    role: MessageRole = Field(description="Message role")
    content: str = Field(description="Message content")
    conversation_id: UUID | None = Field(default=None, description="Parent conversation ID")
    tool_calls: list[dict[str, Any]] | None = Field(default=None, description="Tool calls if any")


class Conversation(BaseModel):
    """A conversation thread containing messages."""

    id: UUID = Field(default_factory=uuid4)
    session_id: str = Field(description="User/agent session identifier")
    title: str | None = Field(default=None, description="Conversation title")
    messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionInfo(BaseModel):
    """Summary information about a session."""

    session_id: str = Field(description="Session identifier")
    title: str | None = Field(default=None, description="Session title")
    created_at: datetime = Field(description="When the session was created")
    updated_at: datetime | None = Field(
        default=None, description="When the session was last updated"
    )
    message_count: int = Field(default=0, description="Number of messages in the session")
    first_message_preview: str | None = Field(
        default=None, description="Preview of the first message (truncated)"
    )
    last_message_preview: str | None = Field(
        default=None, description="Preview of the last message (truncated)"
    )


class ConversationSummary(BaseModel):
    """Summary of a conversation generated by LLM or custom summarizer."""

    session_id: str = Field(description="Session identifier")
    summary: str = Field(description="Generated summary text")
    message_count: int = Field(description="Number of messages summarized")
    time_range: tuple[datetime, datetime] | None = Field(
        default=None, description="Time range of messages (first, last)"
    )
    key_entities: list[str] = Field(
        default_factory=list, description="Key entities mentioned in the conversation"
    )
    key_topics: list[str] = Field(default_factory=list, description="Key topics discussed")
    generated_at: datetime = Field(
        default_factory=datetime.utcnow, description="When summary was generated"
    )


class ShortTermMemory(BaseMemory[Message]):
    """
    Short-term memory stores conversation history and experiences.

    Provides:
    - Thread-based organization of messages
    - Message embeddings for semantic retrieval
    - Entity linking from conversations
    - Session-based conversation management
    """

    def __init__(
        self,
        client: "Neo4jClient",
        embedder: "Embedder | None" = None,
        extractor: "EntityExtractor | None" = None,
        *,
        multi_tenant: bool = False,
    ):
        """Initialize short-term memory."""
        super().__init__(client, embedder, extractor)
        self._multi_tenant = multi_tenant

    def _enforce_multi_tenant(self, user_identifier: str | None) -> None:
        """Raise if multi-tenant is on and ``user_identifier`` is missing."""
        if self._multi_tenant and user_identifier is None:
            raise ValueError(
                "MemorySettings.memory.multi_tenant=True but no "
                "user_identifier was supplied. Pass user_identifier= to "
                "scope this write."
            )

    async def add(self, content: str, **kwargs: Any) -> Message:
        """Add content as a message."""
        session_id = kwargs.get("session_id", "default")
        role = kwargs.get("role", MessageRole.USER)
        return await self.add_message(session_id, role, content, **kwargs)

    async def add_messages_batch(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        batch_size: int = 100,
        generate_embeddings: bool = True,
        extract_entities: bool = False,
        extract_relations: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
        on_batch_complete: Callable[[int, list[Message]], None] | None = None,
    ) -> list[Message]:
        """
        Bulk load messages with transaction batching for better performance.

        This is significantly faster than calling add_message() repeatedly,
        especially for large datasets like podcast transcripts.

        Messages are automatically linked with NEXT_MESSAGE relationships to
        maintain sequential order, and the first message gets a FIRST_MESSAGE
        relationship from the conversation.

        Args:
            session_id: Session identifier
            messages: List of message dicts with 'role' and 'content' keys.
                     Optional keys: 'metadata', 'timestamp' (ISO format string)
            batch_size: Number of messages per transaction batch
            generate_embeddings: Whether to generate embeddings for messages.
                                Can be set to False and called separately with
                                generate_embeddings_batch() for deferred processing.
            extract_entities: Whether to extract entities (disabled by default for
                            performance - can use extract_entities_from_session() later)
            extract_relations: Whether to extract and store relations between entities
                              (only applies when extract_entities=True)
            on_progress: Callback for progress updates (completed_count, total_count)
            on_batch_complete: Callback after each batch completes (batch_num, batch_messages)

        Returns:
            List of created Message objects
        """
        if not messages:
            return []

        # Ensure conversation exists
        conv_id = await self._ensure_conversation(session_id, None)

        total = len(messages)
        all_created: list[Message] = []

        # Get existing last message before any inserts (for linking)
        existing_last_id = await self._get_last_message_id(conv_id)
        previous_last_id: str | None = existing_last_id

        # Process in batches
        for batch_num, i in enumerate(range(0, total, batch_size)):
            batch = messages[i : i + batch_size]
            batch_messages: list[Message] = []

            # Prepare batch data
            batch_data = []
            contents_for_embedding = []

            for msg_dict in batch:
                role = msg_dict.get("role", "user")
                if isinstance(role, str):
                    role = MessageRole(role.lower())

                msg_id = str(uuid4())
                content = msg_dict["content"]
                metadata = msg_dict.get("metadata", {})
                timestamp = msg_dict.get("timestamp")

                batch_data.append(
                    {
                        "id": msg_id,
                        "role": role.value if isinstance(role, MessageRole) else role,
                        "content": content,
                        "embedding": None,  # Will be set after batch embedding
                        "timestamp": timestamp,
                        "metadata": _serialize_metadata(metadata),
                    }
                )
                contents_for_embedding.append(content)

                batch_messages.append(
                    Message(
                        id=UUID(msg_id),
                        role=role if isinstance(role, MessageRole) else MessageRole(role),
                        content=content,
                        conversation_id=conv_id,
                        metadata=metadata,
                    )
                )

            # Generate embeddings in batch if enabled
            if generate_embeddings and self._embedder is not None:
                embeddings = await self._embedder.embed_batch(contents_for_embedding)
                for j, emb in enumerate(embeddings):
                    batch_data[j]["embedding"] = emb
                    batch_messages[j].embedding = emb

            # Insert batch into database
            await self._client.execute_write(
                queries.CREATE_MESSAGES_BATCH,
                {
                    "conversation_id": str(conv_id),
                    "messages": batch_data,
                },
            )

            # Create message links for this batch
            msg_ids = [bd["id"] for bd in batch_data]
            is_first_batch = batch_num == 0 and existing_last_id is None
            await self._create_message_links(
                conv_id,
                msg_ids,
                previous_last_id,
                create_first_message=is_first_batch,
            )

            # Update previous_last_id for next batch
            previous_last_id = msg_ids[-1] if msg_ids else previous_last_id

            all_created.extend(batch_messages)

            # Report progress
            completed = min(i + batch_size, total)
            if on_progress:
                on_progress(completed, total)
            if on_batch_complete:
                on_batch_complete(batch_num + 1, batch_messages)

        # Extract entities if enabled (done separately for performance)
        if extract_entities and self._extractor is not None:
            for msg in all_created:
                await self._extract_and_link_entities(msg, extract_relations=extract_relations)

        return all_created

    async def generate_embeddings_batch(
        self,
        session_id: str,
        *,
        batch_size: int = 100,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """
        Generate embeddings for messages that don't have them.

        Useful after bulk loading with generate_embeddings=False.

        Args:
            session_id: Session to process
            batch_size: Messages to process per batch
            on_progress: Progress callback (processed_count, total_count)

        Returns:
            Number of messages that had embeddings generated
        """
        if self._embedder is None:
            return 0

        # Get messages without embeddings
        results = await self._client.execute_read(
            queries.GET_MESSAGES_WITHOUT_EMBEDDINGS,
            {"session_id": session_id},
        )

        if not results:
            return 0

        total = len(results)
        processed = 0

        # Process in batches
        for i in range(0, total, batch_size):
            batch = results[i : i + batch_size]

            # Extract contents and IDs
            contents = [row["content"] for row in batch]
            msg_ids = [row["id"] for row in batch]

            # Batch generate embeddings
            embeddings = await self._embedder.embed_batch(contents)

            # Update messages with embeddings
            for msg_id, embedding in zip(msg_ids, embeddings):
                await self._client.execute_write(
                    queries.UPDATE_MESSAGE_EMBEDDING,
                    {"id": msg_id, "embedding": embedding},
                )

            processed += len(batch)
            if on_progress:
                on_progress(processed, total)

        return processed

    async def add_message(
        self,
        session_id: str,
        role: MessageRole | str,
        content: str,
        *,
        conversation_id: UUID | str | None = None,
        extract_entities: bool = True,
        extract_relations: bool = True,
        generate_embedding: bool = True,
        metadata: dict[str, Any] | None = None,
        extraction_mode: Literal["auto", "skip", "explicit"] = "auto",
        explicit_mentions: "list[Any] | None" = None,
        user_identifier: str | None = None,
    ) -> Message:
        """
        Add a message to a conversation.

        Args:
            session_id: Session identifier
            role: Message role (user, assistant, system, tool)
            content: Message content
            conversation_id: Optional specific conversation ID
            extract_entities: Whether to extract entities from content. Has
                no effect when ``extraction_mode != 'auto'``.
            extract_relations: Whether to extract and store relations between entities
            generate_embedding: Whether to generate embedding
            metadata: Optional metadata
            extraction_mode: How to populate MENTIONS edges for this message.
                ``'auto'`` (default): run the configured extractor pipeline.
                ``'skip'``: do not run extraction, do not write MENTIONS edges.
                ``'explicit'``: do not run extraction, write MENTIONS edges
                only for the entities supplied via ``explicit_mentions``.
            explicit_mentions: When ``extraction_mode='explicit'``, a list
                of :class:`~neo4j_agent_memory.schema.models.EntityRef`
                describing the entities to MERGE on and link.
            user_identifier: When provided, scopes the conversation to a
                :User node via ``(:User)-[:HAS_CONVERSATION]->(:Conversation)``.
                Required when ``MemorySettings.multi_tenant=True``.

        Returns:
            The created message
        """
        # Multi-tenant guardrail.
        self._enforce_multi_tenant(user_identifier)

        # Normalize role
        if isinstance(role, str):
            role = MessageRole(role.lower())

        # Get or create conversation
        conv_id = await self._ensure_conversation(
            session_id, conversation_id, user_identifier=user_identifier
        )

        # Generate embedding if enabled
        embedding = None
        if generate_embedding and self._embedder is not None:
            embedding = await self._embedder.embed(content)

        # Create message
        message = Message(
            id=uuid4(),
            role=role,
            content=content,
            conversation_id=conv_id,
            embedding=embedding,
            metadata=metadata or {},
        )

        # Store message
        await self._client.execute_write(
            queries.CREATE_MESSAGE,
            {
                "conversation_id": str(conv_id),
                "id": str(message.id),
                "role": message.role.value,
                "content": message.content,
                "embedding": message.embedding,
                "metadata": _serialize_metadata(message.metadata),
            },
        )

        # Extract and link entities according to the requested mode.
        if extraction_mode == "skip":
            pass
        elif extraction_mode == "explicit":
            await self._link_explicit_mentions(message, explicit_mentions or [])
        else:
            # 'auto' — preserve historical behavior.
            if extract_entities and self._extractor is not None:
                await self._extract_and_link_entities(message, extract_relations=extract_relations)

        return message

    async def _link_explicit_mentions(
        self,
        message: Message,
        mentions: list,
    ) -> None:
        """Write a ``MENTIONS`` edge from ``message`` to each supplied EntityRef.

        Skips extraction entirely. Used by callers who already know which
        entities a message touches (e.g. a tool's output is a list of
        Client objects).
        """
        if not mentions:
            return

        for ref in mentions:
            # Resolve to or create the target entity. Identity precedence:
            # id > name+type > name.
            if getattr(ref, "id", None):
                rows = await self._client.execute_read(
                    "MATCH (e:Entity {id: $id}) RETURN e.id AS id LIMIT 1",
                    {"id": ref.id},
                )
                if not rows:
                    continue
                entity_id = rows[0]["id"]
            elif getattr(ref, "type", None):
                rows = await self._client.execute_write(
                    """
                    MERGE (e:Entity {name: $name, type: $type})
                    ON CREATE SET e.id = coalesce(e.id, $name + ':' + $type),
                                  e.created_at = datetime()
                    RETURN e.id AS id
                    """,
                    {"name": ref.name, "type": ref.type},
                )
                entity_id = rows[0]["id"]
            else:
                rows = await self._client.execute_write(
                    """
                    MERGE (e:Entity {name: $name})
                    ON CREATE SET e.id = coalesce(e.id, $name),
                                  e.created_at = datetime()
                    RETURN e.id AS id
                    """,
                    {"name": ref.name},
                )
                entity_id = rows[0]["id"]

            await self._client.execute_write(
                queries.LINK_MESSAGE_TO_ENTITY,
                {
                    "message_id": str(message.id),
                    "entity_id": entity_id,
                    "confidence": 1.0,
                    "start_pos": None,
                    "end_pos": None,
                },
            )

    async def get_conversation(
        self,
        session_id: str,
        *,
        conversation_id: UUID | str | None = None,
        limit: int | None = None,
        since: datetime | None = None,
    ) -> Conversation:
        """
        Get conversation history for a session.

        Args:
            session_id: Session identifier
            conversation_id: Optional specific conversation ID
            limit: Maximum number of messages
            since: Only get messages after this time

        Returns:
            Conversation with messages
        """
        # Get conversation
        if conversation_id:
            conv_id = str(conversation_id) if isinstance(conversation_id, UUID) else conversation_id
            results = await self._client.execute_read(queries.GET_CONVERSATION, {"id": conv_id})
        else:
            results = await self._client.execute_read(
                queries.GET_CONVERSATION_BY_SESSION, {"session_id": session_id}
            )

        if not results:
            # Return empty conversation
            return Conversation(session_id=session_id)

        conv_data = dict(results[0]["c"])

        # Get messages
        msg_results = await self._client.execute_read(
            queries.GET_CONVERSATION_MESSAGES,
            {"conversation_id": conv_data["id"], "limit": limit or 1000},
        )

        messages = []
        for row in msg_results:
            msg_data = dict(row["m"])
            msg = Message(
                id=UUID(msg_data["id"]),
                role=MessageRole(msg_data["role"]),
                content=msg_data["content"],
                embedding=msg_data.get("embedding"),
                conversation_id=UUID(conv_data["id"]),
                created_at=_to_python_datetime(msg_data.get("timestamp")),
                metadata=_deserialize_metadata(msg_data.get("metadata")),
            )
            if since is None or msg.created_at >= since:
                messages.append(msg)

        return Conversation(
            id=UUID(conv_data["id"]),
            session_id=conv_data["session_id"],
            title=conv_data.get("title"),
            messages=messages,
            created_at=_to_python_datetime(conv_data.get("created_at")),
            updated_at=_to_python_datetime(conv_data.get("updated_at"))
            if conv_data.get("updated_at")
            else None,
        )

    async def search(self, query: str, **kwargs: Any) -> list[Message]:
        """Search for messages."""
        return await self.search_messages(query, **kwargs)

    async def search_messages(
        self,
        query: str,
        *,
        session_id: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[Message]:
        """
        Semantic search across messages.

        Args:
            query: Search query
            session_id: Optional filter by session
            limit: Maximum results
            threshold: Minimum similarity threshold
            metadata_filters: Optional metadata-based filters. Supports:
                - Simple equality: {"speaker": "Brian Chesky"}
                - Comparison operators: {"turn_index": {"$gt": 5}}
                - List membership: {"source": {"$in": ["podcast", "interview"]}}
                - Existence check: {"timestamp": {"$exists": True}}
                - String operations: {"speaker": {"$contains": "Brian"}}

        Returns:
            List of matching messages
        """
        if self._embedder is None:
            return []

        query_embedding = await self._embedder.embed(query)

        # Build metadata filter clause if provided
        # Use JSON string matching which doesn't require APOC
        metadata_clause, metadata_params = _build_metadata_filter_clause_json(
            metadata_filters or {}, metadata_prop="m.metadata"
        )

        # Build the query with optional metadata filtering
        if metadata_clause:
            # Use a modified query that includes metadata filtering
            # Metadata is stored as JSON string - we use CONTAINS for filtering
            cypher_query = queries.build_metadata_search_query(metadata_clause)
            params = {
                "embedding": query_embedding,
                "limit": limit,
                "threshold": threshold,
                **metadata_params,
            }
        else:
            cypher_query = queries.SEARCH_MESSAGES_BY_EMBEDDING
            params = {
                "embedding": query_embedding,
                "limit": limit,
                "threshold": threshold,
            }

        results = await self._client.execute_read(cypher_query, params)

        messages = []
        for row in results:
            msg_data = dict(row["m"])
            msg = Message(
                id=UUID(msg_data["id"]),
                role=MessageRole(msg_data["role"]),
                content=msg_data["content"],
                embedding=msg_data.get("embedding"),
                created_at=_to_python_datetime(msg_data.get("timestamp")),
                metadata={
                    **_deserialize_metadata(msg_data.get("metadata")),
                    "similarity": row["score"],
                },
            )
            messages.append(msg)

        return messages

    async def get_context(self, query: str, **kwargs: Any) -> str:
        """
        Get conversation context for LLM prompts.

        Args:
            query: Query to find relevant context
            session_id: Optional session filter
            max_messages: Maximum messages to include
            include_related: Whether to include related entities

        Returns:
            Formatted context string
        """
        session_id = kwargs.get("session_id")
        max_messages = kwargs.get("max_messages", 10)

        parts = []

        # Get recent conversation if session_id provided
        if session_id:
            conv = await self.get_conversation(session_id, limit=max_messages)
            if conv.messages:
                parts.append("### Recent Conversation")
                for msg in conv.messages[-max_messages:]:
                    parts.append(f"**{msg.role.value}**: {msg.content}")

        # Search for relevant messages
        if self._embedder is not None:
            relevant = await self.search_messages(query, limit=5)
            if relevant:
                parts.append("\n### Relevant Past Messages")
                for msg in relevant:
                    score = msg.metadata.get("similarity", 0)
                    parts.append(f"- [{msg.role.value}] {msg.content} (relevance: {score:.2f})")

        return "\n".join(parts)

    async def clear_session(self, session_id: str) -> None:
        """Clear all data for a session."""
        await self._client.execute_write(queries.DELETE_SESSION_DATA, {"session_id": session_id})

    async def migrate_message_links(self) -> dict[str, int]:
        """
        Migrate existing messages to use NEXT_MESSAGE and FIRST_MESSAGE relationships.

        This is a one-time migration for existing data created before sequential
        message linking was implemented. New messages automatically have these
        relationships created.

        The migration:
        - Creates FIRST_MESSAGE relationship from each Conversation to its first message
        - Creates NEXT_MESSAGE relationships between messages based on timestamp order

        Returns:
            Dictionary mapping conversation_id to number of messages linked

        Example:
            # Run migration for existing data
            migrated = await memory.short_term.migrate_message_links()
            print(f"Migrated {len(migrated)} conversations")
            for conv_id, count in migrated.items():
                print(f"  {conv_id}: {count} messages linked")
        """
        results = await self._client.execute_write(queries.MIGRATE_MESSAGE_LINKS)
        return {row["conversation_id"]: row["messages_linked"] for row in results}

    async def list_sessions(
        self,
        *,
        prefix: str | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: Literal["created_at", "updated_at", "message_count"] = "updated_at",
        order_dir: Literal["asc", "desc"] = "desc",
    ) -> list[SessionInfo]:
        """
        List sessions with metadata.

        Args:
            prefix: Filter sessions by ID prefix (e.g., "lenny-podcast-" to match
                   all podcast sessions)
            limit: Maximum sessions to return
            offset: Number of sessions to skip (for pagination)
            order_by: Field to order by ('created_at', 'updated_at', or 'message_count')
            order_dir: Sort direction ('asc' or 'desc')

        Returns:
            List of SessionInfo objects with session details
        """
        results = await self._client.execute_read(
            queries.LIST_SESSIONS,
            {
                "prefix": prefix,
                "limit": limit,
                "offset": offset,
                "order_by": order_by,
                "order_dir": order_dir,
            },
        )

        sessions = []
        for row in results:
            session = SessionInfo(
                session_id=row["session_id"],
                title=row.get("title"),
                created_at=_to_python_datetime(row.get("created_at")),
                updated_at=_to_python_datetime(row.get("updated_at"))
                if row.get("updated_at")
                else None,
                message_count=row.get("message_count", 0),
                first_message_preview=row.get("first_message_preview"),
                last_message_preview=row.get("last_message_preview"),
            )
            sessions.append(session)

        return sessions

    async def delete_message(
        self,
        message_id: UUID | str,
        *,
        cascade: bool = True,
    ) -> bool:
        """
        Delete a message by ID.

        Args:
            message_id: The message UUID to delete
            cascade: If True, also delete MENTIONS relationships to entities.
                    The entities themselves are not deleted as they may be
                    referenced by other messages.

        Returns:
            True if message was deleted, False if not found
        """
        if isinstance(message_id, str):
            try:
                message_id = UUID(message_id)
            except ValueError:
                return False

        query = queries.DELETE_MESSAGE if cascade else queries.DELETE_MESSAGE_NO_CASCADE
        results = await self._client.execute_write(query, {"id": str(message_id)})

        if results and results[0].get("deleted"):
            return True
        return False

    async def extract_entities_from_session(
        self,
        session_id: str,
        *,
        batch_size: int = 50,
        skip_existing: bool = True,
        extract_relations: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, int]:
        """
        Extract entities and relations from all messages in a session.

        This is useful for batch processing messages that were loaded without
        entity extraction (e.g., using extract_entities=False for performance).

        Args:
            session_id: Session to process
            batch_size: Messages to process per batch
            skip_existing: Skip messages that already have entity links (MENTIONS relationships)
            extract_relations: Whether to also extract and store relations between entities
            on_progress: Progress callback (processed_count, total_count)

        Returns:
            Stats dict with 'messages_processed', 'entities_extracted', and 'relations_extracted' counts
        """
        if self._extractor is None:
            return {"messages_processed": 0, "entities_extracted": 0, "relations_extracted": 0}

        # Get messages to process
        if skip_existing:
            query = queries.GET_MESSAGES_FOR_ENTITY_EXTRACTION
        else:
            query = queries.GET_ALL_MESSAGES_FOR_SESSION

        results = await self._client.execute_read(query, {"session_id": session_id})

        if not results:
            return {"messages_processed": 0, "entities_extracted": 0, "relations_extracted": 0}

        total = len(results)
        processed = 0
        entities_extracted = 0
        relations_extracted = 0

        # Process in batches
        for i in range(0, total, batch_size):
            batch = results[i : i + batch_size]

            for row in batch:
                message_id = row["id"]
                content = row["content"]

                # Extract entities
                extraction_result = await self._extractor.extract(content)

                # Filter out invalid entities (stopwords, numbers, etc.)
                extraction_result = extraction_result.filter_invalid_entities()

                # Track entity name to ID mapping for relation linking
                entity_name_to_id: dict[str, str] = {}

                for entity in extraction_result.entities:
                    # Create or get entity with dynamic labels for type/subtype
                    entity_id = str(uuid4())
                    entity_subtype = getattr(entity, "subtype", None)
                    create_query = build_create_entity_query(entity.type, entity_subtype)
                    await self._client.execute_write(
                        create_query,
                        {
                            "id": entity_id,
                            "name": entity.name,
                            "type": entity.type,
                            "subtype": entity_subtype,
                            "canonical_name": entity.name,
                            "description": None,
                            "embedding": None,
                            "confidence": entity.confidence,
                            "metadata": None,
                            "location": None,  # Required for LOCATION entities
                        },
                    )

                    # Store mapping for relation linking
                    entity_name_to_id[entity.name.lower().strip()] = entity_id

                    # Link message to entity
                    await self._client.execute_write(
                        queries.LINK_MESSAGE_TO_ENTITY,
                        {
                            "message_id": message_id,
                            "entity_id": entity_id,
                            "confidence": entity.confidence,
                            "start_pos": entity.start_pos,
                            "end_pos": entity.end_pos,
                        },
                    )
                    entities_extracted += 1

                # Store extracted relations
                if extract_relations and extraction_result.relations:
                    stored = await self._store_relations(
                        extraction_result.relations, entity_name_to_id
                    )
                    relations_extracted += stored

                processed += 1

            # Report progress after each batch
            if on_progress:
                on_progress(processed, total)

        return {
            "messages_processed": processed,
            "entities_extracted": entities_extracted,
            "relations_extracted": relations_extracted,
        }

    async def _ensure_conversation(
        self,
        session_id: str,
        conversation_id: UUID | str | None = None,
        user_identifier: str | None = None,
    ) -> UUID:
        """Ensure a conversation exists and return its ID.

        When ``user_identifier`` is provided and the conversation is being
        created (not just retrieved), the conversation is linked to the
        :User node via ``(:User)-[:HAS_CONVERSATION]->(:Conversation)``,
        and ``user_identifier`` is denormalized onto the Conversation
        node for fast filtering.
        """
        if conversation_id:
            return UUID(str(conversation_id))

        # Check for existing conversation
        results = await self._client.execute_read(
            queries.GET_CONVERSATION_BY_SESSION, {"session_id": session_id}
        )

        if results:
            existing_id = UUID(results[0]["c"]["id"])
            # If the caller now supplies a user_identifier and it wasn't
            # already attached, idempotently wire it up. Cheap and avoids
            # surprise when an existing conversation gets its first
            # user-scoped write.
            if user_identifier is not None:
                await self._link_user_to_conversation(user_identifier, existing_id)
            return existing_id

        # Create new conversation
        new_id = uuid4()
        await self._client.execute_write(
            queries.CREATE_CONVERSATION,
            {
                "id": str(new_id),
                "session_id": session_id,
                "title": None,
            },
        )
        if user_identifier is not None:
            await self._link_user_to_conversation(user_identifier, new_id)
        return new_id

    async def _link_user_to_conversation(self, user_identifier: str, conversation_id: UUID) -> None:
        """Idempotently write ``(:User)-[:HAS_CONVERSATION]->(:Conversation)``.

        Also denormalizes ``user_identifier`` onto the Conversation node
        so per-user queries are indexable via the (existing)
        ``conversation_session_idx`` plus a property-equality filter,
        without an extra MATCH on :User.
        """
        await self._client.execute_write(
            """
            MERGE (u:User {identifier: $user_identifier})
            ON CREATE SET u.id = $user_identifier, u.created_at = datetime()
            WITH u
            MATCH (c:Conversation {id: $conversation_id})
            MERGE (u)-[:HAS_CONVERSATION]->(c)
            SET c.user_identifier = $user_identifier
            """,
            {
                "user_identifier": user_identifier,
                "conversation_id": str(conversation_id),
            },
        )

    async def _get_last_message_id(self, conversation_id: UUID) -> str | None:
        """Get the ID of the last message in a conversation (one without outgoing NEXT_MESSAGE)."""
        results = await self._client.execute_read(
            queries.GET_LAST_MESSAGE,
            {"conversation_id": str(conversation_id)},
        )
        if not results:
            return None
        return results[0]["m"]["id"]

    async def _create_message_links(
        self,
        conversation_id: UUID,
        message_ids: list[str],
        previous_last_id: str | None,
        create_first_message: bool,
    ) -> None:
        """Create NEXT_MESSAGE links for a batch of messages."""
        if not message_ids:
            return

        await self._client.execute_write(
            queries.CREATE_MESSAGE_LINKS,
            {
                "conversation_id": str(conversation_id),
                "message_ids": message_ids,
                "previous_last_id": previous_last_id,
                "create_first_message": create_first_message,
            },
        )

    async def _extract_and_link_entities(
        self, message: Message, *, extract_relations: bool = True
    ) -> None:
        """Extract entities from message and link them.

        Args:
            message: The message to extract entities from
            extract_relations: Whether to also extract and store relations between entities
        """
        if self._extractor is None:
            return

        result = await self._extractor.extract(message.content)

        # Filter out invalid entities (stopwords, numbers, etc.)
        result = result.filter_invalid_entities()

        # Track entity name to ID mapping for relation linking
        entity_name_to_id: dict[str, str] = {}

        for entity in result.entities:
            # Create or get entity with dynamic labels for type/subtype
            entity_id = str(uuid4())
            entity_subtype = getattr(entity, "subtype", None)
            create_query = build_create_entity_query(entity.type, entity_subtype)
            # Stage attribution: which extractor produced this entity?
            # Stored in metadata so it's queryable via apoc / JSON helpers
            # without changing the constraint or index footprint.
            extracted_by = getattr(entity, "extractor", None)
            metadata_payload = (
                json.dumps({"extracted_by": extracted_by}) if extracted_by is not None else None
            )
            await self._client.execute_write(
                create_query,
                {
                    "id": entity_id,
                    "name": entity.name,
                    "type": entity.type,
                    "subtype": entity_subtype,
                    "canonical_name": entity.name,
                    "description": None,
                    "embedding": None,
                    "confidence": entity.confidence,
                    "metadata": metadata_payload,
                    "location": None,  # Required for LOCATION entities
                },
            )

            # Store mapping for relation linking
            entity_name_to_id[entity.name.lower().strip()] = entity_id

            # Link message to entity
            await self._client.execute_write(
                queries.LINK_MESSAGE_TO_ENTITY,
                {
                    "message_id": str(message.id),
                    "entity_id": entity_id,
                    "confidence": entity.confidence,
                    "start_pos": entity.start_pos,
                    "end_pos": entity.end_pos,
                },
            )

        # Store extracted relations
        if extract_relations and result.relations:
            await self._store_relations(result.relations, entity_name_to_id)

    async def _store_relations(
        self,
        relations: list,
        entity_name_to_id: dict[str, str],
    ) -> int:
        """Store extracted relations as RELATED_TO relationships between entities.

        This method first tries to use the local entity_name_to_id mapping
        (for entities extracted from the same message), then falls back to
        looking up entities by name in the database (for cross-message relations).

        Args:
            relations: List of ExtractedRelation objects from the extractor
            entity_name_to_id: Mapping of lowercase entity names to their IDs

        Returns:
            Number of relations successfully stored
        """
        if not relations:
            return 0

        stored_count = 0
        for relation in relations:
            source_name = relation.source.lower().strip()
            target_name = relation.target.lower().strip()

            # First try the local mapping (entities from same message)
            source_id = entity_name_to_id.get(source_name)
            target_id = entity_name_to_id.get(target_name)

            if source_id and target_id:
                # Both entities found locally, use ID-based query
                await self._client.execute_write(
                    queries.CREATE_ENTITY_RELATION_BY_ID,
                    {
                        "source_id": source_id,
                        "target_id": target_id,
                        "relation_type": relation.relation_type,
                        "confidence": relation.confidence,
                    },
                )
                stored_count += 1
            else:
                # Try name-based lookup for cross-message relations
                result = await self._client.execute_write(
                    queries.CREATE_ENTITY_RELATION_BY_NAME,
                    {
                        "source_name": relation.source,
                        "target_name": relation.target,
                        "relation_type": relation.relation_type,
                        "confidence": relation.confidence,
                    },
                )
                if result:
                    stored_count += 1

        return stored_count

    async def get_conversation_summary(
        self,
        session_id: str,
        *,
        max_tokens: int = 500,
        include_entities: bool = True,
        summarizer: Callable[[str], str] | None = None,
    ) -> ConversationSummary:
        """
        Generate a summary of a conversation.

        This method creates a comprehensive summary of the conversation including
        key points discussed, entities mentioned, and topics covered.

        Args:
            session_id: Session to summarize
            max_tokens: Approximate max length of summary (used as hint for summarizer)
            include_entities: Whether to include key entities in the result
            summarizer: Custom summarizer function. If not provided, returns a
                basic summary built from message content. The function should
                accept a conversation transcript string and return a summary string.

                Example with OpenAI:
                    async def openai_summarize(text: str) -> str:
                        response = await client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": "Summarize this conversation concisely."},
                                {"role": "user", "content": text}
                            ]
                        )
                        return response.choices[0].message.content

        Returns:
            ConversationSummary with summary text and metadata

        Example:
            # Basic summary (no LLM)
            summary = await memory.short_term.get_conversation_summary("session-123")

            # With custom OpenAI summarizer
            async def my_summarizer(text):
                # Your LLM call here
                return await openai_client.summarize(text)

            summary = await memory.short_term.get_conversation_summary(
                "session-123",
                summarizer=my_summarizer
            )
        """
        # Get conversation
        conv = await self.get_conversation(session_id)

        if not conv.messages:
            return ConversationSummary(
                session_id=session_id,
                summary="No messages in this conversation.",
                message_count=0,
            )

        # Build transcript for summarization
        transcript_lines = []
        for msg in conv.messages:
            transcript_lines.append(f"{msg.role.value.upper()}: {msg.content}")
        transcript = "\n".join(transcript_lines)

        # Get time range
        first_time = conv.messages[0].created_at
        last_time = conv.messages[-1].created_at
        time_range = (first_time, last_time)

        # Get key entities if requested
        key_entities: list[str] = []
        if include_entities:
            entity_results = await self._client.execute_read(
                queries.GET_SUMMARY_ENTITIES, {"session_id": session_id}
            )
            key_entities = [row["name"] for row in entity_results]

        # Generate summary
        if summarizer is not None:
            # Use custom summarizer (may be sync or async)
            import asyncio
            import inspect

            if inspect.iscoroutinefunction(summarizer):
                summary_text = await summarizer(transcript)
            else:
                # Run sync function in executor to avoid blocking
                loop = asyncio.get_event_loop()
                summary_text = await loop.run_in_executor(None, summarizer, transcript)
        else:
            # Build basic summary without LLM
            summary_text = self._build_basic_summary(conv.messages, max_tokens)

        # Extract key topics (simple keyword extraction from summary)
        key_topics = self._extract_key_topics(summary_text)

        return ConversationSummary(
            session_id=session_id,
            summary=summary_text,
            message_count=len(conv.messages),
            time_range=time_range,
            key_entities=key_entities,
            key_topics=key_topics,
        )

    def _build_basic_summary(self, messages: list[Message], max_tokens: int) -> str:
        """Build a basic summary without using an LLM."""
        if not messages:
            return "Empty conversation."

        # Count messages by role
        role_counts = {}
        for msg in messages:
            role = msg.role.value
            role_counts[role] = role_counts.get(role, 0) + 1

        # Get first user message as topic indicator
        first_user_msg = None
        for msg in messages:
            if msg.role == MessageRole.USER:
                first_user_msg = msg.content[:200]
                break

        # Build summary
        parts = []
        parts.append(f"Conversation with {len(messages)} messages")

        role_str = ", ".join(f"{count} {role}" for role, count in role_counts.items())
        parts.append(f"({role_str}).")

        if first_user_msg:
            parts.append(
                f'Started with: "{first_user_msg}..."'
                if len(first_user_msg) == 200
                else f'Started with: "{first_user_msg}"'
            )

        # Add time info
        if messages[0].created_at and messages[-1].created_at:
            duration = messages[-1].created_at - messages[0].created_at
            if duration.total_seconds() > 0:
                if duration.days > 0:
                    parts.append(f"Duration: {duration.days} days.")
                elif duration.seconds > 3600:
                    parts.append(f"Duration: {duration.seconds // 3600} hours.")
                elif duration.seconds > 60:
                    parts.append(f"Duration: {duration.seconds // 60} minutes.")

        return " ".join(parts)

    def _extract_key_topics(self, text: str) -> list[str]:
        """Extract key topics from summary text (simple keyword extraction)."""
        # Simple extraction - could be enhanced with NLP
        import re

        # Common stop words to filter out
        stop_words = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "must",
            "shall",
            "can",
            "need",
            "dare",
            "ought",
            "used",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "between",
            "under",
            "again",
            "further",
            "then",
            "once",
            "here",
            "there",
            "when",
            "where",
            "why",
            "how",
            "all",
            "each",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "nor",
            "not",
            "only",
            "own",
            "same",
            "so",
            "than",
            "too",
            "very",
            "just",
            "and",
            "but",
            "if",
            "or",
            "because",
            "until",
            "while",
            "although",
            "this",
            "that",
            "these",
            "those",
            "conversation",
            "messages",
            "user",
            "assistant",
            "started",
            "duration",
        }

        # Extract words
        words = re.findall(r"\b[a-zA-Z]{4,}\b", text.lower())

        # Count frequency
        word_freq = {}
        for word in words:
            if word not in stop_words:
                word_freq[word] = word_freq.get(word, 0) + 1

        # Get top topics
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [word for word, _ in sorted_words[:5]]
