#!/usr/bin/env python3
"""Run neo4j-agent-memory with no LLM provider.

This example shows how to use neo4j-agent-memory in air-gapped environments,
or when you simply don't want to call an LLM:

- ``llm=None`` on ``MemorySettings`` — no OpenAI client is ever constructed.
- A local embedder (sentence-transformers) — no embeddings API is called.
- A local extractor pipeline (spaCy + GLiNER) with the LLM fallback disabled.

Requirements:

    pip install "neo4j-agent-memory[extraction,sentence-transformers]"
    python -m spacy download en_core_web_sm

Set ``NEO4J_URI`` / ``NEO4J_PASSWORD`` if you're not using ``bolt://localhost:7687``
with the default ``password``.
"""

from __future__ import annotations

import asyncio
import os

from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
)


def build_settings() -> MemorySettings:
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        # Explicit opt-out: never construct an LLM client.
        llm=None,
        embedding=EmbeddingConfig(
            provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
            model="all-MiniLM-L6-v2",
            dimensions=384,
        ),
        # Local extraction only. enable_llm_fallback=False is required when
        # llm=None — otherwise MemorySettings raises a ValidationError.
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.PIPELINE,
            enable_spacy=True,
            enable_gliner=True,
            enable_llm_fallback=False,
        ),
    )


async def main() -> None:
    settings = build_settings()
    assert settings.llm is None, "expected llm=None to be preserved"

    async with MemoryClient(settings) as memory:
        session_id = "no-llm-demo"

        await memory.short_term.add_message(
            session_id, "user", "John Smith works at Acme Corp in New York."
        )
        await memory.short_term.add_message(
            session_id, "assistant", "Got it — I'll remember John and Acme Corp."
        )

        context = await memory.get_context("What do we know about John?", session_id=session_id)
        print(context)


if __name__ == "__main__":
    asyncio.run(main())
