"""Smoke tests for langchain_agent.py example.

This example requires Neo4j and LangChain integration.
"""

from pathlib import Path
from uuid import uuid4

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


@pytest.mark.requires_neo4j
class TestLangchainAgentExample:
    """Smoke tests for the LangChain agent example."""

    def test_example_file_exists(self, examples_dir):
        """Verify the example file exists."""
        example_path = examples_dir / "langchain_agent.py"
        assert example_path.exists(), f"Example file not found: {example_path}"

    def test_langchain_integration_importable(self):
        """Verify the LangChain integration module is importable."""
        try:
            from neo4j_agent_memory.integrations.langchain import (
                Neo4jAgentMemory,
                Neo4jMemoryRetriever,
            )

            assert Neo4jAgentMemory is not None
            assert Neo4jMemoryRetriever is not None
        except ImportError:
            pytest.skip("LangChain integration not installed")

    @pytest.mark.asyncio
    async def test_neo4j_agent_memory_initialization(self, memory_client):
        """Test Neo4jAgentMemory can be initialized."""
        try:
            from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

            session_id = f"test-langchain-{uuid4()}"

            memory = Neo4jAgentMemory(
                memory_client=memory_client,
                session_id=session_id,
                include_short_term=True,
                include_long_term=True,
                include_reasoning=True,
            )

            assert memory is not None
            assert memory.session_id == session_id

        except ImportError:
            pytest.skip("LangChain integration not installed")

    @pytest.mark.asyncio
    async def test_neo4j_agent_memory_load_variables(self, memory_client):
        """Test loading memory variables."""
        try:
            from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

            session_id = f"test-langchain-load-{uuid4()}"

            # Pre-populate some data
            await memory_client.short_term.add_message(session_id, "user", "I prefer spicy food")
            await memory_client.long_term.add_preference(
                "food", "Loves spicy dishes", context="Dining preferences"
            )

            memory = Neo4jAgentMemory(
                memory_client=memory_client,
                session_id=session_id,
            )

            # Load memory variables
            variables = await memory._load_memory_variables_async(
                {"input": "restaurant recommendation"}
            )

            assert isinstance(variables, dict)
            # Should have some memory key
            assert len(variables) > 0

        except ImportError:
            pytest.skip("LangChain integration not installed")

    @pytest.mark.asyncio
    async def test_neo4j_agent_memory_save_context(self, memory_client):
        """Test saving context to memory."""
        try:
            from neo4j_agent_memory.integrations.langchain import Neo4jAgentMemory

            session_id = f"test-langchain-save-{uuid4()}"

            memory = Neo4jAgentMemory(
                memory_client=memory_client,
                session_id=session_id,
            )

            # Save new context
            await memory._save_context_async(
                {"input": "What's a good Thai restaurant?"},
                {"output": "I recommend Thai Kitchen!"},
            )

            # Verify it was saved
            conversation = await memory_client.short_term.get_conversation(session_id)
            assert len(conversation.messages) == 2

        except ImportError:
            pytest.skip("LangChain integration not installed")

    @pytest.mark.asyncio
    async def test_neo4j_memory_retriever_initialization(self, memory_client):
        """Test Neo4jMemoryRetriever can be initialized."""
        try:
            from neo4j_agent_memory.integrations.langchain import Neo4jMemoryRetriever

            retriever = Neo4jMemoryRetriever(
                memory_client=memory_client,
                search_short_term=True,
                search_long_term=True,
                k=5,
            )

            assert retriever is not None
            assert retriever.k == 5

        except ImportError:
            pytest.skip("LangChain integration not installed")

    @pytest.mark.asyncio
    async def test_neo4j_memory_retriever_search(self, memory_client):
        """Test retrieving documents from memory."""
        try:
            from neo4j_agent_memory.integrations.langchain import Neo4jMemoryRetriever

            session_id = f"test-langchain-retrieve-{uuid4()}"

            # Pre-populate data
            await memory_client.short_term.add_message(session_id, "user", "I love spicy Thai food")
            await memory_client.long_term.add_preference("food", "Prefers spicy dishes")

            retriever = Neo4jMemoryRetriever(
                memory_client=memory_client,
                search_short_term=True,
                search_long_term=True,
                k=5,
            )

            # Retrieve documents
            docs = await retriever._get_relevant_documents_async("spicy food")

            assert isinstance(docs, list)
            # May or may not find documents depending on embedding similarity

        except ImportError:
            pytest.skip("LangChain integration not installed")

    def test_example_sections_present(self, examples_dir):
        """Verify the example covers all documented sections."""
        example_path = examples_dir / "langchain_agent.py"
        content = example_path.read_text(encoding="utf-8")

        # Check for key sections
        assert "Neo4jAgentMemory" in content
        assert "Neo4jMemoryRetriever" in content
        assert "_load_memory_variables_async" in content
        assert "_save_context_async" in content
        assert "_get_relevant_documents_async" in content

    def test_example_has_proper_structure(self, examples_dir):
        """Verify the example has proper Python structure."""
        example_path = examples_dir / "langchain_agent.py"
        content = example_path.read_text(encoding="utf-8")

        # Check for main function and entry point
        assert "async def main(" in content
        assert 'if __name__ == "__main__":' in content
        assert "asyncio.run(main())" in content

        # Check for proper imports
        assert "from neo4j_agent_memory" in content
