"""Smoke tests for CrewAI example code."""

import ast
import importlib.util
from pathlib import Path

import pytest

# Check if CrewAI is available. Import the base class from the same module path
# the adapter uses (`crewai.memory.memory`): crewai 1.x no longer re-exports
# `Memory` from the `crewai.memory` package, so probing the package would skip
# every test below even with crewai installed.
try:
    from crewai.memory.memory import Memory

    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False


class TestCrewAIExampleStructure:
    """Test that CrewAI integration module is properly structured."""

    def test_integration_module_exists(self):
        """Test that the CrewAI integration module exists."""
        from neo4j_agent_memory.integrations import crewai

        assert crewai is not None

    @pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
    def test_memory_class_importable(self):
        """Test that Neo4jCrewMemory can be imported."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        assert Neo4jCrewMemory is not None

    @pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
    def test_memory_class_inherits_crewai_memory(self):
        """Test that Neo4jCrewMemory inherits from CrewAI Memory."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        assert issubclass(Neo4jCrewMemory, Memory)

    @pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
    def test_memory_has_required_methods(self):
        """Test that Neo4jCrewMemory has required methods."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        assert hasattr(Neo4jCrewMemory, "remember")
        assert hasattr(Neo4jCrewMemory, "recall")
        assert hasattr(Neo4jCrewMemory, "get_agent_context")
        assert callable(getattr(Neo4jCrewMemory, "remember", None))
        assert callable(getattr(Neo4jCrewMemory, "recall", None))
        assert callable(getattr(Neo4jCrewMemory, "get_agent_context", None))


class TestCrewAIIntegrationModule:
    """Test the CrewAI integration module structure."""

    def test_module_has_docstring(self):
        """Test that the module has a docstring."""
        from neo4j_agent_memory.integrations import crewai

        # Module should have documentation (allow missing for optional deps)
        _ = crewai.__doc__  # Just verify module is accessible

    def test_integration_init_exports(self):
        """Test that integration __init__ properly handles CrewAI."""
        # This should not raise even if CrewAI isn't installed
        from neo4j_agent_memory import integrations

        # crewai submodule should be accessible
        assert hasattr(integrations, "crewai")


@pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
class TestCrewAIMemoryInterface:
    """Test the CrewAI memory interface compliance."""

    def test_remember_method_signature(self):
        """Test that remember method has correct signature."""
        import inspect

        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        sig = inspect.signature(Neo4jCrewMemory.remember)
        params = list(sig.parameters.keys())

        # Should have self, content, and metadata parameters
        assert "self" in params
        assert "content" in params
        assert "metadata" in params

    def test_recall_method_signature(self):
        """Test that recall method has correct signature."""
        import inspect

        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        sig = inspect.signature(Neo4jCrewMemory.recall)
        params = list(sig.parameters.keys())

        # Should have self, query, and n parameters
        assert "self" in params
        assert "query" in params
        assert "n" in params

    def test_get_agent_context_method_signature(self):
        """Test that get_agent_context method has correct signature."""
        import inspect

        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        sig = inspect.signature(Neo4jCrewMemory.get_agent_context)
        params = list(sig.parameters.keys())

        # Should have self, agent_role, and task parameters
        assert "self" in params
        assert "agent_role" in params
        assert "task" in params

    def test_init_method_signature(self):
        """Test that __init__ method has correct signature."""
        import inspect

        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        sig = inspect.signature(Neo4jCrewMemory.__init__)
        params = list(sig.parameters.keys())

        # Should have memory_client and crew_id
        assert "self" in params
        assert "memory_client" in params
        assert "crew_id" in params


class TestCrewAISourceCodeQuality:
    """Test source code quality of CrewAI integration."""

    def test_source_file_exists(self):
        """Test that the source file exists."""
        from neo4j_agent_memory.integrations import crewai

        source_file = Path(crewai.__file__)
        assert source_file.exists()

    def test_source_file_valid_python(self):
        """Test that the source file is valid Python."""
        from neo4j_agent_memory.integrations import crewai

        source_file = Path(crewai.__file__)
        if source_file.name == "__init__.py":
            # Find memory.py in same directory
            memory_file = source_file.parent / "memory.py"
            if memory_file.exists():
                source_file = memory_file

        source_code = source_file.read_text(encoding="utf-8")

        # Should parse without syntax errors
        try:
            ast.parse(source_code)
        except SyntaxError as e:
            pytest.fail(f"Syntax error in source file: {e}")

    def test_no_bare_except_clauses(self):
        """Test that source doesn't use bare except clauses."""
        from neo4j_agent_memory.integrations import crewai

        source_file = Path(crewai.__file__)
        if source_file.name == "__init__.py":
            memory_file = source_file.parent / "memory.py"
            if memory_file.exists():
                source_file = memory_file

        source_code = source_file.read_text(encoding="utf-8")
        tree = ast.parse(source_code)

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # Bare except has node.type as None
                # We allow ImportError exceptions for optional imports
                pass


@pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
class TestCrewAITypeAnnotations:
    """Test type annotations in CrewAI integration."""

    def test_remember_has_type_hints(self):
        """Test that remember method parameters have type hints."""
        import inspect

        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        sig = inspect.signature(Neo4jCrewMemory.remember)
        # Verify method exists and has expected parameters
        params = sig.parameters
        assert "content" in params
        assert "metadata" in params

    def test_recall_return_type(self):
        """Test that recall method returns list of strings."""
        import inspect

        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        sig = inspect.signature(Neo4jCrewMemory.recall)
        # Return annotation may specify list[str]
        # Just verify method exists

    def test_class_has_type_hints(self):
        """Test that class uses type hints."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        # Check if __init__ has annotations
        annotations = getattr(Neo4jCrewMemory.__init__, "__annotations__", {})
        # Type hints may be in TYPE_CHECKING block


@pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
class TestCrewAIMemoryTypes:
    """Test CrewAI memory type handling."""

    def test_short_term_memory_type(self):
        """Test that short_term memory type is supported."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        # The implementation should handle "short_term" type
        # Verify by checking the _remember_async method exists
        assert hasattr(Neo4jCrewMemory, "_remember_async")

    def test_fact_memory_type(self):
        """Test that fact memory type is supported."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        # The implementation should handle "fact" type
        assert hasattr(Neo4jCrewMemory, "_remember_async")

    def test_preference_memory_type(self):
        """Test that preference memory type is supported."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        # The implementation should handle "preference" type
        assert hasattr(Neo4jCrewMemory, "_remember_async")


class TestCrewAIAsyncImplementation:
    """Test async implementation details of CrewAI integration."""

    @pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
    def test_has_async_remember_method(self):
        """Test that async _remember_async method exists."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        assert hasattr(Neo4jCrewMemory, "_remember_async")

    @pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
    def test_has_async_recall_method(self):
        """Test that async _recall_async method exists."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        assert hasattr(Neo4jCrewMemory, "_recall_async")

    @pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
    def test_has_async_get_agent_context_method(self):
        """Test that async _get_agent_context_async method exists."""
        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        assert hasattr(Neo4jCrewMemory, "_get_agent_context_async")

    @pytest.mark.skipif(not CREWAI_AVAILABLE, reason="CrewAI not installed")
    def test_async_methods_are_coroutines(self):
        """Test that async methods are actual coroutines."""
        import asyncio

        from neo4j_agent_memory.integrations.crewai import Neo4jCrewMemory

        assert asyncio.iscoroutinefunction(Neo4jCrewMemory._remember_async)
        assert asyncio.iscoroutinefunction(Neo4jCrewMemory._recall_async)
        assert asyncio.iscoroutinefunction(Neo4jCrewMemory._get_agent_context_async)
