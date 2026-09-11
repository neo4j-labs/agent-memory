"""Integration tests for Microsoft Agent Framework integration.

These tests require a running Neo4j database and the agent-framework package.
"""

import json

import pytest

from neo4j_agent_memory.memory.short_term import MessageRole

# Check if agent_framework is available
try:
    import agent_framework

    AGENT_FRAMEWORK_AVAILABLE = True
except ImportError:
    AGENT_FRAMEWORK_AVAILABLE = False


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestNeo4jContextProviderInitialization:
    """Test Neo4jContextProvider initialization."""

    @pytest.mark.asyncio
    async def test_provider_initialization(self, memory_client, session_id):
        """Test basic provider initialization."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
        )

        assert provider.session_id == session_id
        assert provider.user_id is None
        assert provider.memory_client is memory_client

    @pytest.mark.asyncio
    async def test_provider_initialization_with_user_id(self, memory_client, session_id):
        """Test provider initialization with user ID."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
            user_id="user-456",
        )

        assert provider.session_id == session_id
        assert provider.user_id == "user-456"

    @pytest.mark.asyncio
    async def test_provider_with_custom_config(self, memory_client, session_id):
        """Test provider with custom configuration."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
            include_short_term=False,
            include_long_term=True,
            include_reasoning=False,
            max_context_items=20,
            max_recent_messages=10,
        )

        assert provider._include_short_term is False
        assert provider._include_long_term is True
        assert provider._include_reasoning is False
        assert provider._max_context_items == 20
        assert provider._max_recent_messages == 10


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestNeo4jContextProviderBeforeRun:
    """Test Neo4jContextProvider before_run hook."""

    @pytest.mark.asyncio
    async def test_before_run_with_empty_memory(self, memory_client, session_id):
        """Test before_run with empty memory."""
        from unittest.mock import MagicMock

        from agent_framework import Message, SessionContext

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
        )

        input_messages = [Message("user", ["Hello"])]
        context = SessionContext(input_messages=input_messages)

        await provider.before_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        # With empty memory, may or may not add instructions
        assert isinstance(context.instructions, list)

    @pytest.mark.asyncio
    async def test_before_run_with_conversation_history(self, memory_client, session_id):
        """Test before_run returns context with conversation history."""
        from unittest.mock import MagicMock

        from agent_framework import Message, SessionContext

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        # Add some conversation history
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I'm looking for running shoes",
            extract_entities=False,
            generate_embedding=True,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "I can help you find running shoes!",
            extract_entities=False,
            generate_embedding=True,
        )

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
        )

        input_messages = [Message("user", ["What about Nike shoes?"])]
        context = SessionContext(input_messages=input_messages)

        await provider.before_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        assert isinstance(context.instructions, list)

    @pytest.mark.asyncio
    async def test_before_run_with_preferences(self, memory_client, session_id):
        """Test before_run includes preferences in context."""
        from unittest.mock import MagicMock

        from agent_framework import Message, SessionContext

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        # Add a preference
        await memory_client.long_term.add_preference(
            category="shopping",
            preference="Prefers eco-friendly products",
            generate_embedding=True,
        )

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
        )

        input_messages = [Message("user", ["Show me products"])]
        context = SessionContext(input_messages=input_messages)

        await provider.before_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        assert isinstance(context.instructions, list)

    @pytest.mark.asyncio
    async def test_before_run_with_entities(self, memory_client, session_id):
        """Test before_run includes entities in context."""
        from unittest.mock import MagicMock

        from agent_framework import Message, SessionContext

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider
        from neo4j_agent_memory.memory.long_term import EntityType

        # Add an entity
        await memory_client.long_term.add_entity(
            name="Nike Air Max",
            entity_type=EntityType.OBJECT,
            subtype="Product",
            description="Popular running shoe model",
            resolve=False,
            generate_embedding=True,
        )

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
        )

        input_messages = [Message("user", ["Tell me about Nike shoes"])]
        context = SessionContext(input_messages=input_messages)

        await provider.before_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        assert isinstance(context.instructions, list)


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestNeo4jContextProviderAfterRun:
    """Test Neo4jContextProvider after_run hook."""

    @pytest.mark.asyncio
    async def test_after_run_saves_messages(self, memory_client, session_id):
        """Test after_run saves messages to memory."""
        from unittest.mock import MagicMock

        from agent_framework import AgentResponse, Message, SessionContext

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
            extract_entities=False,
            extract_entities_async=False,
        )

        input_messages = [Message("user", ["Find me blue shoes"])]
        context = SessionContext(input_messages=input_messages)

        # Mock the response
        mock_response = MagicMock(spec=AgentResponse)
        mock_response.messages = [Message("assistant", ["Here are some blue shoes"])]
        context._response = mock_response

        await provider.after_run(
            agent=MagicMock(),
            session=MagicMock(),
            context=context,
            state={},
        )

        # Verify messages were saved
        conv = await memory_client.short_term.get_conversation(session_id)
        assert len(conv.messages) >= 2, f"Expected at least 2 messages, got {len(conv.messages)}"


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestNeo4jContextProviderSerialization:
    """Test Neo4jContextProvider serialization."""

    @pytest.mark.asyncio
    async def test_serialize_deserialize_roundtrip(self, memory_client, session_id):
        """Test serialize/deserialize roundtrip."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        original = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
            user_id="test-user",
            include_short_term=False,
            include_reasoning=True,
            max_context_items=15,
        )

        # Serialize
        serialized = original.serialize()

        # Deserialize
        restored = Neo4jContextProvider.deserialize(serialized, memory_client)

        assert restored.session_id == session_id
        assert restored.user_id == "test-user"
        assert restored._include_short_term is False
        assert restored._include_reasoning is True
        assert restored._max_context_items == 15


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestNeo4jChatMessageStore:
    """Test Neo4jChatMessageStore."""

    @pytest.mark.asyncio
    async def test_add_messages(self, memory_client, session_id):
        """Test adding messages."""
        from agent_framework import Message

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        store = Neo4jChatMessageStore(
            memory_client=memory_client,
            session_id=session_id,
        )

        messages = [
            Message("user", ["Hello"]),
            Message("assistant", ["Hi there!"]),
        ]

        await store.add_messages(messages)

        # Verify messages were added
        stored = await store.list_messages()
        assert len(stored) >= 2

    @pytest.mark.asyncio
    async def test_list_messages(self, memory_client, session_id):
        """Test listing messages."""
        from agent_framework import Message

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        store = Neo4jChatMessageStore(
            memory_client=memory_client,
            session_id=session_id,
        )

        # Add messages
        await store.add_messages(
            [
                Message("user", ["Test message 1"]),
                Message("assistant", ["Test response 1"]),
            ]
        )

        messages = await store.list_messages()

        assert isinstance(messages, list)
        assert all(isinstance(m, Message) for m in messages)

    @pytest.mark.asyncio
    async def test_clear_messages(self, memory_client, session_id):
        """Test clearing messages."""
        from agent_framework import Message

        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        store = Neo4jChatMessageStore(
            memory_client=memory_client,
            session_id=session_id,
        )

        # Add messages
        await store.add_messages([Message("user", ["Test"])])

        # Clear
        await store.clear()

        # Verify cleared
        messages = await store.list_messages()
        assert len(messages) == 0

    @pytest.mark.asyncio
    async def test_serialize_deserialize(self, memory_client, session_id):
        """Test serialize/deserialize."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        original = Neo4jChatMessageStore(
            memory_client=memory_client,
            session_id=session_id,
            max_messages=100,
        )

        serialized = original.serialize()
        restored = Neo4jChatMessageStore.deserialize(serialized, memory_client)

        assert restored.session_id == session_id


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestNeo4jMicrosoftMemory:
    """Test Neo4jMicrosoftMemory unified interface."""

    @pytest.mark.asyncio
    async def test_initialization(self, memory_client, session_id):
        """Test memory initialization."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        assert memory.session_id == session_id
        assert memory.context_provider is not None
        assert memory.chat_store is not None

    @pytest.mark.asyncio
    async def test_from_memory_client(self, memory_client, session_id):
        """Test factory method."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory.from_memory_client(
            memory_client=memory_client,
            session_id=session_id,
            include_short_term=True,
        )

        assert memory.session_id == session_id

    @pytest.mark.asyncio
    async def test_get_context(self, memory_client, session_id):
        """Test get_context method."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        context = await memory.get_context("test query")

        assert isinstance(context, str)

    @pytest.mark.asyncio
    async def test_save_message(self, memory_client, session_id):
        """Test save_message method."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        msg = await memory.save_message("user", "Hello from test")

        assert msg is not None

    @pytest.mark.asyncio
    async def test_search_memory(self, memory_client, session_id):
        """Test search_memory method."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        results = await memory.search_memory(
            query="test",
            include_messages=True,
            include_entities=True,
            include_preferences=True,
        )

        assert isinstance(results, dict)
        assert "messages" in results
        assert "entities" in results
        assert "preferences" in results

    @pytest.mark.asyncio
    async def test_add_preference(self, memory_client, session_id):
        """Test add_preference method."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        pref = await memory.add_preference(
            category="brand",
            preference="Prefers Nike shoes",
        )

        assert pref is not None

    @pytest.mark.asyncio
    async def test_add_fact(self, memory_client, session_id):
        """Test add_fact method."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        fact = await memory.add_fact(
            subject="user",
            predicate="wears",
            obj="size 10 shoes",
        )

        assert fact is not None


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestMemoryTools:
    """Test memory tools."""

    @pytest.mark.asyncio
    async def test_create_memory_tools(self, memory_client, session_id):
        """Test creating memory tools."""
        from neo4j_agent_memory.integrations.microsoft_agent import (
            Neo4jMicrosoftMemory,
            create_memory_tools,
        )

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        tools = create_memory_tools(memory)

        assert isinstance(tools, list)
        assert len(tools) >= 6

        tool_names = [t.name for t in tools]
        assert "search_memory" in tool_names
        assert "remember_preference" in tool_names
        assert "recall_preferences" in tool_names
        assert "search_knowledge" in tool_names
        assert "remember_fact" in tool_names
        assert "find_similar_tasks" in tool_names

    @pytest.mark.asyncio
    async def test_execute_search_memory(self, memory_client, session_id):
        """Test executing search_memory tool."""
        from neo4j_agent_memory.integrations.microsoft_agent import (
            Neo4jMicrosoftMemory,
            execute_memory_tool,
        )

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        result = await execute_memory_tool(
            memory,
            "search_memory",
            {"query": "shoes", "limit": 5},
        )

        data = json.loads(result)
        assert "results" in data

    @pytest.mark.asyncio
    async def test_execute_remember_preference(self, memory_client, session_id):
        """Test executing remember_preference tool."""
        from neo4j_agent_memory.integrations.microsoft_agent import (
            Neo4jMicrosoftMemory,
            execute_memory_tool,
        )

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        result = await execute_memory_tool(
            memory,
            "remember_preference",
            {"category": "size", "preference": "Wears medium size"},
        )

        data = json.loads(result)
        assert data["status"] == "saved"

    @pytest.mark.asyncio
    async def test_execute_remember_fact(self, memory_client, session_id):
        """Test executing remember_fact tool."""
        from neo4j_agent_memory.integrations.microsoft_agent import (
            Neo4jMicrosoftMemory,
            execute_memory_tool,
        )

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        result = await execute_memory_tool(
            memory,
            "remember_fact",
            {"subject": "user", "predicate": "lives in", "object": "New York"},
        )

        data = json.loads(result)
        assert data["status"] == "saved"


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestGDSIntegration:
    """Test GDS integration."""

    @pytest.mark.asyncio
    async def test_gds_availability_check(self, memory_client, session_id):
        """Test GDS availability check."""
        from neo4j_agent_memory.integrations.microsoft_agent import GDSConfig, GDSIntegration

        config = GDSConfig(enabled=True, warn_on_fallback=False)
        gds = GDSIntegration(memory_client, config)

        # Should return True or False without error
        available = await gds.is_gds_available()
        assert isinstance(available, bool)

    @pytest.mark.asyncio
    async def test_find_shortest_path(self, memory_client, session_id):
        """Test find_shortest_path (works without GDS)."""
        from neo4j_agent_memory.integrations.microsoft_agent import GDSConfig, GDSIntegration
        from neo4j_agent_memory.memory.long_term import EntityType

        # Create two entities with a relationship
        # add_entity returns (entity, dedup_result) tuple
        e1, _ = await memory_client.long_term.add_entity(
            name="Running Shoes",
            entity_type=EntityType.OBJECT,
            subtype="Product",
            resolve=False,
        )
        e2, _ = await memory_client.long_term.add_entity(
            name="Athletic Footwear",
            entity_type=EntityType.CONCEPT,
            resolve=False,
        )

        # Create relationship between them
        await memory_client.long_term.add_relationship(
            source=e1,
            target=e2,
            relationship_type="IS_A",
        )

        config = GDSConfig(enabled=True, warn_on_fallback=False)
        gds = GDSIntegration(memory_client, config)

        path = await gds.find_shortest_path("Running Shoes", "Athletic Footwear", max_hops=3)

        # May or may not find path depending on graph structure
        assert path is None or isinstance(path, dict)

    @pytest.mark.asyncio
    async def test_pagerank_fallback(self, memory_client, session_id):
        """Test PageRank with fallback."""
        from neo4j_agent_memory.integrations.microsoft_agent import GDSConfig, GDSIntegration
        from neo4j_agent_memory.memory.long_term import EntityType

        # Create some entities
        # add_entity returns (entity, dedup_result) tuple
        e1, _ = await memory_client.long_term.add_entity(
            name="Product A",
            entity_type=EntityType.OBJECT,
            resolve=False,
        )
        e2, _ = await memory_client.long_term.add_entity(
            name="Product B",
            entity_type=EntityType.OBJECT,
            resolve=False,
        )

        config = GDSConfig(enabled=True, fallback_to_basic=True, warn_on_fallback=False)
        gds = GDSIntegration(memory_client, config)

        # Force fallback mode
        gds._gds_available = False

        scores = await gds.get_pagerank_scores([str(e1.id), str(e2.id)])

        assert isinstance(scores, list)


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestReasoningTraces:
    """Test reasoning trace recording."""

    @pytest.mark.asyncio
    async def test_record_agent_trace(self, memory_client, session_id):
        """Test recording an agent trace."""
        from agent_framework import Message

        from neo4j_agent_memory.integrations.microsoft_agent import (
            Neo4jMicrosoftMemory,
            record_agent_trace,
        )

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        messages = [
            Message("user", ["Find me running shoes"]),
            Message("assistant", ["Here are some running shoes..."]),
        ]

        trace = await record_agent_trace(
            memory=memory,
            messages=messages,
            task="Find running shoes for user",
            outcome="Found 3 running shoe options",
            success=True,
        )

        assert trace is not None
        assert trace.task == "Find running shoes for user"
        assert trace.success is True

    @pytest.mark.asyncio
    async def test_get_similar_traces(self, memory_client, session_id):
        """Test getting similar traces."""
        from agent_framework import Message

        from neo4j_agent_memory.integrations.microsoft_agent import (
            Neo4jMicrosoftMemory,
            get_similar_traces,
            record_agent_trace,
        )

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        # Create a trace with embedding
        await record_agent_trace(
            memory=memory,
            messages=[Message("user", ["Test"])],
            task="Find athletic shoes for running",
            generate_embedding=True,
        )

        # Find similar traces
        traces = await get_similar_traces(
            memory=memory,
            task="Search for jogging shoes",
            limit=5,
        )

        assert isinstance(traces, list)

    @pytest.mark.asyncio
    async def test_format_traces_for_prompt(self, memory_client, session_id):
        """Test formatting traces for prompt."""
        from agent_framework import Message

        from neo4j_agent_memory.integrations.microsoft_agent import (
            Neo4jMicrosoftMemory,
            format_traces_for_prompt,
            record_agent_trace,
        )

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        trace = await record_agent_trace(
            memory=memory,
            messages=[Message("user", ["Test"])],
            task="Test task",
            outcome="Test outcome",
            success=True,
        )

        formatted = format_traces_for_prompt([trace])

        assert "Past experience" in formatted
        assert "Test task" in formatted


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestSessionIsolation:
    """Test session isolation."""

    @pytest.mark.asyncio
    async def test_different_sessions_isolated(self, memory_client):
        """Test that different sessions are isolated."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory1 = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id="session-isolation-1",
        )
        memory2 = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id="session-isolation-2",
        )

        # Add message to session 1
        await memory1.save_message("user", "Session 1 message")

        # Add message to session 2
        await memory2.save_message("user", "Session 2 message")

        # Get conversations
        conv1 = await memory1.get_conversation()
        conv2 = await memory2.get_conversation()

        # Verify isolation
        conv1_content = [m.text for m in conv1]
        conv2_content = [m.text for m in conv2]

        assert "Session 1 message" in conv1_content
        assert "Session 2 message" not in conv1_content
        assert "Session 2 message" in conv2_content
        assert "Session 1 message" not in conv2_content

        # Cleanup
        await memory1.clear_session()
        await memory2.clear_session()


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestEdgeCases:
    """Test edge cases."""

    @pytest.mark.asyncio
    async def test_large_content(self, memory_client, session_id):
        """Test handling of large content."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        large_content = "A" * 5000
        await memory.save_message("user", large_content)

        conv = await memory.get_conversation()
        assert any(large_content in m.text for m in conv)

    @pytest.mark.asyncio
    async def test_special_characters(self, memory_client, session_id):
        """Test handling of special characters."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        special_content = "Special: <>&\"'`\n\t日本語 emoji 🛒💳"
        await memory.save_message("user", special_content)

        conv = await memory.get_conversation()
        assert any(special_content in m.text for m in conv)

    @pytest.mark.asyncio
    async def test_empty_query(self, memory_client, session_id):
        """Test handling of empty query."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        results = await memory.search_memory(query="")

        assert isinstance(results, dict)

    @pytest.mark.asyncio
    async def test_clear_session(self, memory_client, session_id):
        """Test clearing session."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

        memory = Neo4jMicrosoftMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        # Add messages
        await memory.save_message("user", "Test message")

        # Clear
        await memory.clear_session()

        # Verify cleared
        conv = await memory.get_conversation()
        assert len(conv) == 0


@pytest.mark.integration
@pytest.mark.skipif(not AGENT_FRAMEWORK_AVAILABLE, reason="Microsoft Agent Framework not installed")
class TestAgentLoopWiring:
    """Drive a real ``Agent`` run loop over the Neo4j providers.

    Every other test in this file calls ``before_run`` / ``after_run`` by hand,
    which is why the 1.x GA rename of ``BaseContextProvider`` ->
    ``ContextProvider`` went unnoticed: the providers were never handed to an
    actual agent. These tests assemble the pipeline the way the docs tell users
    to (``chat_client.as_agent(context_providers=[...])``) against a fake chat
    client, so a future change to the provider protocol fails here.
    """

    @staticmethod
    def _echo_client(reply="Canned reply"):
        """Build a minimal non-streaming chat client that returns ``reply``."""
        from agent_framework import BaseChatClient, ChatResponse, Message

        class EchoChatClient(BaseChatClient):
            """Records what the agent sent it and answers with a canned message."""

            def __init__(self):
                super().__init__()
                self.seen_messages = []
                self.seen_instructions = ""

            def _inner_get_response(self, *, messages, stream, options, **kwargs):
                self.seen_messages = list(messages)
                # Provider instructions reach the client through the options
                # mapping (``Agent`` merges ``SessionContext.instructions`` into
                # ``chat_options["instructions"]``), not as a system message.
                self.seen_instructions = options.get("instructions") or ""

                async def _respond():
                    return ChatResponse(
                        messages=[Message("assistant", [reply])],
                        finish_reason="stop",
                    )

                return _respond()

        return EchoChatClient()

    @pytest.mark.asyncio
    async def test_context_provider_persists_a_full_turn(self, memory_client, session_id):
        """One agent.run() writes both the user and assistant message to Neo4j."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
            extract_entities=False,
            include_reasoning=False,
        )
        client = self._echo_client("Hello back")
        agent = client.as_agent(instructions="Be terse.", context_providers=[provider])

        response = await agent.run("Remember that I like graph databases")

        assert response.text == "Hello back"

        conv = await memory_client.short_term.get_conversation(session_id=session_id)
        roles = [m.role.value if hasattr(m.role, "value") else str(m.role) for m in conv.messages]
        contents = [m.content for m in conv.messages]
        assert roles == ["user", "assistant"]
        assert contents == ["Remember that I like graph databases", "Hello back"]

    @pytest.mark.asyncio
    async def test_context_provider_injects_stored_memory(self, memory_client, session_id):
        """Memory stored before the run reaches the model as instructions."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jContextProvider

        await memory_client.short_term.add_message(
            session_id=session_id,
            role="user",
            content="I am shopping for a birthday gift for my sister",
            extract_entities=False,
            generate_embedding=False,
        )

        provider = Neo4jContextProvider(
            memory_client=memory_client,
            session_id=session_id,
            extract_entities=False,
            include_long_term=False,
            include_reasoning=False,
        )
        client = self._echo_client()
        agent = client.as_agent(context_providers=[provider])

        await agent.run("Any suggestions?")

        assert "Recent Conversation" in client.seen_instructions
        assert "birthday gift for my sister" in client.seen_instructions

    @pytest.mark.asyncio
    async def test_chat_store_loads_history_into_the_next_run(self, memory_client, session_id):
        """The history provider replays earlier turns on the following run."""
        from neo4j_agent_memory.integrations.microsoft_agent import Neo4jChatMessageStore

        store = Neo4jChatMessageStore(
            memory_client=memory_client,
            session_id=session_id,
            generate_embeddings=False,
        )
        client = self._echo_client("Noted")
        agent = client.as_agent(context_providers=[store])

        await agent.run("First question")
        await agent.run("Second question")

        # The second run must have replayed turn one before the new input.
        texts = [m.text for m in client.seen_messages]
        assert "First question" in texts
        assert "Noted" in texts
        assert texts[-1] == "Second question"

        conv = await memory_client.short_term.get_conversation(session_id=session_id)
        assert [m.content for m in conv.messages] == [
            "First question",
            "Noted",
            "Second question",
            "Noted",
        ]
