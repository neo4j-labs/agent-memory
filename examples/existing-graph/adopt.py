"""Adopt the seed domain graph as long-term memory entities.

Run this *after* ``seed_domain_graph.cypher`` has loaded the Movies domain.
Idempotent — re-running on an already-adopted graph is a no-op.

    uv run examples/existing-graph/adopt.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Allow running as a standalone script (uv run python examples/existing-graph/adopt.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_settings import build_settings

from neo4j_agent_memory import MemoryClient


async def adopt() -> None:
    settings = build_settings()
    async with MemoryClient(settings) as client:
        report = await client.schema.adopt_existing_graph(
            label_to_type={
                "Person": "PERSON",
                "Movie": "MOVIE",
                "Genre": "GENRE",
            },
            # Movies use ``title`` rather than ``name`` for their display
            # name. Person and Genre already use ``name``.
            name_property_per_label={"Movie": "title"},
        )

        print(f"Adopted {report.total_migrated} nodes "
              f"({report.total_already_adopted} already adopted, "
              f"{report.total_skipped} skipped).")
        for label_report in report.by_label:
            print(
                f"  {label_report.label} → {label_report.type}: "
                f"+{label_report.migrated_count} new, "
                f"={label_report.already_adopted_count} already, "
                f"~{label_report.skipped_count} skipped"
            )


if __name__ == "__main__":
    asyncio.run(adopt())
