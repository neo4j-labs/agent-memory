"""Make the adopted graph *useful*: search it, relate to it, traverse it.

Run after ``adopt.py`` (and optionally ``memory_io.py``). Adoption attaches
the ``:Entity`` label and the library's id/type/name properties, but it does
not embed anything — so the adopted nodes are invisible to semantic search
until you backfill embeddings. This script:

1. backfills embeddings for the adopted entities,
2. retrieves them with ``client.long_term.search_entities()``,
3. records a memory-derived relation between two adopted nodes with
   ``client.long_term.add_relationship()`` and reads it back with
   ``client.long_term.get_related_entities()``,
4. traverses the *pre-existing* domain relationships (``ACTED_IN``,
   ``DIRECTED``, ``IN_GENRE``) from an adopted entity with
   ``client.query.cypher()``.

    uv run python examples/existing-graph/retrieve.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

# Allow running as a standalone script (uv run python examples/existing-graph/retrieve.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_settings import ENTITY_TYPES, SEED_LABELS, build_settings, describe_target

from neo4j_agent_memory import MemoryClient

SEARCH_QUERY = "science fiction film"


def _is_uuid(value: object) -> bool:
    """Whether a node's library id is UUID-shaped.

    Adoption keeps an existing ``id`` property and otherwise generates
    ``<label_lc>:<name>``. The library's read helpers hydrate ``Entity.id``
    as a ``UUID``, so nodes adopted with a generated id are reachable
    through Cypher but not through helpers such as ``search_entities()``.
    See the README "Known gaps" section.
    """
    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


async def backfill_embeddings(client: MemoryClient) -> int:
    """Embed adopted entities so semantic search can retrieve them."""
    # Scoped to the adopted domain labels so the backfill cannot wander into
    # unrelated entities that happen to share a type.
    label_filter = " OR ".join(f"e:{label}" for label in SEED_LABELS)
    rows = await client.query.cypher(
        f"""
        MATCH (e:Entity)
        WHERE ({label_filter}) AND e.type IN $types AND e.embedding IS NULL
        RETURN e.name AS name, e.type AS type, e.id AS id
        ORDER BY type, name
        """,
        {"types": ENTITY_TYPES},
    )

    embedded = 0
    for row in rows:
        if not _is_uuid(row["id"]):
            print(f"    skipping {row['name']!r} — non-UUID adopted id {row['id']!r}")
            continue
        # MERGEs on (name, type) and therefore lands on the adopted node,
        # setting the embedding it was missing. `resolve=False` and
        # `deduplicate=False` keep the write on exactly this node instead of
        # letting resolution or dedupe redirect it to a similar entity.
        await client.long_term.add_entity(
            row["name"],
            row["type"],
            resolve=False,
            deduplicate=False,
        )
        embedded += 1
    return embedded


async def main() -> None:
    settings = build_settings()
    print(f"==> Retrieval over the adopted graph on {describe_target()}")

    async with MemoryClient(settings) as client:
        print("\n1. Backfilling embeddings for adopted entities")
        embedded = await backfill_embeddings(client)
        print(f"    embedded {embedded} adopted entities")

        print(f"\n2. search_entities({SEARCH_QUERY!r}, entity_types=['MOVIE'])")
        movies = await client.long_term.search_entities(
            SEARCH_QUERY,
            entity_types=["MOVIE"],
            limit=5,
            threshold=0.3,
        )
        for hit in movies:
            similarity = hit.metadata.get("similarity", 0.0)
            print(f"    {hit.name:<14} {hit.full_type:<8} similarity={similarity:.3f}")
        if not movies:
            raise SystemExit(
                "search_entities() returned nothing — run adopt.py first, and check "
                "that the vector index dimensions match the configured embedder."
            )

        print("\n3. A library relation write between two adopted nodes")
        director = await client.long_term.get_entity_by_name("Bob Singh")
        movie = await client.long_term.get_entity_by_name("Inception")
        if director is None or movie is None:
            raise SystemExit("Expected adopted entities are missing — run adopt.py first.")

        # Library relations are stored as (:Entity)-[:RELATED_TO {type}]->(:Entity)
        # next to the graph's own DIRECTED edge, so memory-derived claims stay
        # distinguishable from domain facts.
        await client.long_term.add_relationship(
            director,
            movie,
            "DIRECTED",
            description="Stated by the user in the existing-graph demo conversation",
        )
        related = await client.long_term.get_related_entities(director.id)
        for entity, relationship in related:
            print(f"    {director.name} -[:{relationship.type}]-> {entity.name} ({entity.type})")
        print("    (the edge's `type` property records DIRECTED; the Neo4j")
        print("     relationship type stays RELATED_TO so memory writes are")
        print("     distinguishable from the domain graph's own edges)")

        print("\n4. The pre-existing domain relationships, read from the same nodes")
        rows = await client.query.cypher(
            """
            MATCH (p:Entity {name: $name})-[r:ACTED_IN|DIRECTED]->(m:Entity)
            OPTIONAL MATCH (m)-[:IN_GENRE]->(g:Entity)
            RETURN p.name AS person, type(r) AS relation, m.name AS movie,
                   collect(g.name) AS genres
            ORDER BY movie
            """,
            {"name": director.name},
        )
        for row in rows:
            genres = ", ".join(sorted(filter(None, row["genres"]))) or "—"
            print(f"    {row['person']} -[:{row['relation']}]-> {row['movie']}  [{genres}]")


if __name__ == "__main__":
    asyncio.run(main())
