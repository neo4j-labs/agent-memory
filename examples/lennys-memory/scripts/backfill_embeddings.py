#!/usr/bin/env python3
"""Backfill embeddings for existing Entity nodes.

Entities created without an embedding cannot be found by vector search. This
script embeds them in batches and writes the vectors back through the library's
buffered writer.

Usage:
    python backfill_embeddings.py [--batch-size 100] [--status] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (  # noqa: E402
    Colors,
    ProgressBar,
    add_model_args,
    add_neo4j_args,
    build_memory_settings,
    color,
    format_duration,
    load_backend_env,
)

from neo4j_agent_memory import MemoryClient  # noqa: E402
from neo4j_agent_memory.config.settings import MemoryConfig  # noqa: E402
from neo4j_agent_memory.llm import from_provider  # noqa: E402

load_backend_env()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Inlined rather than imported from ``neo4j_agent_memory.graph.queries``: that
# module is internal with no stability guarantee, and an example should show the
# query it runs. ``$exclude`` lets a poison row drop out of the result set so a
# persistent failure cannot turn the backfill into an infinite loop.
COUNT_PENDING = """
MATCH (e:Entity)
WHERE e.embedding IS NULL
RETURN count(e) AS count
"""

COUNT_TOTAL = "MATCH (e:Entity) RETURN count(e) AS count"

FETCH_PENDING = """
MATCH (e:Entity)
WHERE e.embedding IS NULL AND NOT e.id IN $exclude
RETURN e.id AS id, e.name AS name, e.type AS type, e.description AS description
ORDER BY e.name
LIMIT $limit
"""
# ``$exclude`` carries both the ids we already submitted a write for and the ids
# that failed to embed. Both matter: buffered writes land asynchronously, so a
# row we just embedded can still have a NULL embedding on the next page -- without
# excluding it the loop would re-embed the same page indefinitely.

WRITE_EMBEDDINGS = """
UNWIND $rows AS row
MATCH (e:Entity {id: row.id})
SET e.embedding = row.embedding, e.updated_at = datetime()
"""


def embed_text(row: dict) -> str:
    """Text used to embed an entity: its name, plus its description if present."""
    name = row["name"]
    description = row.get("description")
    return f"{name}: {description}" if description else name


async def get_status(client: MemoryClient) -> dict[str, int]:
    """Count entities with and without embeddings."""
    pending_rows = await client.query.cypher(COUNT_PENDING)
    total_rows = await client.query.cypher(COUNT_TOTAL)
    pending = pending_rows[0]["count"] if pending_rows else 0
    total = total_rows[0]["count"] if total_rows else 0
    return {"total": total, "with_embeddings": total - pending, "pending": pending}


def print_status(status: dict[str, int]) -> None:
    print(f"\n{color('Entity Embedding Status', Colors.BOLD + Colors.CYAN)}")
    print("=" * 50)
    print(f"Total entities:  {color(str(status['total']), Colors.BOLD)}")
    print(f"With embeddings: {color(str(status['with_embeddings']), Colors.GREEN)}")
    print(f"Pending:         {color(str(status['pending']), Colors.YELLOW)}")
    print("=" * 50)


async def backfill_embeddings(
    client: MemoryClient,
    embedding_model: str,
    *,
    batch_size: int = 100,
    dry_run: bool = False,
) -> dict[str, int]:
    """Embed every Entity that has no embedding, one ``embed_batch`` per page."""
    embedder = from_provider(embedding_model, kind="embedding")

    status = await get_status(client)
    if status["pending"] == 0:
        print(color("\nAll entities already have embeddings.", Colors.GREEN))
        return {"embedded": 0, "failed": 0}

    print_status(status)
    if dry_run:
        print(color("\nDry run: no embeddings generated.", Colors.YELLOW))
        return {"embedded": 0, "failed": 0}

    progress = ProgressBar(status["pending"], label="Embedding")
    embedded = 0
    failed: list[str] = []
    # Ids we have already handled this run (submitted or failed). The old loop
    # paged with a constant skip=0 and relied on rows dropping out of the result
    # set, which only held while every row succeeded -- one un-embeddable entity
    # turned the backfill into an infinite loop that kept spending API calls.
    handled: set[str] = set()

    while True:
        rows = await client.query.cypher(
            FETCH_PENDING,
            {"exclude": sorted(handled), "limit": batch_size},
        )
        if not rows:
            break
        handled.update(row["id"] for row in rows)

        try:
            # EmbeddingProvider.embed() takes a SEQUENCE of texts and returns one
            # vector per text -- one call per page instead of one per entity.
            # (embed_one() is the single-text form; passing a str to embed()
            # would iterate its characters.)
            vectors = await embedder.embed([embed_text(row) for row in rows])
        except Exception:
            logger.exception("Batch embedding failed; falling back to one call per entity")
            vectors = []
            for row in rows:
                try:
                    vectors.append(await embedder.embed_one(embed_text(row)))
                except Exception:
                    logger.warning("Could not embed entity %s; skipping", row["name"])
                    failed.append(row["id"])
                    vectors.append(None)

        payload = [
            {"id": row["id"], "embedding": vector}
            for row, vector in zip(rows, vectors, strict=True)
            if vector is not None
        ]
        if payload:
            # Fire-and-forget write: the queue drains in the background while we
            # embed the next page. ``client.flush()`` below waits for it.
            await client.buffered.submit(WRITE_EMBEDDINGS, {"rows": payload})
            embedded += len(payload)

        progress.advance(len(rows), suffix=f"failed: {len(failed)}")

    await client.flush()
    progress.close()

    print(f"\n{color('Backfill complete', Colors.BOLD + Colors.GREEN)}")
    print("=" * 50)
    print(f"Embedded: {color(str(embedded), Colors.GREEN)}")
    print(f"Failed:   {color(str(len(failed)), Colors.RED if failed else Colors.DIM)}")
    print(f"Time:     {format_duration(progress.elapsed)}")
    if client.write_errors:
        print(color(f"Write errors: {len(client.write_errors)}", Colors.RED))
    print("=" * 50)
    return {"embedded": embedded, "failed": len(failed)}


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill embeddings for Entity nodes without them",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Entities embedded per batch (default: 100)",
    )
    parser.add_argument("--status", action="store_true", help="Show status only")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be embedded without calling the embedding API",
    )
    add_neo4j_args(parser)
    add_model_args(parser)
    args = parser.parse_args()

    # Buffered writes keep the embedding loop off the Neo4j round-trip path:
    # ``submit()`` enqueues and returns, ``client.flush()`` waits at the end.
    settings = build_memory_settings(args, memory=MemoryConfig(write_mode="buffered"))

    async with MemoryClient(settings) as client:
        if args.status:
            print_status(await get_status(client))
            return
        await backfill_embeddings(
            client,
            args.embedding_model,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    asyncio.run(main())
