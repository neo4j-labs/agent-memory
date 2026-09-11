"""Integration tests for Google ADK integration with Neo4j."""

import pytest

from neo4j_agent_memory.memory.short_term import MessageRole


@pytest.mark.integration
class TestGoogleADKMemoryServiceIntegration:
    """Integration tests for Neo4jMemoryService with real Neo4j database."""

    @pytest.mark.asyncio
    async def test_add_session_to_memory(self, memory_client, session_id):
        """Test storing a session through ADK memory service."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="test-user",
            extract_on_store=False,  # Disable extraction for faster tests
        )

        # Create a session dict (ADK-style)
        session = {
            "id": session_id,
            "messages": [
                {"role": "user", "content": "Hello, I'm interested in AI"},
                {"role": "assistant", "content": "Great! AI is a fascinating field."},
                {"role": "user", "content": "Tell me about machine learning"},
            ],
        }

        await memory_service.add_session_to_memory(session)

        # Verify messages were stored
        conversation = await memory_client.short_term.get_conversation(session_id)
        assert len(conversation.messages) == 3
        assert conversation.messages[0].content == "Hello, I'm interested in AI"

    @pytest.mark.asyncio
    async def test_search_memory_messages(self, memory_client, session_id):
        """Test searching for messages through ADK memory service."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        # Add some messages first
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I love Python programming and data science",
            extract_entities=False,
            generate_embedding=True,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "Python is great for data science and machine learning!",
            extract_entities=False,
            generate_embedding=True,
        )

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="test-user",
            include_entities=False,
            include_preferences=False,
        )

        # Search memories
        results = await memory_service.search_memory(
            query="Python programming",
            limit=10,
        )

        assert len(results.memories) >= 1
        assert any("Python" in r.content.parts[0].text for r in results.memories)

    @pytest.mark.asyncio
    async def test_search_memory_with_entities(self, memory_client, session_id):
        """Test searching includes entities when enabled."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService
        from neo4j_agent_memory.memory.long_term import EntityType

        # Add an entity
        await memory_client.long_term.add_entity(
            name="Google Cloud",
            entity_type=EntityType.ORGANIZATION,
            description="Cloud computing platform",
            generate_embedding=True,
            resolve=False,
        )

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="test-user",
            include_entities=True,
            include_preferences=False,
        )

        results = await memory_service.search_memory(
            query="Google Cloud platform",
            limit=10,
        )

        assert len(results.memories) >= 1
        assert any("Google Cloud" in r.content.parts[0].text for r in results.memories)

    @pytest.mark.asyncio
    async def test_search_memory_with_preferences(self, memory_client, session_id):
        """Test searching includes preferences when enabled."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        # Add a preference
        await memory_client.long_term.add_preference(
            category="programming",
            preference="Prefers Python over JavaScript",
            context="Language preferences",
            generate_embedding=True,
        )

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="test-user",
            include_entities=False,
            include_preferences=True,
        )

        results = await memory_service.search_memory(
            query="programming language preference",
            limit=10,
        )

        assert len(results.memories) >= 1
        assert any(
            "Prefers Python over JavaScript" in r.content.parts[0].text for r in results.memories
        )

    @pytest.mark.asyncio
    async def test_get_memories_for_session(self, memory_client, session_id):
        """Test getting all memories for a specific session."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        # Add messages to the session
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "First message in session",
            extract_entities=False,
            generate_embedding=False,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "Response to first message",
            extract_entities=False,
            generate_embedding=False,
        )

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="test-user",
        )

        results = await memory_service.get_memories_for_session(session_id)

        assert len(results) == 2
        assert all(r.memory_type == "message" for r in results)

    @pytest.mark.asyncio
    async def test_add_memory_message(self, memory_client, session_id):
        """Test adding a message through ADK memory service."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="test-user",
        )

        result = await memory_service.add_memory(
            content="This is a test message",
            memory_type="message",
            session_id=session_id,
            role="user",
        )

        assert result is not None
        assert result.memory_type == "message"
        assert "test message" in result.content

        # Verify it was stored
        conversation = await memory_client.short_term.get_conversation(session_id)
        assert len(conversation.messages) == 1

    @pytest.mark.asyncio
    async def test_add_memory_preference(self, memory_client, session_id):
        """Test adding a preference through ADK memory service."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="test-user",
        )

        result = await memory_service.add_memory(
            content="Prefers dark mode UI",
            memory_type="preference",
            category="ui",
        )

        assert result is not None
        assert result.memory_type == "preference"
        assert result.metadata["category"] == "ui"

    @pytest.mark.asyncio
    async def test_clear_session(self, memory_client, session_id):
        """Test clearing a session through ADK memory service."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        # Add some messages first
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "Message to be cleared",
            extract_entities=False,
            generate_embedding=False,
        )

        # Verify message exists
        conversation = await memory_client.short_term.get_conversation(session_id)
        assert len(conversation.messages) == 1

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="test-user",
        )

        # Clear the session
        await memory_service.clear_session(session_id)

        # Verify it's cleared
        conversation = await memory_client.short_term.get_conversation(session_id)
        assert len(conversation.messages) == 0

    @pytest.mark.asyncio
    async def test_full_adk_workflow(self, memory_client, session_id):
        """Test a complete ADK workflow with multiple operations."""
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService
        from neo4j_agent_memory.memory.long_term import EntityType

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="test-user",
            include_entities=True,
            include_preferences=True,
            extract_on_store=False,
        )

        # 1. Store a session
        session = {
            "id": session_id,
            "messages": [
                {"role": "user", "content": "I want to learn about Neo4j graph databases"},
                {"role": "assistant", "content": "Neo4j is a powerful graph database!"},
            ],
        }
        await memory_service.add_session_to_memory(session)

        # 2. Add related entity
        await memory_client.long_term.add_entity(
            name="Neo4j",
            entity_type=EntityType.ORGANIZATION,
            description="Graph database company",
            generate_embedding=True,
            resolve=False,
        )

        # 3. Add user preference
        await memory_service.add_memory(
            content="Interested in graph databases",
            memory_type="preference",
            category="technology",
        )

        # 4. Search across all memory types
        results = await memory_service.search_memory(
            query="graph database",
            limit=20,
        )

        # Extract all text from the retrieved memories
        texts = [r.content.parts[0].text for r in results.memories]

        # Verify both the message and the entity/preference text were retrieved
        assert any("Neo4j is a powerful graph database!" in t for t in texts)
        assert any(
            "Graph database company" in t or "Interested in graph databases" in t for t in texts
        )

        session_memories = await memory_service.get_memories_for_session(session_id)
        assert len(session_memories) == 2


@pytest.mark.integration
class TestGoogleADK2xSessionIngestion:
    """End-to-end ingestion of a real google-adk 2.x ``Session``."""

    @pytest.mark.asyncio
    async def test_adk_session_events_round_trip(self, memory_client, session_id):
        """Events in, ADK MemoryEntry objects out (the load_memory path)."""
        pytest.importorskip("google.adk", reason="requires the [google-adk] extra")

        from google.adk.events.event import Event
        from google.adk.memory.base_memory_service import SearchMemoryResponse
        from google.adk.sessions.session import Session
        from google.adk.tools import _memory_entry_utils
        from google.genai import types

        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        memory_service = Neo4jMemoryService(
            memory_client=memory_client,
            user_id="adk-2x-user",
            include_entities=False,
            include_preferences=False,
            extract_on_store=False,
        )

        session = Session(
            id=session_id,
            app_name="memory-demo",
            user_id="adk-2x-user",
            events=[
                Event(
                    author="user",
                    content=types.Content(
                        role="user",
                        parts=[types.Part(text="Project Alpha ships next Friday.")],
                    ),
                ),
                Event(
                    author="memory_demo",
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text="Noted: Project Alpha ships next Friday.")],
                    ),
                ),
                # Tool-only event — must not be ingested (#130, defect 2).
                Event(
                    author="memory_demo",
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                function_call=types.FunctionCall(
                                    name="load_memory", args={"query": "Project Alpha"}
                                )
                            )
                        ],
                    ),
                ),
            ],
        )

        await memory_service.add_session_to_memory(session)

        conversation = await memory_client.short_term.get_conversation(session_id)
        assert len(conversation.messages) == 2

        response = await memory_service.search_memory(
            app_name="memory-demo",
            user_id="adk-2x-user",
            query="Project Alpha deadline",
        )

        assert isinstance(response, SearchMemoryResponse)
        assert response.memories
        texts = [_memory_entry_utils.extract_text(entry) for entry in response.memories]
        assert any("Project Alpha" in text for text in texts)
        # author/timestamp are what preload_memory renders.
        assert all(entry.author for entry in response.memories)
        assert all(entry.custom_metadata["memory_type"] == "message" for entry in response.memories)
