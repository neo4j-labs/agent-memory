"""Smoke tests for pydantic_ai_agent.py example.

This example requires Neo4j and Pydantic AI integration.
"""

from pathlib import Path
from uuid import uuid4

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


@pytest.mark.requires_neo4j
class TestPydanticAIAgentExample:
    """Smoke tests for the Pydantic AI agent example."""

    def test_example_file_exists(self, examples_dir):
        """Verify the example file exists."""
        example_path = examples_dir / "pydantic_ai_agent.py"
        assert example_path.exists(), f"Example file not found: {example_path}"

    def test_pydantic_ai_integration_importable(self):
        """Verify the Pydantic AI integration module is importable."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import (
                MemoryDependency,
                create_memory_tools,
                record_agent_trace,
            )

            assert MemoryDependency is not None
            assert create_memory_tools is not None
            assert record_agent_trace is not None
        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_dependency_initialization(self, memory_client):
        """Test MemoryDependency can be initialized."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

            session_id = f"test-pydantic-{uuid4()}"

            deps = MemoryDependency(client=memory_client, session_id=session_id)

            assert deps is not None
            assert deps.session_id == session_id
            assert deps.client == memory_client

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_dependency_get_context(self, memory_client):
        """Test getting context through MemoryDependency."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

            session_id = f"test-pydantic-ctx-{uuid4()}"

            # Pre-populate some data
            await memory_client.long_term.add_preference(
                "communication", "Prefers concise responses"
            )

            deps = MemoryDependency(client=memory_client, session_id=session_id)

            # Get context
            context = await deps.get_context("restaurant recommendation")

            assert isinstance(context, str)

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_dependency_add_preference(self, memory_client):
        """Test adding preference through MemoryDependency."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

            session_id = f"test-pydantic-pref-{uuid4()}"

            deps = MemoryDependency(client=memory_client, session_id=session_id)

            # Add preference
            await deps.add_preference(
                category="location",
                preference="Prefers downtown area",
            )

            # Verify it was saved
            prefs = await memory_client.long_term.search_preferences("downtown")
            assert len(prefs) >= 1

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_dependency_search_preferences(self, memory_client):
        """Test searching preferences through MemoryDependency."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import MemoryDependency

            session_id = f"test-pydantic-search-{uuid4()}"

            # Pre-populate
            await memory_client.long_term.add_preference("food", "Vegetarian, loves Indian cuisine")

            deps = MemoryDependency(client=memory_client, session_id=session_id)

            # Search
            prefs = await deps.search_preferences("food")

            assert isinstance(prefs, list)

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_create_memory_tools(self, memory_client):
        """Test creating memory tools."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

            tools = create_memory_tools(memory_client)

            assert isinstance(tools, list)
            assert len(tools) >= 3  # search, save_preference, recall

            # Check tool names
            tool_names = [t.__name__ for t in tools]
            assert "search_memory" in tool_names
            assert "save_preference" in tool_names
            assert "recall_preferences" in tool_names

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    @pytest.mark.asyncio
    async def test_memory_tools_execution(self, memory_client):
        """Test executing memory tools."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import create_memory_tools

            tools = create_memory_tools(memory_client)

            # Find tools by name
            search_tool = next(t for t in tools if t.__name__ == "search_memory")
            save_tool = next(t for t in tools if t.__name__ == "save_preference")
            recall_tool = next(t for t in tools if t.__name__ == "recall_preferences")

            # Test save_preference
            save_result = await save_tool("cuisine", "Enjoys Mediterranean food")
            assert isinstance(save_result, str)

            # Test search_memory
            search_result = await search_tool("Mediterranean food")
            assert isinstance(search_result, str)

            # Test recall_preferences
            recall_result = await recall_tool("cuisine")
            assert isinstance(recall_result, str)

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    def test_record_agent_trace_available(self):
        """Verify record_agent_trace function is available."""
        try:
            from neo4j_agent_memory.integrations.pydantic_ai import record_agent_trace

            # Just verify it's callable
            assert callable(record_agent_trace)

        except ImportError:
            pytest.skip("Pydantic AI integration not installed")

    def test_example_sections_present(self, examples_dir):
        """Verify the example covers all documented sections."""
        example_path = examples_dir / "pydantic_ai_agent.py"
        content = example_path.read_text(encoding="utf-8")

        # Check for key sections
        assert "MemoryDependency" in content
        assert "create_memory_tools" in content
        assert "get_context" in content
        assert "add_preference" in content
        assert "search_preferences" in content
        assert "record_agent_trace" in content

    def test_example_has_proper_structure(self, examples_dir):
        """Verify the example has proper Python structure."""
        example_path = examples_dir / "pydantic_ai_agent.py"
        content = example_path.read_text(encoding="utf-8")

        # Check for main function and entry point
        assert "async def main(" in content
        assert 'if __name__ == "__main__":' in content
        assert "asyncio.run(main())" in content

        # Check for proper imports
        assert "from neo4j_agent_memory" in content
        assert "from neo4j_agent_memory.integrations.pydantic_ai" in content
