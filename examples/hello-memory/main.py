# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "neo4j-agent-memory[nams,openai]>=0.5.0,<0.7",
# ]
# ///
"""hello-memory — the smallest complete round trip through agent memory.

Two messages in, one entity, one preference, then the assembled context back
out. That is the whole library's contract; every other example in this
directory is a variation on it.

Run it with no checkout and no virtualenv — uv reads the PEP 723 header above
and builds a throwaway environment:

    # Hosted: no database to run.
    MEMORY_API_KEY=nams_... uv run examples/hello-memory/main.py

    # Your own Neo4j (start one with the docker one-liner in the README).
    OPENAI_API_KEY=sk-... uv run examples/hello-memory/main.py

The backend auto-selects: NAMS when ``MEMORY_API_KEY`` is set, otherwise bolt
against ``NEO4J_URI``. The memory calls below are identical either way — that
is the point of the backend-agnostic Protocol. Two differences do surface here,
and both are commented where they happen.

This is the only example that uses a PEP 723 header; the rest pin the library
in a ``requirements.txt`` or ``pyproject.toml`` and run from a checkout. It is
deliberately written against the *released* surface so a stranger can run it
before cloning anything.
"""

from __future__ import annotations

import asyncio
import os

from pydantic import SecretStr

from neo4j_agent_memory import (
    ExtractionConfig,
    ExtractorType,
    MemoryClient,
    MemorySettings,
    Neo4jConfig,
)

SESSION = "hello-memory"


def settings_from_env() -> MemorySettings:
    """NAMS when ``MEMORY_API_KEY`` is set, otherwise your own Neo4j over bolt."""
    if os.getenv("MEMORY_API_KEY"):
        # Nothing else to configure: the service owns embeddings and extraction.
        return MemorySettings(backend="nams")
    return MemorySettings(
        backend="bolt",
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "test-password")),
        ),
        # Any provider string works. "sentence-transformers/all-MiniLM-L6-v2"
        # runs locally with no API key (add the [sentence-transformers] extra).
        embedding=os.getenv("EMBEDDING", "openai/text-embedding-3-small"),
        # Hello world writes its own entity, so it needs no extractor and no LLM.
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
        llm=None,
    )


async def main() -> None:
    async with MemoryClient(settings_from_env()) as client:
        print(f"backend: {client.backend}")
        session = SESSION
        if client.is_nams:
            # Difference 1: NAMS mints conversation ids server-side. On bolt the
            # first add_message creates the conversation under the name you chose.
            session = str((await client.short_term.create_conversation(SESSION)).id)
        await client.short_term.add_message(session, "user", "I'm allergic to shellfish.")
        await client.short_term.add_message(session, "assistant", "Noted — no shellfish.")
        # Returns (entity, dedup_result) on bolt and the entity on NAMS; this
        # example needs neither — see examples/entity_resolution.py for dedup.
        await client.long_term.add_entity("Shellfish", "OBJECT", description="A food allergen.")
        if not client.is_nams:
            # Difference 2: preferences are bolt-only — NAMS has no endpoint yet.
            await client.long_term.add_preference("diet", "Avoids shellfish")
        # One call, three memory layers. Retrieval is vector search with a
        # similarity floor (MemorySettings.search), so ask something close to
        # what you stored — "dinner plans" is too far from "shellfish" to hit.
        print(await client.get_context("shellfish allergy", session_id=session))


if __name__ == "__main__":
    asyncio.run(main())
