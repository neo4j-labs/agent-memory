"""Integration tests for running MemoryClient with no LLM provider.

Validates the optional-LLM path end-to-end:

- T5: a freshly constructed MemoryClient with `llm=None` and a non-OpenAI
  embedder must not import the `openai` module along the LLM-extraction path.
  We assert this in a subprocess to avoid false positives from other tests
  that may have already imported `openai` in the parent process.
- T6: `get_context` works end-to-end with `llm=None`. Today none of the
  `get_context` paths call an LLM, so this test documents the current
  behavior. If reasoning summarization is added later, that feature will
  gain its own guard and a corresponding test.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from neo4j import GraphDatabase
from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
)
from neo4j_agent_memory.graph.schema import SchemaManager

# Names of the managed vector indexes that get created with the configured
# embedder dimension. Earlier tests in the same pytest session create these
# at 1536 dims (mock embedder / OpenAI default); without dropping them, the
# 384-dim sentence-transformers configuration used below trips the
# EmbeddingDimensionMismatchError guard in MemoryClient.connect().
_MANAGED_VECTOR_INDEX_NAMES = tuple(name for name, _, _ in SchemaManager._MANAGED_VECTOR_INDEXES)


def _build_settings(neo4j_connection_info) -> MemorySettings:
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=neo4j_connection_info["uri"],
            username=neo4j_connection_info["username"],
            password=SecretStr(neo4j_connection_info["password"]),
        ),
        llm=None,
        embedding=EmbeddingConfig(
            provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
            model="all-MiniLM-L6-v2",
            dimensions=384,
        ),
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.NONE,
            enable_llm_fallback=False,
        ),
    )


@pytest.fixture
def _reset_vector_indexes(neo4j_connection_info):
    """Drop managed vector indexes so each no-LLM test can recreate them.

    The session-scoped Neo4j testcontainer is shared across all integration
    tests. Earlier tests create vector indexes sized for a 1536-dim embedder;
    the no-LLM tests configure a 384-dim sentence-transformers embedder, and
    MemoryClient.connect() refuses to attach to indexes with a mismatched
    dimension. Dropping the managed indexes before each test in this class
    lets them be re-created at whatever dimension this test expects.

    Uses the sync driver so the fixture works for both async and sync tests
    in this class.
    """

    def _drop_indexes() -> None:
        with driver.session() as session:
            for name in _MANAGED_VECTOR_INDEX_NAMES:
                session.run(f"DROP INDEX {name} IF EXISTS")

    driver = GraphDatabase.driver(
        neo4j_connection_info["uri"],
        auth=(neo4j_connection_info["username"], neo4j_connection_info["password"]),
    )
    try:
        # Drop before so this test can recreate at its expected dim.
        _drop_indexes()
        yield
        # Drop after too so the next test in the session — which likely
        # uses a 1536-dim mock embedder — isn't blocked by 384-dim indexes
        # this test may have left behind.
        _drop_indexes()
    finally:
        driver.close()


@pytest.mark.integration
class TestMemoryClientNoLLM:
    @pytest.mark.asyncio
    async def test_get_context_works_without_llm(
        self, neo4j_connection_info, mock_embedder, session_id, _reset_vector_indexes
    ):
        """T6: end-to-end add_message + get_context with llm=None succeeds."""
        settings = _build_settings(neo4j_connection_info)

        async with MemoryClient(settings, embedder=mock_embedder) as client:
            assert client._settings.llm is None

            await client.short_term.add_message(session_id, "user", "John works at Acme in NYC")
            context = await client.get_context("Tell me about John")
            assert isinstance(context, str)

    def test_no_openai_import_with_llm_none(self, neo4j_connection_info, _reset_vector_indexes):
        """T5: constructing+connecting a client with llm=None must not import openai."""
        script = textwrap.dedent(
            f"""
            import asyncio, sys
            from pydantic import SecretStr
            from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
            from neo4j_agent_memory.config.settings import (
                EmbeddingConfig, EmbeddingProvider,
                ExtractionConfig, ExtractorType,
            )

            settings = MemorySettings(
                neo4j=Neo4jConfig(
                    uri={neo4j_connection_info["uri"]!r},
                    username={neo4j_connection_info["username"]!r},
                    password=SecretStr({neo4j_connection_info["password"]!r}),
                ),
                llm=None,
                embedding=EmbeddingConfig(
                    provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
                    model="all-MiniLM-L6-v2",
                    dimensions=384,
                ),
                extraction=ExtractionConfig(
                    extractor_type=ExtractorType.NONE,
                    enable_llm_fallback=False,
                ),
            )

            async def main():
                async with MemoryClient(settings) as client:
                    await client.short_term.add_message(
                        "no-llm-smoke", "user", "Hello"
                    )

            asyncio.run(main())

            # Subprocess assertion: no openai SDK module loaded along this path.
            offenders = [m for m in sys.modules if m == "openai" or m.startswith("openai.")]
            if offenders:
                print("OPENAI_LOADED:" + ",".join(sorted(offenders)))
                sys.exit(1)
            print("OK")
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            pytest.fail(
                f"no-llm subprocess failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        assert "OK" in result.stdout
        assert "OPENAI_LOADED" not in result.stdout
