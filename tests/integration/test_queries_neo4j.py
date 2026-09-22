"""Integration tests for Cypher queries against Neo4j.

These tests run actual Cypher queries against a Neo4j database to verify
they execute correctly and return expected results.

Requires either:
- testcontainers[neo4j] installed with Docker running, OR
- NEO4J_URI environment variable set to a running Neo4j instance
"""

from uuid import uuid4

import pytest

from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.graph.query_builder import build_create_entity_query


@pytest.mark.integration
class TestConversationQueries:
    """Test conversation-related queries against Neo4j."""

    @pytest.mark.asyncio
    async def test_create_and_get_conversation(self, neo4j_client):
        """Test creating and retrieving a conversation."""
        conv_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"

        # Create conversation
        await neo4j_client.execute_write(
            queries.CREATE_CONVERSATION,
            {"id": conv_id, "session_id": session_id, "title": "Test Conversation"},
        )

        # Get conversation by ID
        results = await neo4j_client.execute_read(queries.GET_CONVERSATION, {"id": conv_id})
        assert len(results) == 1
        assert results[0]["c"]["id"] == conv_id
        assert results[0]["c"]["session_id"] == session_id
        assert results[0]["c"]["title"] == "Test Conversation"

    @pytest.mark.asyncio
    async def test_get_conversation_by_session(self, neo4j_client):
        """Test retrieving conversation by session ID."""
        conv_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"

        # Create conversation
        await neo4j_client.execute_write(
            queries.CREATE_CONVERSATION,
            {"id": conv_id, "session_id": session_id, "title": None},
        )

        # Get by session
        results = await neo4j_client.execute_read(
            queries.GET_CONVERSATION_BY_SESSION, {"session_id": session_id}
        )
        assert len(results) == 1
        assert results[0]["c"]["id"] == conv_id


@pytest.mark.integration
class TestMessageQueries:
    """Test message-related queries against Neo4j."""

    @pytest.mark.asyncio
    async def test_create_message(self, neo4j_client):
        """Test creating a message linked to a conversation."""
        conv_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"
        msg_id = f"test-{uuid4()}"

        # Create conversation first
        await neo4j_client.execute_write(
            queries.CREATE_CONVERSATION,
            {"id": conv_id, "session_id": session_id, "title": None},
        )

        # Create message
        await neo4j_client.execute_write(
            queries.CREATE_MESSAGE,
            {
                "conversation_id": conv_id,
                "id": msg_id,
                "role": "user",
                "content": "Hello, world!",
                "embedding": None,
                "metadata": None,
            },
        )

        # Get conversation messages
        results = await neo4j_client.execute_read(
            queries.GET_CONVERSATION_MESSAGES,
            {"conversation_id": conv_id, "limit": 10},
        )
        assert len(results) == 1
        assert results[0]["m"]["id"] == msg_id
        assert results[0]["m"]["content"] == "Hello, world!"
        assert results[0]["m"]["role"] == "user"

    @pytest.mark.asyncio
    async def test_delete_message(self, neo4j_client):
        """Test deleting a message.

        Note: We need to use DETACH DELETE because CREATE_MESSAGE creates
        relationships (HAS_MESSAGE, FIRST_MESSAGE/NEXT_MESSAGE) that must
        also be removed.
        """
        conv_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"
        msg_id = f"test-{uuid4()}"

        # Setup
        await neo4j_client.execute_write(
            queries.CREATE_CONVERSATION,
            {"id": conv_id, "session_id": session_id, "title": None},
        )
        await neo4j_client.execute_write(
            queries.CREATE_MESSAGE,
            {
                "conversation_id": conv_id,
                "id": msg_id,
                "role": "user",
                "content": "To be deleted",
                "embedding": None,
                "metadata": None,
            },
        )

        # Delete message using DETACH DELETE to handle all relationships
        # The DELETE_MESSAGE query handles MENTIONS but not conversation relationships,
        # so we use a direct DETACH DELETE for this test
        results = await neo4j_client.execute_write(
            "MATCH (m:Message {id: $id}) DETACH DELETE m RETURN count(m) > 0 AS deleted",
            {"id": msg_id},
        )
        assert results[0]["deleted"] is True

        # Verify deletion
        results = await neo4j_client.execute_read(
            queries.GET_CONVERSATION_MESSAGES,
            {"conversation_id": conv_id, "limit": 10},
        )
        assert len(results) == 0


@pytest.mark.integration
class TestEntityQueries:
    """Test entity-related queries against Neo4j."""

    @pytest.mark.asyncio
    async def test_create_entity_with_type_label(self, neo4j_client):
        """Test creating an entity with type as additional label."""
        entity_id = f"test-{uuid4()}"

        # Use query builder to get dynamic query with type label
        create_query = build_create_entity_query("PERSON", None)
        await neo4j_client.execute_write(
            create_query,
            {
                "id": entity_id,
                "name": "John Doe",
                "type": "PERSON",
                "subtype": None,
                "canonical_name": "John Doe",
                "description": "A test person",
                "embedding": None,
                "confidence": 0.9,
                "metadata": None,
                "location": None,
            },
        )

        # Verify entity was created with correct labels
        results = await neo4j_client.execute_read(queries.GET_ENTITY, {"id": entity_id})
        assert len(results) == 1
        assert results[0]["e"]["name"] == "John Doe"
        assert results[0]["e"]["type"] == "PERSON"

    @pytest.mark.asyncio
    async def test_create_entity_with_subtype_label(self, neo4j_client):
        """Test creating an entity with both type and subtype labels."""
        entity_id = f"test-{uuid4()}"

        create_query = build_create_entity_query("LOCATION", "CITY")
        await neo4j_client.execute_write(
            create_query,
            {
                "id": entity_id,
                "name": "San Francisco",
                "type": "LOCATION",
                "subtype": "CITY",
                "canonical_name": "San Francisco",
                "description": "A city in California",
                "embedding": None,
                "confidence": 0.95,
                "metadata": None,
                "location": None,
            },
        )

        # Verify entity
        results = await neo4j_client.execute_read(queries.GET_ENTITY, {"id": entity_id})
        assert len(results) == 1
        assert results[0]["e"]["name"] == "San Francisco"
        assert results[0]["e"]["subtype"] == "CITY"

    @pytest.mark.asyncio
    async def test_link_message_to_entity(self, neo4j_client):
        """Test creating MENTIONS relationship between message and entity."""
        conv_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"
        msg_id = f"test-{uuid4()}"
        entity_id = f"test-{uuid4()}"

        # Setup conversation and message
        await neo4j_client.execute_write(
            queries.CREATE_CONVERSATION,
            {"id": conv_id, "session_id": session_id, "title": None},
        )
        await neo4j_client.execute_write(
            queries.CREATE_MESSAGE,
            {
                "conversation_id": conv_id,
                "id": msg_id,
                "role": "user",
                "content": "I met John yesterday",
                "embedding": None,
                "metadata": None,
            },
        )

        # Create entity
        create_query = build_create_entity_query("PERSON", None)
        await neo4j_client.execute_write(
            create_query,
            {
                "id": entity_id,
                "name": "John",
                "type": "PERSON",
                "subtype": None,
                "canonical_name": "John",
                "description": None,
                "embedding": None,
                "confidence": 0.8,
                "metadata": None,
                "location": None,
            },
        )

        # Link message to entity
        results = await neo4j_client.execute_write(
            queries.LINK_MESSAGE_TO_ENTITY,
            {
                "message_id": msg_id,
                "entity_id": entity_id,
                "confidence": 0.8,
                "start_pos": 6,
                "end_pos": 10,
            },
        )
        assert results[0]["r"] is not None


@pytest.mark.integration
class TestEntityRelationQueries:
    """Test entity relation queries against Neo4j."""

    @pytest.mark.asyncio
    async def test_create_entity_relation_by_id(self, neo4j_client):
        """Test creating RELATED_TO relationship by entity IDs."""
        entity1_id = f"test-{uuid4()}"
        entity2_id = f"test-{uuid4()}"

        # Create two entities
        create_query = build_create_entity_query("PERSON", None)
        await neo4j_client.execute_write(
            create_query,
            {
                "id": entity1_id,
                "name": "Alice",
                "type": "PERSON",
                "subtype": None,
                "canonical_name": "Alice",
                "description": None,
                "embedding": None,
                "confidence": 0.9,
                "metadata": None,
                "location": None,
            },
        )
        await neo4j_client.execute_write(
            create_query,
            {
                "id": entity2_id,
                "name": "Bob",
                "type": "PERSON",
                "subtype": None,
                "canonical_name": "Bob",
                "description": None,
                "embedding": None,
                "confidence": 0.9,
                "metadata": None,
                "location": None,
            },
        )

        # Create relation
        results = await neo4j_client.execute_write(
            queries.CREATE_ENTITY_RELATION_BY_ID,
            {
                "source_id": entity1_id,
                "target_id": entity2_id,
                "id": f"test-{uuid4()}",
                "relation_type": "knows",
                "confidence": 0.85,
                "derived": False,
                "extractor": "test-extractor",
                "message_id": None,
                "evidence": None,
            },
        )
        assert results[0]["r"] is not None

    @pytest.mark.asyncio
    async def test_create_entity_relation_by_name(self, neo4j_client):
        """Test creating RELATED_TO relationship by entity names."""
        entity1_id = f"test-{uuid4()}"
        entity2_id = f"test-{uuid4()}"

        # Create two entities
        create_query = build_create_entity_query("ORGANIZATION", None)
        await neo4j_client.execute_write(
            create_query,
            {
                "id": entity1_id,
                "name": "Acme Corp",
                "type": "ORGANIZATION",
                "subtype": None,
                "canonical_name": "Acme Corp",
                "description": None,
                "embedding": None,
                "confidence": 0.9,
                "metadata": None,
                "location": None,
            },
        )
        create_query = build_create_entity_query("PERSON", None)
        await neo4j_client.execute_write(
            create_query,
            {
                "id": entity2_id,
                "name": "Jane Smith",
                "type": "PERSON",
                "subtype": None,
                "canonical_name": "Jane Smith",
                "description": None,
                "embedding": None,
                "confidence": 0.9,
                "metadata": None,
                "location": None,
            },
        )

        # Create relation by name
        results = await neo4j_client.execute_write(
            queries.CREATE_ENTITY_RELATION_BY_NAME,
            {
                "source_name": "Jane Smith",
                "target_name": "Acme Corp",
                "id": f"test-{uuid4()}",
                "relation_type": "works_at",
                "confidence": 0.9,
                "derived": False,
                "extractor": "test-extractor",
                "message_id": None,
                "evidence": "Jane Smith works at Acme Corp.",
            },
        )
        assert len(results) > 0


@pytest.mark.integration
class TestSchemaPersistenceQueries:
    """Test schema persistence queries against Neo4j."""

    @pytest.mark.asyncio
    async def test_create_and_get_schema(self, neo4j_client):
        """Test creating and retrieving a schema."""
        schema_id = f"test-{uuid4()}"
        schema_name = f"test-schema-{uuid4()}"

        # Create schema
        await neo4j_client.execute_write(
            queries.CREATE_SCHEMA,
            {
                "id": schema_id,
                "name": schema_name,
                "version": "1.0",
                "description": "Test schema",
                "config": '{"entity_types": []}',
                "is_active": True,
                "created_by": "test",
            },
        )

        # Get schema by name
        results = await neo4j_client.execute_read(queries.GET_SCHEMA_BY_NAME, {"name": schema_name})
        assert len(results) == 1
        assert results[0]["s"]["id"] == schema_id
        assert results[0]["s"]["version"] == "1.0"
        assert results[0]["s"]["is_active"] is True

    @pytest.mark.asyncio
    async def test_schema_versioning(self, neo4j_client):
        """Test creating multiple schema versions."""
        schema_name = f"test-schema-{uuid4()}"

        # Create version 1.0
        await neo4j_client.execute_write(
            queries.CREATE_SCHEMA,
            {
                "id": f"test-{uuid4()}",
                "name": schema_name,
                "version": "1.0",
                "description": "Version 1",
                "config": "{}",
                "is_active": False,
                "created_by": "test",
            },
        )

        # Create version 2.0 (active)
        schema_id_v2 = f"test-{uuid4()}"
        await neo4j_client.execute_write(
            queries.CREATE_SCHEMA,
            {
                "id": schema_id_v2,
                "name": schema_name,
                "version": "2.0",
                "description": "Version 2",
                "config": "{}",
                "is_active": True,
                "created_by": "test",
            },
        )

        # Get active schema should return v2
        results = await neo4j_client.execute_read(queries.GET_SCHEMA_BY_NAME, {"name": schema_name})
        assert len(results) == 1
        assert results[0]["s"]["version"] == "2.0"

        # List all versions
        results = await neo4j_client.execute_read(
            queries.LIST_SCHEMA_VERSIONS, {"name": schema_name}
        )
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_delete_schema(self, neo4j_client):
        """Test deleting a schema."""
        schema_id = f"test-{uuid4()}"
        schema_name = f"test-schema-{uuid4()}"

        # Create schema
        await neo4j_client.execute_write(
            queries.CREATE_SCHEMA,
            {
                "id": schema_id,
                "name": schema_name,
                "version": "1.0",
                "description": "To be deleted",
                "config": "{}",
                "is_active": True,
                "created_by": "test",
            },
        )

        # Delete schema
        results = await neo4j_client.execute_write(queries.DELETE_SCHEMA, {"id": schema_id})
        assert results[0]["deleted"] is True

        # Verify deletion
        results = await neo4j_client.execute_read(queries.GET_SCHEMA_BY_NAME, {"name": schema_name})
        assert len(results) == 0


@pytest.mark.integration
class TestSchemaManagementQueryFunctions:
    """Test schema management DDL query functions against Neo4j."""

    @pytest.mark.asyncio
    async def test_create_and_drop_index(self, neo4j_client):
        """Test creating and dropping an index."""
        index_name = f"test_idx_{uuid4().hex[:8]}"

        # Create index
        create_query = queries.create_index_query(index_name, "TestNode", "testProp")
        await neo4j_client.execute_write(create_query)

        # Verify index exists
        exists = await neo4j_client.check_index_exists(index_name)
        assert exists is True

        # Drop index
        drop_query = queries.drop_index_query(index_name)
        await neo4j_client.execute_write(drop_query)

        # Verify index is gone
        exists = await neo4j_client.check_index_exists(index_name)
        assert exists is False

    @pytest.mark.asyncio
    async def test_create_and_drop_constraint(self, neo4j_client):
        """Test creating and dropping a constraint."""
        constraint_name = f"test_constraint_{uuid4().hex[:8]}"

        # Create constraint
        create_query = queries.create_constraint_query(
            constraint_name, "TestConstraintNode", "uniqueProp"
        )
        await neo4j_client.execute_write(create_query)

        # Verify constraint exists
        exists = await neo4j_client.check_constraint_exists(constraint_name)
        assert exists is True

        # Drop constraint
        drop_query = queries.drop_constraint_query(constraint_name)
        await neo4j_client.execute_write(drop_query)

        # Verify constraint is gone
        exists = await neo4j_client.check_constraint_exists(constraint_name)
        assert exists is False


@pytest.mark.integration
class TestReasoningMemoryQueries:
    """Test reasoning memory queries against Neo4j."""

    @pytest.mark.asyncio
    async def test_create_reasoning_trace(self, neo4j_client):
        """Test creating a reasoning trace."""
        trace_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"

        await neo4j_client.execute_write(
            queries.CREATE_REASONING_TRACE,
            {
                "id": trace_id,
                "session_id": session_id,
                "task": "Find information about Neo4j",
                "task_embedding": None,
                "outcome": None,
                "success": None,
                "completed_at": None,
                "metadata": None,
            },
        )

        # Verify trace
        results = await neo4j_client.execute_read(queries.GET_TRACE_WITH_STEPS, {"id": trace_id})
        assert len(results) == 1
        assert results[0]["rt"]["task"] == "Find information about Neo4j"

    @pytest.mark.asyncio
    async def test_create_reasoning_step(self, neo4j_client):
        """Test creating a reasoning step linked to a trace."""
        trace_id = f"test-{uuid4()}"
        step_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"

        # Create trace first
        await neo4j_client.execute_write(
            queries.CREATE_REASONING_TRACE,
            {
                "id": trace_id,
                "session_id": session_id,
                "task": "Test task",
                "task_embedding": None,
                "outcome": None,
                "success": None,
                "completed_at": None,
                "metadata": None,
            },
        )

        # Create step
        await neo4j_client.execute_write(
            queries.CREATE_REASONING_STEP,
            {
                "trace_id": trace_id,
                "id": step_id,
                "step_number": 1,
                "thought": "I should search the database",
                "action": "search",
                "observation": "Found 10 results",
                "embedding": None,
                "metadata": None,
            },
        )

        # Verify step is linked to trace
        results = await neo4j_client.execute_read(queries.GET_TRACE_WITH_STEPS, {"id": trace_id})
        assert len(results) == 1
        steps = results[0]["steps"]
        assert len(steps) == 1
        assert steps[0]["thought"] == "I should search the database"


@pytest.mark.integration
class TestSessionQueries:
    """Test session-related queries against Neo4j."""

    @pytest.mark.asyncio
    async def test_list_sessions(self, neo4j_client):
        """Test listing sessions with ordering."""
        # Create multiple conversations with unique prefix
        test_prefix = f"test-list-{uuid4().hex[:8]}"

        for i in range(3):
            await neo4j_client.execute_write(
                queries.CREATE_CONVERSATION,
                {
                    "id": f"test-{uuid4()}",
                    "session_id": f"{test_prefix}-session-{i}",
                    "title": f"Session {i}",
                },
            )

        # List sessions with prefix filter
        results = await neo4j_client.execute_read(
            queries.LIST_SESSIONS,
            {
                "prefix": test_prefix,
                "limit": 10,
                "offset": 0,
                "order_by": "created_at",
                "order_dir": "desc",
            },
        )

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_delete_session_data(self, neo4j_client):
        """Test deleting all data for a session."""
        conv_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"
        msg_id = f"test-{uuid4()}"

        # Create conversation with message
        await neo4j_client.execute_write(
            queries.CREATE_CONVERSATION,
            {"id": conv_id, "session_id": session_id, "title": None},
        )
        await neo4j_client.execute_write(
            queries.CREATE_MESSAGE,
            {
                "conversation_id": conv_id,
                "id": msg_id,
                "role": "user",
                "content": "Test message",
                "embedding": None,
                "metadata": None,
            },
        )

        # Delete session
        await neo4j_client.execute_write(queries.DELETE_SESSION_DATA, {"session_id": session_id})

        # Verify conversation is gone
        results = await neo4j_client.execute_read(
            queries.GET_CONVERSATION_BY_SESSION, {"session_id": session_id}
        )
        assert len(results) == 0


@pytest.mark.integration
class TestEntityExtractionQueries:
    """Test entity extraction helper queries against Neo4j."""

    @pytest.mark.asyncio
    async def test_get_messages_for_entity_extraction(self, neo4j_client):
        """Test getting messages that need entity extraction."""
        conv_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"
        msg1_id = f"test-{uuid4()}"
        msg2_id = f"test-{uuid4()}"
        entity_id = f"test-{uuid4()}"

        # Create conversation with two messages
        await neo4j_client.execute_write(
            queries.CREATE_CONVERSATION,
            {"id": conv_id, "session_id": session_id, "title": None},
        )
        await neo4j_client.execute_write(
            queries.CREATE_MESSAGE,
            {
                "conversation_id": conv_id,
                "id": msg1_id,
                "role": "user",
                "content": "Message 1",
                "embedding": None,
                "metadata": None,
            },
        )
        await neo4j_client.execute_write(
            queries.CREATE_MESSAGE,
            {
                "conversation_id": conv_id,
                "id": msg2_id,
                "role": "assistant",
                "content": "Message 2",
                "embedding": None,
                "metadata": None,
            },
        )

        # Link msg1 to an entity (simulating extraction already done)
        create_query = build_create_entity_query("PERSON", None)
        await neo4j_client.execute_write(
            create_query,
            {
                "id": entity_id,
                "name": "Test Person",
                "type": "PERSON",
                "subtype": None,
                "canonical_name": "Test Person",
                "description": None,
                "embedding": None,
                "confidence": 0.9,
                "metadata": None,
                "location": None,
            },
        )
        await neo4j_client.execute_write(
            queries.LINK_MESSAGE_TO_ENTITY,
            {
                "message_id": msg1_id,
                "entity_id": entity_id,
                "confidence": 0.9,
                "start_pos": 0,
                "end_pos": 5,
            },
        )

        # Query for messages without entities
        results = await neo4j_client.execute_read(
            queries.GET_MESSAGES_FOR_ENTITY_EXTRACTION, {"session_id": session_id}
        )

        # Only msg2 should be returned (msg1 already has entities)
        assert len(results) == 1
        assert results[0]["id"] == msg2_id

    @pytest.mark.asyncio
    async def test_get_all_messages_for_session(self, neo4j_client):
        """Test getting all messages for a session."""
        conv_id = f"test-{uuid4()}"
        session_id = f"test-session-{uuid4()}"

        # Create conversation with messages
        await neo4j_client.execute_write(
            queries.CREATE_CONVERSATION,
            {"id": conv_id, "session_id": session_id, "title": None},
        )
        for i in range(3):
            await neo4j_client.execute_write(
                queries.CREATE_MESSAGE,
                {
                    "conversation_id": conv_id,
                    "id": f"test-{uuid4()}",
                    "role": "user",
                    "content": f"Message {i}",
                    "embedding": None,
                    "metadata": None,
                },
            )

        # Get all messages
        results = await neo4j_client.execute_read(
            queries.GET_ALL_MESSAGES_FOR_SESSION, {"session_id": session_id}
        )
        assert len(results) == 3
