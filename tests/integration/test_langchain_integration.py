"""Integration tests for the LangChain 1.x integration (needs Neo4j).

These drive the adapters against a real database. The LangChain contracts they
rely on — ``BaseChatMessageHistory``, ``BaseRetriever``, ``AgentMiddleware`` —
are asserted in ``tests/unit/integrations/test_langchain.py``; here we only care
that the Cypher round-trips work.
"""

import pytest

LANGCHAIN_CORE_AVAILABLE = True
try:
    from langchain_core.messages import AIMessage, HumanMessage
except ImportError:  # pragma: no cover - depends on installed extras
    LANGCHAIN_CORE_AVAILABLE = False

LANGCHAIN_AGENTS_AVAILABLE = True
try:
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
except ImportError:  # pragma: no cover - depends on installed extras
    LANGCHAIN_AGENTS_AVAILABLE = False


@pytest.mark.integration
@pytest.mark.skipif(not LANGCHAIN_CORE_AVAILABLE, reason="langchain-core not installed")
class TestNeo4jAgentMemory:
    """Test Neo4jAgentMemory against a live Neo4j."""

    @pytest.mark.asyncio
    async def test_memory_initialization(self, memory_client, session_id):
        """Test initializing Neo4jAgentMemory."""
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        memory = Neo4jAgentMemory(
            memory_client=memory_client,
            session_id=session_id,
        )

        assert memory.session_id == session_id
        assert memory.include_short_term is True
        assert memory.include_long_term is True
        assert memory.include_reasoning is True

    @pytest.mark.asyncio
    async def test_memory_variables(self, memory_client, session_id):
        """Test memory_variables property."""
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        memory = Neo4jAgentMemory(memory_client=memory_client, session_id=session_id)

        variables = memory.memory_variables
        assert "history" in variables
        assert "context" in variables
        assert "preferences" in variables
        assert "similar_tasks" in variables

    @pytest.mark.asyncio
    async def test_memory_variables_filtered(self, memory_client, session_id):
        """Test memory_variables with layers disabled."""
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        memory = Neo4jAgentMemory(
            memory_client=memory_client,
            session_id=session_id,
            include_short_term=False,
            include_reasoning=False,
        )

        variables = memory.memory_variables
        assert "history" not in variables
        assert "context" in variables
        assert "similar_tasks" not in variables

    @pytest.mark.asyncio
    async def test_save_and_load_context(self, memory_client, session_id):
        """Test saving and loading conversation context."""
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        memory = Neo4jAgentMemory(
            memory_client=memory_client,
            session_id=session_id,
            include_long_term=False,
            include_reasoning=False,
        )

        await memory.asave_context(
            {"input": "Hello, I am John"},
            {"output": "Hello John! How can I help you today?"},
        )

        variables = await memory.aload_memory_variables({"input": "What is my name?"})

        assert "history" in variables
        assert "John" in variables["history"]
        assert "Hello" in variables["history"]

    @pytest.mark.asyncio
    async def test_chat_message_history_round_trip(self, memory_client, session_id):
        """aadd_messages / aget_messages / aclear against Neo4j."""
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        memory = Neo4jAgentMemory(memory_client=memory_client, session_id=session_id)

        await memory.aadd_messages(
            [
                HumanMessage(content="My name is Alice"),
                AIMessage(content="Nice to meet you, Alice!"),
            ]
        )

        messages = await memory.aget_messages()
        assert [type(m) for m in messages] == [HumanMessage, AIMessage]
        assert messages[0].text == "My name is Alice"

        await memory.aclear()
        assert await memory.aget_messages() == []

    @pytest.mark.asyncio
    async def test_load_memory_with_long_term(self, memory_client, session_id):
        """Test loading memory with long-term context."""
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        await memory_client.long_term.add_preference(
            category="food",
            preference="I love Italian food",
            generate_embedding=True,
        )
        await memory_client.long_term.add_entity(
            name="Italian Restaurant",
            entity_type="ORGANIZATION",
            resolve=False,
            generate_embedding=True,
        )

        memory = Neo4jAgentMemory(
            memory_client=memory_client,
            session_id=session_id,
            include_short_term=False,
            include_reasoning=False,
        )

        variables = await memory.aload_memory_variables({"input": "food recommendations"})

        assert "context" in variables
        assert isinstance(variables["preferences"], list)

    @pytest.mark.asyncio
    async def test_empty_conversation_history(self, memory_client, session_id):
        """Test loading memory with no conversation history."""
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        memory = Neo4jAgentMemory(
            memory_client=memory_client,
            session_id=session_id,
            include_long_term=False,
            include_reasoning=False,
        )

        variables = await memory.aload_memory_variables({"input": "Hello"})

        assert isinstance(variables["history"], str)

    @pytest.mark.asyncio
    async def test_multiple_save_contexts(self, memory_client, session_id):
        """Test saving multiple contexts in sequence."""
        from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

        memory = Neo4jAgentMemory(
            memory_client=memory_client,
            session_id=session_id,
            include_long_term=False,
            include_reasoning=False,
            max_messages=20,
        )

        exchanges = [
            ("Hello", "Hi there!"),
            ("My name is Alice", "Nice to meet you, Alice!"),
            ("What time is it?", "I don't have access to the current time."),
        ]

        for user_input, assistant_output in exchanges:
            await memory.asave_context({"input": user_input}, {"output": assistant_output})

        variables = await memory.aload_memory_variables({"input": "recap"})

        assert "Alice" in variables["history"]
        assert "Hello" in variables["history"]


@pytest.mark.integration
@pytest.mark.skipif(not LANGCHAIN_CORE_AVAILABLE, reason="langchain-core not installed")
class TestNeo4jMemoryRetriever:
    """Test Neo4jMemoryRetriever against a live Neo4j."""

    @pytest.mark.asyncio
    async def test_retriever_initialization(self, memory_client, session_id):
        """Test initializing Neo4jMemoryRetriever."""
        from neo4j_agent_memory.integrations.langchain import Neo4jMemoryRetriever

        retriever = Neo4jMemoryRetriever(
            memory_client=memory_client,
            session_id=session_id,
        )

        assert retriever.session_id == session_id

    @pytest.mark.asyncio
    async def test_retriever_ainvoke(self, memory_client, session_id):
        """Test retrieving documents through LangChain's async entry point."""
        from neo4j_agent_memory.integrations.langchain import Neo4jMemoryRetriever
        from neo4j_agent_memory.memory.short_term import MessageRole

        await memory_client.short_term.add_message(
            session_id,
            MessageRole.USER,
            "I love hiking in the mountains",
            extract_entities=False,
            generate_embedding=True,
        )
        await memory_client.short_term.add_message(
            session_id,
            MessageRole.ASSISTANT,
            "That sounds wonderful! Do you have a favorite trail?",
            extract_entities=False,
            generate_embedding=True,
        )

        retriever = Neo4jMemoryRetriever(
            memory_client=memory_client,
            session_id=session_id,
        )

        docs = await retriever.ainvoke("outdoor activities")

        assert isinstance(docs, list)


@pytest.mark.integration
@pytest.mark.skipif(
    not LANGCHAIN_AGENTS_AVAILABLE,
    reason="the `langchain` distribution is not installed",
)
class TestNeo4jMemoryMiddleware:
    """Test Neo4jMemoryMiddleware inside create_agent against a live Neo4j."""

    @pytest.mark.asyncio
    async def test_agent_turn_is_persisted(self, memory_client, session_id):
        """One agent turn with a fake model writes both messages to Neo4j."""
        from neo4j_agent_memory.integrations.langchain import Neo4jMemoryMiddleware

        model = GenericFakeChatModel(messages=iter([AIMessage(content="Try Thai Kitchen.")]))
        agent = create_agent(
            model,
            tools=[],
            system_prompt="You are helpful.",
            middleware=[
                Neo4jMemoryMiddleware(
                    memory_client,
                    session_id=session_id,
                    extract_entities=False,
                )
            ],
        )

        result = await agent.ainvoke({"messages": [("user", "Where should I eat?")]})

        assert result["messages"][-1].content == "Try Thai Kitchen."
        conversation = await memory_client.short_term.get_conversation(session_id)
        stored = [(m.role.value, m.content) for m in conversation.messages]
        assert stored == [
            ("user", "Where should I eat?"),
            ("assistant", "Try Thai Kitchen."),
        ]
