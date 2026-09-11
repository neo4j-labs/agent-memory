"""Smoke tests for the ``examples/google_adk_demo`` example.

The example is *executed* here rather than grepped: the helpers that read ADK
``SearchMemoryResponse`` objects are called against real ADK types, the settings
builder is exercised for all three backend paths, and (with Neo4j available)
``main()`` runs the full ``Runner`` loop — using the demo's own scripted
stand-in model, so no Google credentials are required.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
DEMO_DIR = EXAMPLES_DIR / "google_adk_demo"
DEMO_FILE = DEMO_DIR / "demo.py"
MODULE_NAME = "google_adk_demo_demo"

try:
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService  # noqa: F401

    GOOGLE_ADK_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional extra
    GOOGLE_ADK_AVAILABLE = False


def _load_example() -> ModuleType:
    """Import ``examples/google_adk_demo/demo.py`` under a throwaway name."""
    spec = importlib.util.spec_from_file_location(MODULE_NAME, DEMO_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def demo_module(monkeypatch):
    """The example module, with ``.env`` loading and API keys neutralised."""
    for var in ("OPENAI_API_KEY", "MEMORY_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    try:
        module = _load_example()
        # The demo reads examples/.env by design; a developer's real key there
        # must not change what these tests exercise.
        monkeypatch.setattr(module, "load_env", lambda: None)
        yield module
    finally:
        sys.modules.pop(MODULE_NAME, None)


class TestStructure:
    def test_files_exist(self):
        assert DEMO_FILE.exists()
        assert (DEMO_DIR / "README.md").exists()
        assert (DEMO_DIR / ".env.example").exists()

    def test_demo_compiles(self):
        ast.parse(DEMO_FILE.read_text(encoding="utf-8"))

    def test_demo_has_entry_point(self):
        content = DEMO_FILE.read_text(encoding="utf-8")
        assert "async def main(" in content
        assert 'if __name__ == "__main__":' in content

    def test_demo_wires_a_real_adk_runner(self):
        """The point of the example: ADK Runner + load_memory, not just the adapter."""
        content = DEMO_FILE.read_text(encoding="utf-8")
        for needle in (
            "from google.adk.runners import Runner",
            "from google.adk.tools import load_memory",
            "from google.adk.sessions import InMemorySessionService",
            "memory_service=memory_service",
            "async with MemoryClient",
        ):
            assert needle in content, f"demo.py should contain {needle!r}"

    def test_demo_does_not_use_the_phantom_adk_surface(self):
        """``LlmAgent`` has no ``memory=`` field and no ``@agent.tool`` decorator."""
        content = DEMO_FILE.read_text(encoding="utf-8")
        assert "memory=memory_service" not in content
        assert "@agent.tool" not in content

    def test_demo_unwraps_the_search_memory_response(self):
        """search_memory() returns SearchMemoryResponse — callers read .memories."""
        content = DEMO_FILE.read_text(encoding="utf-8")
        assert ".memories" in content
        # ADK MemoryEntry has no .memory_type; it lives in custom_metadata.
        assert ".memory_type" not in content

    def test_env_example_documents_both_backends(self):
        env = (DEMO_DIR / ".env.example").read_text(encoding="utf-8")
        assert "MEMORY_API_KEY" in env
        assert "OPENAI_API_KEY" in env
        assert "your-password-here" in env

    def test_readme_has_labs_conventions(self):
        readme = (DEMO_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j Labs" in readme
        assert "Neo4j Labs Project" in readme
        assert "Verified against" in readme
        assert "Support" in readme


class TestSettings:
    def test_local_path_needs_no_api_key(self, demo_module, monkeypatch):
        pytest.importorskip("sentence_transformers")
        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_PASSWORD", "test-password")

        settings = demo_module.build_settings()
        assert settings.backend == "bolt"
        assert settings.llm is None
        assert settings.extraction.enable_llm_fallback is False

    def test_openai_path_uses_env_driven_model_ids(self, demo_module, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
        monkeypatch.setenv("NEO4J_PASSWORD", "test-password")

        settings = demo_module.build_settings()
        assert settings.backend == "bolt"
        # No retired ids anywhere in the resolved configuration.
        rendered = f"{settings.llm} {settings.embedding}"
        assert "gpt-4o" not in rendered
        assert "text-embedding-004" not in rendered

    def test_memory_api_key_selects_the_hosted_backend(self, demo_module, monkeypatch):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_not_a_real_key")
        settings = demo_module.build_settings()
        assert settings.backend == "nams"

    def test_extraction_downgrades_instead_of_failing(self, demo_module):
        """A missing local extractor must not raise at settings-build time."""
        config = demo_module._local_extraction()
        assert config.enable_llm_fallback is False


@pytest.mark.skipif(not GOOGLE_ADK_AVAILABLE, reason="google-adk not installed")
class TestADKHelpers:
    def test_installed_google_adk_is_2x(self):
        from importlib.metadata import version

        assert int(version("google-adk").split(".")[0]) == 2

    def test_memory_service_implements_the_2x_contract(self):
        from google.adk.memory import BaseMemoryService

        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        assert issubclass(Neo4jMemoryService, BaseMemoryService)
        for name in (
            "add_session_to_memory",
            "add_events_to_memory",
            "add_memory",
            "search_memory",
        ):
            assert hasattr(Neo4jMemoryService, name)

    def test_entry_text_reads_adk_content_parts(self, demo_module):
        from google.adk.memory.memory_entry import MemoryEntry
        from google.genai import types

        entry = MemoryEntry(
            content=types.Content(
                role="user", parts=[types.Part(text="hello "), types.Part(text="world")]
            ),
            author="user",
            custom_metadata={"memory_type": "message"},
        )
        assert demo_module.entry_text(entry) == "hello world"
        assert demo_module.describe(entry) == "[message/user] hello world"

    def test_build_model_falls_back_to_a_scripted_model(self, demo_module):
        from google.adk.models.base_llm import BaseLlm

        model = demo_module.build_model()
        assert isinstance(model, BaseLlm)

    def test_build_model_uses_the_env_model_id_when_a_key_is_present(
        self, demo_module, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_API_KEY", "not-a-real-key")
        monkeypatch.setenv("ADK_MODEL", "gemini-2.5-pro")
        assert demo_module.build_model() == "gemini-2.5-pro"

    def test_default_gemini_model_is_current(self, demo_module):
        assert demo_module.DEFAULT_GEMINI_MODEL.startswith("gemini-2.5")

    def test_build_runner_accepts_the_memory_service(self, demo_module):
        from unittest.mock import MagicMock

        from google.adk.runners import Runner

        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        service = Neo4jMemoryService(memory_client=MagicMock(), user_id="u1")
        runner, session_service = demo_module.build_runner(service)
        try:
            assert isinstance(runner, Runner)
            assert runner.memory_service is service
            assert any(tool.name == "load_memory" for tool in runner.agent.tools)
        finally:
            asyncio.run(runner.close())


@pytest.mark.skipif(not GOOGLE_ADK_AVAILABLE, reason="google-adk not installed")
@pytest.mark.requires_neo4j
@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="set NEO4J_URI to run the example end-to-end (the quick CI job has no database)",
)
class TestEndToEnd:
    def test_demo_runs_the_full_adk_loop(self, demo_module, monkeypatch):
        """Run main() against Neo4j with the scripted model and assert recall."""
        pytest.importorskip("sentence_transformers")
        spacy = pytest.importorskip("spacy")
        pytest.importorskip("gliner")
        if not spacy.util.is_package("en_core_web_sm"):
            pytest.skip("en_core_web_sm not installed")

        from neo4j_agent_memory import MemoryClient
        from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

        monkeypatch.setenv("NEO4J_URI", os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
        monkeypatch.setenv("NEO4J_USERNAME", os.environ.get("NEO4J_USERNAME", "neo4j"))
        monkeypatch.setenv("NEO4J_PASSWORD", os.environ.get("NEO4J_PASSWORD", "test-password"))

        asyncio.run(demo_module.main(use_agent=True))

        async def check() -> tuple[int, int]:
            async with MemoryClient(demo_module.build_settings()) as client:
                rows = await client.query.cypher(
                    "MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message) "
                    "WHERE c.session_id STARTS WITH 'adk-demo-' RETURN count(m) AS n"
                )
                service = Neo4jMemoryService(memory_client=client)
                response = await service.search_memory(query="Project Alpha", limit=5)
                return int(rows[0]["n"]), len(response.memories)

        messages, memories = asyncio.run(check())
        assert messages >= 2, "the ADK session should have been written to Neo4j"
        assert memories >= 1, "search_memory() should return entries from response.memories"
