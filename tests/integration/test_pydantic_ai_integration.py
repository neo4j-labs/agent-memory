"""Integration tests for Pydantic AI integration."""

import pytest

from neo4j_agent_memory.memory.short_term import MessageRole


@pytest.mark.integration
class TestMemoryDependency:
    """Test MemoryDependency class."""

    @pytest.mark.asyncio
    async def test_memory_dependency_initialization(self, memory_client, session_id):
        """Test initializing MemoryDependency."""
        from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

        deps = MemoryDependency(client=memory_client, session_id=session_id)

        assert deps.client is memory_client
        assert deps.session_id == session_id

    @pytest.mark.asyncio
    async def test_get_context_empty(self, memory_client, session_id):
        """Test getting context with no data."""
        from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

        deps = MemoryDependency(client=memory_client, session_id=session_id)

        context = await deps.get_context("test query")

        assert isinstance(context, str)

    @pytest.mark.asyncio
    async def test_get_context_with_data(self, memory_client, session_id):
        """Test getting context with stored data."""
        from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

        # Add some data
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I love Italian food",
            extract_entities=False,
            generate_embedding=True,
        )
        await memory_client.long_term.add_preference(
            category="food",
            preference="Loves Italian cuisine",
            generate_embedding=True,
        )

        deps = MemoryDependency(client=memory_client, session_id=session_id)
        context = await deps.get_context("food preferences")

        assert isinstance(context, str)

    @pytest.mark.asyncio
    async def test_save_interaction(self, memory_client, session_id):
        """Test saving an interaction."""
        from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

        deps = MemoryDependency(client=memory_client, session_id=session_id)

        await deps.save_interaction(
            "Hello, I need help",
            "Hello! How can I assist you today?",
            extract_entities=False,
        )

        # Verify messages were saved
        conv = await memory_client.short_term.get_conversation(session_id)
        assert len(conv.messages) >= 2

    @pytest.mark.asyncio
    async def test_add_preference(self, memory_client, session_id):
        """Test adding a preference."""
        from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

        deps = MemoryDependency(client=memory_client, session_id=session_id)

        await deps.add_preference(
            category="music",
            preference="Enjoys classical music",
            context="When working",
        )

        # Verify preference was saved
        prefs = await memory_client.long_term.get_preferences_by_category("music")
        assert len(prefs) >= 1
        assert any("classical" in p.preference.lower() for p in prefs)

    @pytest.mark.asyncio
    async def test_search_preferences(self, memory_client, session_id):
        """Test searching preferences."""
        from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

        # Add preferences
        await memory_client.long_term.add_preference(
            category="sports",
            preference="Plays tennis on weekends",
            generate_embedding=True,
        )
        await memory_client.long_term.add_preference(
            category="sports",
            preference="Enjoys watching basketball",
            generate_embedding=True,
        )

        deps = MemoryDependency(client=memory_client, session_id=session_id)
        results = await deps.search_preferences("sports activities")

        assert isinstance(results, list)
        for result in results:
            assert "category" in result
            assert "preference" in result


@pytest.mark.integration
class TestMemoryTools:
    """Test create_memory_tools function."""

    @pytest.mark.asyncio
    async def test_create_memory_tools(self, memory_client):
        """Test creating memory tools."""
        from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

        tools = create_memory_tools(memory_client)

        assert isinstance(tools, list)
        assert len(tools) == 3

        # Check tool names
        tool_names = [t.__name__ for t in tools]
        assert "search_memory" in tool_names
        assert "save_preference" in tool_names
        assert "recall_preferences" in tool_names

    @pytest.mark.asyncio
    async def test_search_memory_tool(self, memory_client, session_id):
        """Test search_memory tool."""
        from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

        # Add some data
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I went hiking yesterday",
            extract_entities=False,
            generate_embedding=True,
        )

        tools = create_memory_tools(memory_client)
        search_memory = next(t for t in tools if t.__name__ == "search_memory")

        result = await search_memory("outdoor activities")

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_save_preference_tool(self, memory_client):
        """Test save_preference tool."""
        from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

        tools = create_memory_tools(memory_client)
        save_preference = next(t for t in tools if t.__name__ == "save_preference")

        result = await save_preference(
            category="travel",
            preference="Prefers window seats on flights",
        )

        assert isinstance(result, str)
        assert "Saved preference" in result

    @pytest.mark.asyncio
    async def test_recall_preferences_tool(self, memory_client):
        """Test recall_preferences tool."""
        from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

        # Add preferences
        await memory_client.long_term.add_preference(
            category="drinks",
            preference="Prefers coffee over tea",
            generate_embedding=True,
        )

        tools = create_memory_tools(memory_client)
        recall_preferences = next(t for t in tools if t.__name__ == "recall_preferences")

        result = await recall_preferences("beverages")

        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_search_memory_no_results(self, memory_client):
        """Test search_memory with no matching results."""
        from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

        tools = create_memory_tools(memory_client)
        search_memory = next(t for t in tools if t.__name__ == "search_memory")

        result = await search_memory("xyz123nonexistent")

        assert "No relevant memories found" in result

    @pytest.mark.asyncio
    async def test_recall_preferences_no_results(self, memory_client):
        """Test recall_preferences with no matching results."""
        from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

        tools = create_memory_tools(memory_client)
        recall_preferences = next(t for t in tools if t.__name__ == "recall_preferences")

        result = await recall_preferences("xyz123nonexistent")

        assert "No preferences found" in result


@pytest.mark.integration
class TestRecordAgentTrace:
    """Record a PydanticAI 2.x run into reasoning memory and read it back."""

    @pytest.mark.asyncio
    async def test_trace_round_trip(self, memory_client, session_id):
        """A TestModel run (no network) becomes a readable reasoning trace."""
        pytest.importorskip("pydantic_ai")
        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel

        from neo4j_agent_memory.integrations.pydantic_ai import (
            create_memory_tools,
            record_agent_trace,
        )

        tools = create_memory_tools(memory_client)
        agent = Agent(TestModel(), tools=tools)

        result = await agent.run("Find me a vegetarian restaurant")

        trace = await record_agent_trace(
            memory_client.reasoning,
            session_id=session_id,
            result=result,
            task="Find restaurant recommendation",
        )

        read_back = await memory_client.reasoning.get_trace_with_steps(trace.id)

        assert read_back is not None
        assert read_back.task == "Find restaurant recommendation"
        assert read_back.success is True
        # One step per tool call; TestModel exercises every registered tool.
        assert len(read_back.steps) == len(tools)
        tool_names = {
            call.tool_name for step in read_back.steps for call in (step.tool_calls or [])
        }
        assert {"search_memory", "save_preference", "recall_preferences"} <= tool_names
        # 2.x: the outcome summary comes from ``result.output``.
        assert read_back.outcome
