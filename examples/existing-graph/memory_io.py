"""Write messages against the adopted graph and prove no duplicates appear.

Run after ``adopt.py``. The script writes two messages that name people and
movies from the seed graph, then asserts that the resulting ``MENTIONS``
edges point at the pre-existing domain nodes — and that the graph still
holds exactly one node per name.

    uv run python examples/existing-graph/memory_io.py

Why mentions are linked explicitly
----------------------------------
``add_message()`` defaults to running the configured NER pipeline. Against
an adopted graph that path has two verified failure modes in v0.5.0, both
silent:

* the extractors map their labels through POLE+O, so a movie comes back
  typed ``OBJECT`` — and because entities MERGE on ``(:Entity {name,
  type})`` that writes a *second* ``:Entity:Object`` node beside the
  adopted ``:Movie``, and the mention links to the duplicate;
* give the extractor a ``label_mapping`` so it emits ``MOVIE`` and the
  MERGE does find the adopted node — but no ``MENTIONS`` edge is written
  at all, because the link step looks the entity up by the id it generated
  rather than the id the MERGE returned (the adopted node kept its own).

``extraction_mode="explicit"`` sidesteps both: it skips extraction and
MERGEs exactly the entities you name, linking by the id that MERGE
returns. That is the right default whenever your application already knows
which domain objects a message is about — which, with an existing graph,
it usually does. See the README "Known gaps" section.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Allow running as a standalone script (uv run python examples/existing-graph/memory_io.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_settings import DEMO_NAMES, SESSION_ID, build_settings, describe_target

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.schema.models import EntityRef

MESSAGES: list[tuple[str, str, list[EntityRef]]] = [
    (
        "user",
        "Have you seen Inception? Bob Singh directed it.",
        [
            # `label` records the source domain label for readers and tools;
            # the MERGE itself matches on name + type.
            EntityRef(name="Inception", type="MOVIE", label="Movie"),
            EntityRef(name="Bob Singh", type="PERSON", label="Person"),
        ],
    ),
    (
        "assistant",
        "Yes, and Carol Reyes plays a brilliant linguist in Arrival.",
        [
            EntityRef(name="Arrival", type="MOVIE", label="Movie"),
            EntityRef(name="Carol Reyes", type="PERSON", label="Person"),
        ],
    ),
]

NO_MENTIONS_HINT = (
    "No MENTIONS edges were created. Run adopt.py first, and check that the "
    "types in explicit_mentions match LABEL_TO_TYPE in memory_settings.py."
)


async def write_and_verify() -> None:
    settings = build_settings()
    print(f"==> Writing messages with explicit mentions to {describe_target()}")

    async with MemoryClient(settings) as client:
        for role, content, mentions in MESSAGES:
            await client.short_term.add_message(
                SESSION_ID,
                role,
                content,
                extraction_mode="explicit",
                explicit_mentions=mentions,
            )

        # Count every node the library could have created for these names —
        # across *all* labels, not just :Person/:Movie. A duplicate shows up
        # as total=2 with a second label set such as ["Entity", "Object"].
        rows = await client.query.cypher(
            """
            UNWIND $names AS target
            MATCH (n) WHERE n.name = target
            RETURN target, count(n) AS total,
                   collect(DISTINCT labels(n)) AS label_sets
            ORDER BY target
            """,
            {"names": DEMO_NAMES},
        )
        print("\nNodes per demo name (1 means library writes hit the adopted node):")
        duplicates = []
        for row in rows:
            label_sets = [":".join(labels) for labels in row["label_sets"]]
            print(f"  {row['target']:<14} total={row['total']:<3} {label_sets}")
            if row["total"] != 1:
                duplicates.append(row["target"])

        # MENTIONS edges must point at the adopted domain nodes.
        mention_rows = await client.query.cypher(
            """
            MATCH (c:Conversation {session_id: $session_id})
                  -[:HAS_MESSAGE]->(:Message)-[:MENTIONS]->(e:Entity)
            RETURN DISTINCT e.name AS name, e.type AS type,
                            labels(e) AS labels, e.id AS id
            ORDER BY name
            """,
            {"session_id": SESSION_ID},
        )
        print("\nMENTIONS edges produced by add_message():")
        for row in mention_rows:
            print(f"  {row['name']:<14} {row['type']:<8} {':'.join(row['labels'])}")

        if duplicates:
            raise SystemExit(
                "Duplicate nodes found for: "
                + ", ".join(duplicates)
                + ". The library write did not land on the adopted node — check "
                "that adopt.py ran and that the entity types match LABEL_TO_TYPE."
            )
        if not mention_rows:
            raise SystemExit(NO_MENTIONS_HINT)

        print("\nOK: one node per name, and every MENTIONS edge lands on an adopted node.")


if __name__ == "__main__":
    asyncio.run(write_and_verify())
