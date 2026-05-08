"""Buffered-writes example: agent responds to the user before persistence completes.

Demonstrates the v0.4 P1.1 fire-and-forget API:

* ``MemorySettings.memory.write_mode = "buffered"``
* ``client.buffered.submit(query, params)`` queues writes
* ``client.flush()`` drains the queue at shutdown / between bursts
* ``client.write_errors`` exposes background failures

Run from the repo root::

    uv run python examples/buffered-writes/main.py
"""

from __future__ import annotations

import asyncio
import os
import time

from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
)


def build_settings() -> MemorySettings:
    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        llm=None,
        embedding=EmbeddingConfig(
            provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
            model="all-MiniLM-L6-v2",
            dimensions=384,
        ),
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
    )
    settings.memory.write_mode = "buffered"
    settings.memory.max_pending = 200
    return settings


async def fake_agent_turn(client: MemoryClient, turn: int) -> str:
    """Simulate one agent turn: submit a write, then return the user-visible response.

    The Cypher write goes into the buffer and the function returns
    immediately. The agent's response is therefore not blocked on Neo4j.
    """
    await client.buffered.submit(
        """
        MERGE (t:AgentTurn {turn: $turn})
        ON CREATE SET t.recorded_at = datetime()
        """,
        {"turn": turn},
    )
    return f"response to turn {turn}"


async def main() -> None:
    async with MemoryClient(build_settings()) as client:
        # Reset for an idempotent rerun.
        await client.graph.execute_write(
            "MATCH (t:AgentTurn) DETACH DELETE t"
        )

        # Run 50 "agent turns" and time how long the user-facing path takes.
        # Without buffering, each turn would block on a Neo4j round-trip.
        start = time.perf_counter()
        responses = await asyncio.gather(
            *(fake_agent_turn(client, i) for i in range(50))
        )
        elapsed = (time.perf_counter() - start) * 1000
        print(f"50 turns produced {len(responses)} responses in {elapsed:.1f} ms")
        print(f"Pending writes after responses returned: {client.buffered.pending}")

        # Now drain the buffer.
        flush_start = time.perf_counter()
        await client.flush()
        flush_elapsed = (time.perf_counter() - flush_start) * 1000
        print(f"flush() drained the queue in {flush_elapsed:.1f} ms")

        # Verify all 50 turns landed.
        rows = await client.graph.execute_read(
            "MATCH (t:AgentTurn) RETURN count(t) AS cnt"
        )
        print(f"AgentTurn rows in Neo4j: {rows[0]['cnt']}")

        # Inspect any background errors.
        if client.write_errors:
            print(f"WARNING: {len(client.write_errors)} buffered writes failed")
            for err in client.write_errors:
                print(f"  - {err.error}")
        else:
            print("No buffered-write errors.")


if __name__ == "__main__":
    asyncio.run(main())
