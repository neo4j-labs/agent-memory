"""Smoke tests for the ``examples/google_cloud_integration`` scripts.

Three layers:

* structure + anti-regression greps (no imports, no database);
* executable tests that import the scripts and call their pure helpers — the
  settings builder, the FastMCP result reader, the ADK response flattener — plus
  the live FastMCP server that pins the exact 6/16 tool surface;
* Neo4j-backed runs of ``adk_memory_service.main()`` and
  ``full_pipeline.main()`` (``requires_neo4j``), which is what would have caught
  every crash the review found.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
INTEGRATION_DIR = EXAMPLES_DIR / "google_cloud_integration"

SCRIPTS = [
    "vertex_ai_embeddings.py",
    "adk_memory_service.py",
    "mcp_server_demo.py",
    "full_pipeline.py",
    "_common.py",
]

try:
    from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder  # noqa: F401

    VERTEX_AI_AVAILABLE = True
except ImportError:  # pragma: no cover
    VERTEX_AI_AVAILABLE = False

try:
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService  # noqa: F401

    GOOGLE_ADK_AVAILABLE = True
except ImportError:  # pragma: no cover
    GOOGLE_ADK_AVAILABLE = False

try:
    from neo4j_agent_memory.mcp.server import Neo4jMemoryMCPServer  # noqa: F401

    MCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    MCP_AVAILABLE = False


def _load(script: str) -> ModuleType:
    """Import one example script (they import the sibling ``_common``)."""
    name = f"gci_{script.removesuffix('.py')}"
    added = str(INTEGRATION_DIR) not in sys.path
    if added:
        sys.path.insert(0, str(INTEGRATION_DIR))
    spec = importlib.util.spec_from_file_location(name, INTEGRATION_DIR / script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_env(monkeypatch):
    """No API keys, and no ``.env`` side effects from the developer's machine."""
    for var in ("OPENAI_API_KEY", "MEMORY_API_KEY", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NEO4J_URI", os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    monkeypatch.setenv("NEO4J_USERNAME", os.environ.get("NEO4J_USERNAME", "neo4j"))
    monkeypatch.setenv("NEO4J_PASSWORD", os.environ.get("NEO4J_PASSWORD", "test-password"))
    yield
    for name in list(sys.modules):
        if name.startswith("gci_"):
            del sys.modules[name]


class TestStructure:
    def test_directory_and_docs_exist(self):
        assert INTEGRATION_DIR.exists()
        assert (INTEGRATION_DIR / "README.md").exists()
        assert (INTEGRATION_DIR / ".env.example").exists()

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_script_is_valid_python_with_a_docstring(self, script):
        source = (INTEGRATION_DIR / script).read_text(encoding="utf-8")
        ast.parse(source)
        assert source.lstrip().startswith(('"""', "#!/usr/bin/env python"))

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_no_retired_or_phantom_api(self, script):
        """Guards for the exact defects the review found.

        These look for the retired id / phantom API **as code** — the scripts are
        allowed (and expected) to name ``text-embedding-004`` in a prose note
        saying it was shut down.
        """
        source = (INTEGRATION_DIR / script).read_text(encoding="utf-8")
        for retired in ("text-embedding-004", "textembedding-gecko@003"):
            assert f'"{retired}"' not in source, f"{retired} used as a model value"
            assert f"'{retired}'" not in source, f"{retired} used as a model value"
        # add_message has no user_id= parameter — multi-tenancy is user_identifier=.
        assert 'user_id="demo-user"' not in source
        # MemoryIntegration has no .session_id attribute.
        assert "memory.session_id" not in source
        # LlmAgent has no memory= field and no @agent.tool decorator.
        assert "memory=memory_service" not in source
        assert "@agent.tool" not in source
        # search_memory returns SearchMemoryResponse; entries have no memory_type.
        assert "for entry in results" not in source
        # FastMCP 4: read result.data, not content[0].text.
        assert "json.loads(result.content[0].text)" not in source
        # SSE is deprecated for network transport.
        assert '"--transport", "sse"' not in source

    def test_readme_has_labs_conventions_and_no_broken_image(self):
        readme = (INTEGRATION_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j Labs Project" in readme
        assert "Verified against" in readme
        assert "Support" in readme
        # The PNG was never committed; only the .excalidraw source exists.
        assert "img/architecture.png" not in readme
        assert "img/architecture.excalidraw" in readme
        # Tool counts must match the live server surface asserted below.
        assert "16" in readme and "6 core" in readme
        # The docker tag CI uses, not 5-enterprise (which needs a licence env var).
        assert "neo4j:5.26-community" in readme

    def test_env_example_has_placeholders_not_literals(self):
        env = (INTEGRATION_DIR / ".env.example").read_text(encoding="utf-8")
        assert "NEO4J_PASSWORD=your-password-here" in env
        assert "MEMORY_API_KEY" in env
        assert "OPENAI_API_KEY" in env
        assert "gemini-embedding-001" in env


class TestImports:
    def test_memory_client_importable(self):
        from neo4j_agent_memory import MemoryClient

        assert MemoryClient is not None

    @pytest.mark.skipif(not VERTEX_AI_AVAILABLE, reason="vertex-ai not installed")
    def test_vertex_ai_embedder_defaults(self):
        from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

        embedder = VertexAIEmbedder()
        assert embedder.model == "gemini-embedding-001"
        assert embedder.task_type == "RETRIEVAL_DOCUMENT"
        # 768 by default (truncated from the model's native 3072) so vector
        # indexes sized for the retired text-embedding-004 keep working.
        assert embedder.dimensions == 768
        assert embedder.output_dimensionality == 768

    @pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")
    def test_mcp_registration_helpers_importable(self):
        from fastmcp import FastMCP

        from neo4j_agent_memory.mcp._tools import register_tools

        register_tools(FastMCP("test"), profile="core")  # should not raise


class TestSharedHelpers:
    def test_build_settings_local_path(self, clean_env):
        pytest.importorskip("sentence_transformers")
        common = _load("_common.py")
        settings = common.build_settings()
        assert settings.backend == "bolt"
        assert settings.llm is None
        assert settings.extraction.enable_llm_fallback is False

    def test_build_settings_hosted_path(self, clean_env, monkeypatch):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_not_a_real_key")
        common = _load("_common.py")
        assert common.build_settings().backend == "nams"

    def test_build_settings_multi_tenant_is_opt_in(self, clean_env):
        pytest.importorskip("sentence_transformers")
        common = _load("_common.py")
        assert common.build_settings().memory.multi_tenant is False
        assert common.build_settings(multi_tenant=True).memory.multi_tenant is True

    def test_build_settings_accepts_an_embedding_override(self, clean_env):
        pytest.importorskip("sentence_transformers")
        common = _load("_common.py")
        settings = common.build_settings("sentence-transformers/all-MiniLM-L6-v2")
        assert settings.embedding is not None

    def test_vertex_model_default_is_current(self, clean_env):
        common = _load("_common.py")
        assert common.VERTEX_EMBEDDING_MODEL == "gemini-embedding-001"

    def test_describe_extractor_names_pipeline_stages(self, clean_env):
        common = _load("_common.py")
        extractor = SimpleNamespace(stages=[SimpleNamespace(name="SpacyEntityExtractor")])
        assert "SpacyEntityExtractor" in common.describe_extractor(extractor)


class TestScriptHelpers:
    def test_tool_payload_reads_result_data(self, clean_env):
        """Tool results are double-encoded: result.data is a JSON string."""
        pytest.importorskip("fastmcp")
        pipeline = _load("full_pipeline.py")
        result = SimpleNamespace(data='{"row_count": 2, "rows": [1, 2]}')
        assert pipeline.tool_payload(result) == {"row_count": 2, "rows": [1, 2]}

    @pytest.mark.skipif(not GOOGLE_ADK_AVAILABLE, reason="google-adk not installed")
    def test_adk_entries_flattens_a_search_memory_response(self, clean_env):
        from google.adk.memory.base_memory_service import SearchMemoryResponse
        from google.adk.memory.memory_entry import MemoryEntry
        from google.genai import types

        pipeline = _load("full_pipeline.py")
        response = SearchMemoryResponse(
            memories=[
                MemoryEntry(
                    content=types.Content(role="user", parts=[types.Part(text="hi there")]),
                    author="user",
                    custom_metadata={"memory_type": "message"},
                )
            ]
        )
        assert pipeline.adk_entries(response) == [("message", "hi there")]

    @pytest.mark.skipif(not GOOGLE_ADK_AVAILABLE, reason="google-adk not installed")
    def test_adk_script_entries_helper(self, clean_env):
        from google.adk.memory.base_memory_service import SearchMemoryResponse
        from google.adk.memory.memory_entry import MemoryEntry
        from google.genai import types

        adk = _load("adk_memory_service.py")
        response = SearchMemoryResponse(
            memories=[
                MemoryEntry(
                    content=types.Content(role="model", parts=[types.Part(text="answer")]),
                    author="agent-name",
                    custom_metadata={"memory_type": "entity"},
                )
            ]
        )
        assert adk.entries(response) == [("entity", "agent-name", "answer")]

    @pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")
    def test_mcp_demo_parses_its_flags(self, clean_env):
        demo = _load("mcp_server_demo.py")
        args = demo.parse_args(["--schemas", "--no-tools"])
        assert args.schemas is True
        assert args.no_tools is True


@pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")
class TestMCPToolSurface:
    """Pins the exact tool surface — the strongest regression guard here."""

    @staticmethod
    def _tool_names(profile: str) -> set[str]:
        from fastmcp import Client, FastMCP

        from neo4j_agent_memory.mcp._tools import register_tools

        mcp = FastMCP("test")
        register_tools(mcp, profile=profile)

        async def _check() -> set[str]:
            async with Client(mcp) as client:
                return {tool.name for tool in await client.list_tools()}

        return asyncio.run(_check())

    def test_core_profile(self):
        names = self._tool_names("core")
        assert names == {
            "memory_search",
            "memory_get_context",
            "memory_store_message",
            "memory_add_entity",
            "memory_add_preference",
            "memory_add_fact",
        }
        assert len(names) == 6

    def test_extended_profile(self):
        names = self._tool_names("extended")
        assert names == {
            "memory_search",
            "memory_get_context",
            "memory_store_message",
            "memory_add_entity",
            "memory_add_preference",
            "memory_add_fact",
            "memory_get_conversation",
            "memory_list_sessions",
            "memory_get_entity",
            "memory_export_graph",
            "memory_create_relationship",
            "memory_start_trace",
            "memory_record_step",
            "memory_complete_trace",
            "memory_get_observations",
            "graph_query",
        }
        assert len(names) == 16


@pytest.mark.requires_neo4j
@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="set NEO4J_URI to run the scripts end-to-end (the quick CI job has no database)",
)
class TestEndToEnd:
    """Run the scripts for real — the layer that catches crashes."""

    @staticmethod
    def _require_local_stack() -> None:
        pytest.importorskip("sentence_transformers")
        spacy = pytest.importorskip("spacy")
        if not spacy.util.is_package("en_core_web_sm"):
            pytest.skip("en_core_web_sm not installed")

    @pytest.mark.skipif(not GOOGLE_ADK_AVAILABLE, reason="google-adk not installed")
    def test_adk_memory_service_script_runs(self, clean_env):
        self._require_local_stack()
        adk = _load("adk_memory_service.py")
        asyncio.run(adk.main())

    @pytest.mark.skipif(not MCP_AVAILABLE, reason="mcp not installed")
    def test_full_pipeline_runs_and_writes_audit_edges(self, clean_env):
        self._require_local_stack()

        from neo4j_agent_memory import MemoryClient

        pipeline = _load("full_pipeline.py")
        asyncio.run(pipeline.main())

        async def audit_rows() -> int:
            async with MemoryClient(pipeline.build_settings()) as client:
                rows = await client.query.cypher(
                    pipeline.AUDIT_QUERY, {"name": pipeline.AUDIT_ENTITY}
                )
                return len(rows)

        assert asyncio.run(audit_rows()) >= 1, (
            "phase 4 should have written (:ReasoningStep)-[:TOUCHED]->(:Entity) edges"
        )
