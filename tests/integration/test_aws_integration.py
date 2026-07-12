"""Integration tests for AWS features.

These tests require:
- AWS credentials configured (via environment or IAM role)
- Neo4j database connection (via NEO4J_URI, NEO4J_PASSWORD)
- Bedrock model access enabled

Skip with: pytest -m "not aws"
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient


# Skip all tests in this module if AWS credentials not available
pytestmark = [
    pytest.mark.aws,
    pytest.mark.integration,
]


def aws_credentials_available() -> bool:
    """Check if AWS credentials are available."""
    return bool(
        os.environ.get("AWS_ACCESS_KEY_ID")
        or os.environ.get("AWS_PROFILE")
        or os.environ.get("AWS_ROLE_ARN")
    )


def neo4j_credentials_available() -> bool:
    """Check if Neo4j credentials are available."""
    return bool(os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_PASSWORD"))


class TestBedrockEmbedderIntegration:
    """Integration tests for BedrockEmbedder with real AWS."""

    @pytest.mark.skipif(
        not aws_credentials_available(),
        reason="AWS credentials not available",
    )
    @pytest.mark.asyncio
    async def test_real_embedding_generation(self) -> None:
        """Test generating real embeddings via Bedrock."""
        from neo4j_agent_memory.embeddings.bedrock import BedrockEmbedder

        embedder = BedrockEmbedder(
            model="amazon.titan-embed-text-v2:0",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )

        embedding = await embedder.embed("Hello, world!")

        assert len(embedding) == 1024
        assert all(isinstance(v, float) for v in embedding)

    @pytest.mark.skipif(
        not aws_credentials_available(),
        reason="AWS credentials not available",
    )
    @pytest.mark.asyncio
    async def test_real_batch_embedding(self) -> None:
        """Test batch embedding generation via Bedrock."""
        from neo4j_agent_memory.embeddings.bedrock import BedrockEmbedder

        embedder = BedrockEmbedder(
            model="amazon.titan-embed-text-v2:0",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )

        texts = ["First text", "Second text", "Third text"]
        embeddings = await embedder.embed_batch(texts)

        assert len(embeddings) == 3
        assert all(len(e) == 1024 for e in embeddings)


class TestBedrockEmbedderMocked:
    """Unit-style integration tests with mocked Bedrock client."""

    @pytest.fixture
    def mock_bedrock_client(self):
        """Create a mock Bedrock client."""
        import json

        mock_client = MagicMock()

        def create_response(embedding):
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps({"embedding": embedding}).encode()
            return {"body": mock_body}

        mock_client.invoke_model.side_effect = lambda **_kwargs: create_response([0.1] * 1024)
        return mock_client

    @pytest.mark.asyncio
    async def test_embedder_with_mocked_client(self, mock_bedrock_client) -> None:
        """Test embedder works correctly with mocked client."""
        with patch.dict("sys.modules", {"boto3": MagicMock()}):
            import boto3

            mock_session = MagicMock()
            mock_session.client.return_value = mock_bedrock_client
            boto3.Session.return_value = mock_session

            from neo4j_agent_memory.embeddings.bedrock import BedrockEmbedder

            embedder = BedrockEmbedder()
            embedding = await embedder.embed("Test text")

            assert len(embedding) == 1024
            mock_bedrock_client.invoke_model.assert_called_once()


class TestStrandsIntegrationMocked:
    """Integration tests for Strands tools with mocked dependencies."""

    @pytest.fixture
    def mock_memory_client(self):
        """Create a mock MemoryClient for testing."""
        client = MagicMock()
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client.reasoning = MagicMock()
        client._client = MagicMock()

        # Setup async mocks
        mock_message = MagicMock()
        mock_message.id = "msg-123"
        mock_message.content = "Test message"
        mock_message.role = MagicMock(value="user")

        client.short_term.search_messages = AsyncMock(return_value=[mock_message])
        client.short_term.add_message = AsyncMock(return_value=mock_message)
        client.long_term.search_entities = AsyncMock(return_value=[])
        client.long_term.search_preferences = AsyncMock(return_value=[])
        client.long_term.get_entity_context = AsyncMock(return_value={})

        return client

    def test_context_graph_tools_creation(self) -> None:
        """Test creating context graph tools."""
        mock_tool = MagicMock()
        mock_tool.side_effect = lambda fn: fn

        with patch.dict("sys.modules", {"strands": MagicMock(tool=mock_tool)}):
            from neo4j_agent_memory.integrations.strands import context_graph_tools

            env = {
                "NEO4J_URI": "neo4j+s://test.databases.neo4j.io",
                "NEO4J_PASSWORD": "test-password",
            }

            with patch.dict(os.environ, env, clear=False):
                tools = context_graph_tools()

            assert len(tools) == 4

    def test_strands_config_from_env(self) -> None:
        """Test StrandsConfig loads from environment."""
        from neo4j_agent_memory.integrations.strands import StrandsConfig

        env = {
            "NEO4J_URI": "neo4j+s://test.databases.neo4j.io",
            "NEO4J_PASSWORD": "test-password",
            "AWS_REGION": "us-west-2",
        }

        with patch.dict(os.environ, env, clear=False):
            config = StrandsConfig.from_env()

        assert config.neo4j_uri == "neo4j+s://test.databases.neo4j.io"
        assert config.aws_region == "us-west-2"


class TestAgentCoreProviderIntegration:
    """Integration tests for AgentCore Memory Provider."""

    @pytest.fixture
    def mock_memory_client(self):
        """Create a mock MemoryClient."""
        from datetime import datetime

        client = MagicMock()
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client.reasoning = MagicMock()
        client._client = MagicMock()
        client.get_context = AsyncMock(return_value="Context string")

        # Mock message
        mock_message = MagicMock()
        mock_message.id = "msg-123"
        mock_message.content = "Test message"
        mock_message.role = MagicMock(value="user")
        mock_message.created_at = datetime.utcnow()
        mock_message.metadata = {}

        # Mock conversation
        mock_conversation = MagicMock()
        mock_conversation.messages = [mock_message]

        client.short_term.add_message = AsyncMock(return_value=mock_message)
        client.short_term.search_messages = AsyncMock(return_value=[mock_message])
        client.short_term.get_conversation = AsyncMock(return_value=mock_conversation)
        client.long_term.search_entities = AsyncMock(return_value=[])
        client.long_term.search_preferences = AsyncMock(return_value=[])
        client._client.execute_write = AsyncMock(return_value=[{"deleted": 1}])
        client._client.execute_read = AsyncMock(return_value=[])

        return client

    @pytest.mark.asyncio
    async def test_provider_full_workflow(self, mock_memory_client) -> None:
        """Test complete workflow: store, search, retrieve, delete."""
        from neo4j_agent_memory.integrations.agentcore import Neo4jMemoryProvider

        provider = Neo4jMemoryProvider(
            memory_client=mock_memory_client,
            namespace="test-ns",
        )

        # Store a memory
        memory = await provider.store_memory(
            session_id="session-1",
            content="Important information",
        )
        assert memory.id == "msg-123"

        # Search memories
        result = await provider.search_memory(query="important")
        assert len(result.memories) >= 1

        # Get session memories
        memories = await provider.get_session_memories("session-1")
        assert len(memories) == 1

        # Delete memory
        deleted = await provider.delete_memory("msg-123")
        assert deleted is True

    @pytest.mark.asyncio
    async def test_provider_context_retrieval(self, mock_memory_client) -> None:
        """Test context retrieval for agent prompts."""
        from neo4j_agent_memory.integrations.agentcore import Neo4jMemoryProvider

        provider = Neo4jMemoryProvider(memory_client=mock_memory_client)

        context = await provider.get_context(
            query="test query",
            session_id="session-1",
        )

        assert context == "Context string"
        mock_memory_client.get_context.assert_called_once()


class TestHybridMemoryProviderIntegration:
    """Integration tests for Hybrid Memory Provider."""

    @pytest.fixture
    def mock_memory_client(self):
        """Create a mock MemoryClient with multi-type results."""
        from datetime import datetime

        client = MagicMock()
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client._client = MagicMock()

        # Mock message
        mock_message = MagicMock()
        mock_message.id = "msg-1"
        mock_message.content = "Recent conversation"
        mock_message.role = MagicMock(value="user")
        mock_message.created_at = datetime.utcnow()
        mock_message.metadata = {"similarity": 0.85}

        # Mock entity
        mock_entity = MagicMock()
        mock_entity.id = "ent-1"
        mock_entity.display_name = "John Doe"
        mock_entity.type = MagicMock(value="PERSON")
        mock_entity.description = "A developer"

        # Mock preference
        mock_preference = MagicMock()
        mock_preference.id = "pref-1"
        mock_preference.preference = "Prefers Python"
        mock_preference.category = "programming"
        mock_preference.context = None
        mock_preference.confidence = 0.9

        client.short_term.search_messages = AsyncMock(return_value=[mock_message])
        client.long_term.search_entities = AsyncMock(return_value=[mock_entity])
        client.long_term.search_preferences = AsyncMock(return_value=[mock_preference])
        # v0.4: HybridMemoryProvider migrated to client.query.cypher (Phase 6).
        client.query.cypher = AsyncMock(return_value=[])

        return client

    @pytest.mark.asyncio
    async def test_hybrid_search_all_types(self, mock_memory_client) -> None:
        """Test hybrid search across all memory types."""
        from neo4j_agent_memory.integrations.agentcore import (
            HybridMemoryProvider,
            RoutingStrategy,
        )

        provider = HybridMemoryProvider(
            memory_client=mock_memory_client,
            routing_strategy=RoutingStrategy.ALL,
        )

        result = await provider.search_memory("test query")

        # Should have results from all three types
        assert len(result.memories) == 3
        memory_types = {m.memory_type.value for m in result.memories}
        assert "message" in memory_types
        assert "entity" in memory_types
        assert "preference" in memory_types

    @pytest.mark.asyncio
    async def test_hybrid_auto_routing_short_term(self, mock_memory_client) -> None:
        """Test auto routing routes to short-term for recent queries."""
        from neo4j_agent_memory.integrations.agentcore import (
            HybridMemoryProvider,
            RoutingStrategy,
        )

        provider = HybridMemoryProvider(
            memory_client=mock_memory_client,
            routing_strategy=RoutingStrategy.AUTO,
        )

        # Short-term query pattern
        result = await provider.search_memory("What did I say earlier?")

        # Should search messages
        mock_memory_client.short_term.search_messages.assert_called()

    @pytest.mark.asyncio
    async def test_hybrid_auto_routing_entities(self, mock_memory_client) -> None:
        """Test auto routing routes to entities for relationship queries."""
        from neo4j_agent_memory.integrations.agentcore import (
            HybridMemoryProvider,
            RoutingStrategy,
        )

        provider = HybridMemoryProvider(
            memory_client=mock_memory_client,
            routing_strategy=RoutingStrategy.AUTO,
        )

        # Entity/relationship query pattern
        result = await provider.search_memory("How is John connected to the project?")

        # Should search entities
        mock_memory_client.long_term.search_entities.assert_called()

    @pytest.mark.asyncio
    async def test_hybrid_entity_relationships(self, mock_memory_client) -> None:
        """Test getting entity relationships."""
        from neo4j_agent_memory.integrations.agentcore import HybridMemoryProvider

        # v0.4: HybridMemoryProvider.get_entity_relationships now uses
        # client.query.cypher (Phase 6 migration).
        mock_memory_client.query.cypher = AsyncMock(
            return_value=[
                {
                    "entity_name": "John Doe",
                    "entity_type": "PERSON",
                    "entity_description": "Developer",
                    "from_entity": "John Doe",
                    "relationship": "WORKS_AT",
                    "to_entity": "Acme Corp",
                }
            ]
        )

        provider = HybridMemoryProvider(memory_client=mock_memory_client)

        result = await provider.get_entity_relationships("John Doe")

        assert result["found"] is True
        assert result["entity"]["name"] == "John Doe"
        assert len(result["relationships"]) == 1


class TestMCPReasoningTraceIntegration:
    """Integration tests for MCP reasoning trace tool via FastMCP Client."""

    @pytest.fixture
    def mock_memory_client(self):
        """Create a mock MemoryClient with proper async mocks."""
        client = MagicMock()
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client.reasoning = MagicMock()
        client.graph = MagicMock()

        # Mock trace object
        mock_trace = MagicMock()
        mock_trace.id = "trace-123"
        mock_trace.task = "Test task"

        # Mock step object
        mock_step = MagicMock()
        mock_step.id = "step-123"

        # All reasoning methods need to be AsyncMock
        client.reasoning.start_trace = AsyncMock(return_value=mock_trace)
        client.reasoning.add_step = AsyncMock(return_value=mock_step)
        client.reasoning.record_tool_call = AsyncMock(return_value=None)
        client.reasoning.complete_trace = AsyncMock(return_value=mock_trace)

        return client

    @pytest.mark.asyncio
    async def test_reasoning_trace_lifecycle(self, mock_memory_client) -> None:
        """Test reasoning trace lifecycle via FastMCP tools (start -> step -> complete)."""
        import json
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock

        from fastmcp import Client, FastMCP

        from neo4j_agent_memory.integration import MemoryIntegration
        from neo4j_agent_memory.mcp._observer import MemoryObserver
        from neo4j_agent_memory.mcp._tools import register_tools

        integration = MagicMock(spec=MemoryIntegration)
        observer = MagicMock(spec=MemoryObserver)

        @asynccontextmanager
        async def mock_lifespan(server):
            yield {
                "client": mock_memory_client,
                "integration": integration,
                "observer": observer,
            }

        mcp = FastMCP("test-reasoning", lifespan=mock_lifespan)
        register_tools(mcp, profile="extended")

        async with Client(mcp) as client:
            # Start trace
            result = await client.call_tool(
                "memory_start_trace",
                {
                    "session_id": "session-1",
                    "task": "Find restaurants nearby",
                },
            )
            data = json.loads(result.content[0].text)
            assert data["started"] is True
            mock_memory_client.reasoning.start_trace.assert_called_once()

            # Complete trace
            result = await client.call_tool(
                "memory_complete_trace",
                {
                    "trace_id": str(mock_memory_client.reasoning.start_trace.return_value.id),
                    "outcome": "Found 5 restaurants",
                    "success": True,
                },
            )
            data = json.loads(result.content[0].text)
            assert data["completed"] is True
            mock_memory_client.reasoning.complete_trace.assert_called_once()


class TestEndToEndWorkflow:
    """End-to-end workflow tests."""

    @pytest.fixture
    def mock_memory_client(self):
        """Create a comprehensive mock MemoryClient."""
        from datetime import datetime

        client = MagicMock()
        client.short_term = MagicMock()
        client.long_term = MagicMock()
        client.reasoning = MagicMock()
        client._client = MagicMock()
        client.get_context = AsyncMock(return_value="Relevant context")

        # Messages
        mock_message = MagicMock()
        mock_message.id = "msg-1"
        mock_message.content = "User message"
        mock_message.role = MagicMock(value="user")
        mock_message.created_at = datetime.utcnow()
        mock_message.metadata = {}

        mock_conversation = MagicMock()
        mock_conversation.messages = [mock_message]

        # Setup all async mocks
        client.short_term.add_message = AsyncMock(return_value=mock_message)
        client.short_term.search_messages = AsyncMock(return_value=[mock_message])
        client.short_term.get_conversation = AsyncMock(return_value=mock_conversation)
        client.long_term.search_entities = AsyncMock(return_value=[])
        client.long_term.search_preferences = AsyncMock(return_value=[])
        client.long_term.add_preference = AsyncMock()
        client._client.execute_write = AsyncMock(return_value=[{"deleted": 1}])
        client._client.execute_read = AsyncMock(return_value=[])

        mock_trace = MagicMock()
        mock_trace.id = "trace-1"
        client.reasoning.add_trace = AsyncMock(return_value=mock_trace)

        return client

    @pytest.mark.asyncio
    async def test_agent_conversation_workflow(self, mock_memory_client) -> None:
        """Test a complete agent conversation workflow."""
        from neo4j_agent_memory.integrations.agentcore import (
            HybridMemoryProvider,
            RoutingStrategy,
        )

        provider = HybridMemoryProvider(
            memory_client=mock_memory_client,
            routing_strategy=RoutingStrategy.AUTO,
        )

        # 1. Store user message
        memory = await provider.store_memory(
            session_id="session-1",
            content="Hi, I'm working on Project Alpha",
        )
        assert memory is not None

        # 2. Search for context
        result = await provider.search_memory(
            query="What project is the user working on?",
            session_id="session-1",
        )
        assert result.memories is not None

        # 3. Get context for LLM
        context = await provider.get_context(
            query="project status",
            session_id="session-1",
        )
        assert context == "Relevant context"

        # 4. Get session history
        memories = await provider.get_session_memories("session-1")
        assert len(memories) >= 1

        # 5. Clear session when done
        deleted = await provider.clear_session("session-1")
        assert deleted >= 0
