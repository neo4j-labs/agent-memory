"""Add messages to the adopted graph and verify MENTIONS link to existing nodes.

Run after ``adopt.py``. The script writes a couple of messages that name
people and movies from the seed graph, then queries Neo4j to confirm
that the resulting ``MENTIONS`` edges point at the pre-existing domain
nodes — not at duplicates.

    uv run examples/existing-graph/memory_io.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Allow running as a standalone script (uv run python examples/existing-graph/memory_io.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_settings import build_settings

from neo4j_agent_memory import MemoryClient

SESSION_ID = "existing-graph-demo"


async def write_and_verify() -> None:
    settings = build_settings()
    async with MemoryClient(settings) as client:
        # A couple of messages that mention people/movies the domain graph
        # already knows about.
        await client.short_term.add_message(
            SESSION_ID,
            "user",
            "Have you seen Inception? Bob Singh directed it.",
        )
        await client.short_term.add_message(
            SESSION_ID,
            "assistant",
            "Yes, and Carol Reyes plays a brilliant linguist in Arrival.",
        )

        # If adoption worked, there should still be exactly one node per
        # name across the entire graph — even though the messages above
        # mentioned them and would have triggered a MERGE on
        # (:Entity {name, type}).
        rows = await client.graph.execute_read(
            """
            UNWIND ['Bob Singh', 'Carol Reyes', 'Inception', 'Arrival'] AS target
            MATCH (n {name: target})
            WHERE n:Person OR n:Movie
            RETURN target, count(n) AS count
            ORDER BY target
            """
        )
        print("Per-name node count after writes (1 means adoption worked):")
        for row in rows:
            print(f"  {row['target']:<14} -> {row['count']}")

        # And the MENTIONS edges should connect messages to the
        # pre-existing domain nodes.
        rows = await client.graph.execute_read(
            """
            MATCH (m:Message)-[:MENTIONS]->(e:Entity)
            WHERE e.name IN ['Bob Singh', 'Carol Reyes', 'Inception', 'Arrival']
            RETURN e.name AS name, labels(e) AS labels
            ORDER BY name
            """
        )
        print("\nMENTIONS edges produced by add_message():")
        for row in rows:
            print(f"  {row['name']:<14} -> labels={row['labels']}")


if __name__ == "__main__":
    asyncio.run(write_and_verify())
