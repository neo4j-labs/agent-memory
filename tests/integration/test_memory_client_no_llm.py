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
from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
)


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


@pytest.mark.integration
class TestMemoryClientNoLLM:
    @pytest.mark.asyncio
    async def test_get_context_works_without_llm(
        self, neo4j_connection_info, mock_embedder, session_id
    ):
        """T6: end-to-end add_message + get_context with llm=None succeeds."""
        settings = _build_settings(neo4j_connection_info)

        async with MemoryClient(settings, embedder=mock_embedder) as client:
            assert client._settings.llm is None

            await client.short_term.add_message(session_id, "user", "John works at Acme in NYC")
            context = await client.get_context("Tell me about John")
            assert isinstance(context, str)

    def test_no_openai_import_with_llm_none(self, neo4j_connection_info):
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
