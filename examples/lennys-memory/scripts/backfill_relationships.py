#!/usr/bin/env python3
"""Backfill RELATED_TO relationships for existing entities in the database.

Runs GLiREL relationship extraction (no LLM) over messages that already have
extracted entities and writes ``RELATED_TO`` edges between Entity nodes.

Useful for databases populated before relationship extraction existed, or when
``--no-relations`` was used during the initial load.

Features:
- Durable progress: every visited message is stamped with
  ``relations_extracted_at``, so a message GLiREL finds no relations in is still
  "done" and the loop cannot re-process the same batch forever.
- Resumable (``--status`` shows what is left), ``--dry-run`` previews.
- Writes through ``long_term.add_relationship()`` -- the public writer.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import warnings
from pathlib import Path
from uuid import UUID

# Quiet the HuggingFace/spaCy import chatter -- scoped to those modules so the
# library's own DeprecationWarnings (and pydantic's, and the driver's) still
# reach the operator. Never filter a whole warning category process-wide in an
# example meant to teach current API usage.
for _module in ("transformers", "huggingface_hub", "spacy", "thinc", "torch"):
    warnings.filterwarnings("ignore", category=UserWarning, module=_module)
    warnings.filterwarnings("ignore", category=FutureWarning, module=_module)
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

sys.path.insert(0, str(Path(__file__).parent))

from _common import (  # noqa: E402
    Colors,
    add_model_args,
    add_neo4j_args,
    build_memory_settings,
    color,
    format_duration,
    load_backend_env,
)

from neo4j_agent_memory import MemoryClient  # noqa: E402
from neo4j_agent_memory.extraction.base import ExtractedEntity  # noqa: E402
from neo4j_agent_memory.extraction.gliner_extractor import (  # noqa: E402
    GLiRELExtractor,
    is_glirel_available,
)

load_backend_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Cypher queries for the backfill. Inlined rather than imported from
# ``neo4j_agent_memory.graph.queries`` (internal, no stability guarantee) so the
# example shows the query it runs.
GET_MESSAGES_WITH_ENTITIES = """
MATCH (m:Message)-[:MENTIONS]->(e:Entity)
WITH m, collect(DISTINCT {
    id: e.id,
    name: e.name,
    type: e.type,
    subtype: e.subtype
}) AS entities
WHERE size(entities) >= 2
RETURN m.id AS message_id,
       m.content AS content,
       entities
ORDER BY m.timestamp
SKIP $skip
LIMIT $limit
"""

# "Pending" is a durable fact on the node, not something inferred from whether
# relationships happen to exist. A message GLiREL finds nothing in still gets
# stamped, so it leaves this result set and the next page is genuinely new.
GET_MESSAGES_PENDING = """
MATCH (m:Message)-[:MENTIONS]->(e:Entity)
WHERE m.relations_extracted_at IS NULL
WITH m, collect(DISTINCT {
    id: e.id,
    name: e.name,
    type: e.type,
    subtype: e.subtype
}) AS entities
WHERE size(entities) >= 2
RETURN m.id AS message_id,
       m.content AS content,
       entities
ORDER BY m.timestamp
LIMIT $limit
"""

COUNT_MESSAGES_WITH_ENTITIES = """
MATCH (m:Message)-[:MENTIONS]->(e:Entity)
WITH m, count(DISTINCT e) AS entity_count
WHERE entity_count >= 2
RETURN count(m) AS total
"""

COUNT_MESSAGES_PENDING = """
MATCH (m:Message)-[:MENTIONS]->(e:Entity)
WHERE m.relations_extracted_at IS NULL
WITH m, count(DISTINCT e) AS entity_count
WHERE entity_count >= 2
RETURN count(m) AS total
"""

MARK_MESSAGES_PROCESSED = """
UNWIND $message_ids AS message_id
MATCH (m:Message {id: message_id})
SET m.relations_extracted_at = datetime()
"""

CREATE_RELATION_BY_NAME = """
MATCH (source:Entity {name: $source_name})
MATCH (target:Entity {name: $target_name})
MERGE (source)-[r:RELATED_TO {relation_type: $relation_type}]->(target)
ON CREATE SET r.confidence = $confidence, r.created_at = datetime()
"""

GET_RELATIONSHIP_STATS = """
MATCH ()-[r:RELATED_TO]->()
RETURN count(r) AS total_relationships,
       count(DISTINCT r.relation_type) AS unique_types
"""


async def get_message_count(
    memory: MemoryClient,
    skip_processed: bool = True,
) -> int:
    """Count messages that still need relationship extraction."""
    query = COUNT_MESSAGES_PENDING if skip_processed else COUNT_MESSAGES_WITH_ENTITIES
    results = await memory.query.cypher(query)
    return results[0]["total"] if results else 0


async def get_messages_batch(
    memory: MemoryClient,
    skip: int,
    limit: int,
    skip_processed: bool = True,
) -> list[dict]:
    """Get a batch of messages with their entities."""
    if skip_processed:
        # Stamped messages drop out of the result set, so paging is unnecessary.
        return await memory.query.cypher(GET_MESSAGES_PENDING, {"limit": limit})
    return await memory.query.cypher(GET_MESSAGES_WITH_ENTITIES, {"skip": skip, "limit": limit})


async def mark_processed(memory: MemoryClient, message_ids: list[str]) -> None:
    """Stamp messages as visited so a zero-relation message is genuinely done."""
    if not message_ids:
        return
    await memory.graph.execute_write(MARK_MESSAGES_PROCESSED, {"message_ids": message_ids})


async def get_relationship_stats(memory: MemoryClient) -> dict:
    """Get current relationship statistics."""
    results = await memory.query.cypher(GET_RELATIONSHIP_STATS)
    if results:
        return {
            "total_relationships": results[0]["total_relationships"],
            "unique_types": results[0]["unique_types"],
        }
    return {"total_relationships": 0, "unique_types": 0}


async def store_relations(
    memory: MemoryClient,
    relations: list[dict],
    entity_id_map: dict[str, str],
) -> int:
    """Store extracted relations as RELATED_TO relationships.

    Uses the public ``long_term.add_relationship()`` writer when both endpoints
    resolved to ids, and falls back to a name match otherwise.

    Args:
        memory: MemoryClient instance
        relations: Relation dicts with source, target, relation_type, confidence
        entity_id_map: Mapping from lowercased entity name to entity ID

    Returns:
        Number of relationships created
    """
    created = 0

    for rel in relations:
        source_id = entity_id_map.get(rel["source"].lower())
        target_id = entity_id_map.get(rel["target"].lower())

        if source_id and target_id:
            try:
                await memory.long_term.add_relationship(
                    UUID(source_id),
                    UUID(target_id),
                    rel["relation_type"],
                    confidence=rel["confidence"],
                )
                created += 1
            except Exception as e:
                logger.debug(f"Failed to create relation by ID: {e}")
        else:
            # Names that GLiREL returned but that are not in this message's
            # entity list (e.g. an alias) -- match by name instead.
            try:
                await memory.graph.execute_write(
                    CREATE_RELATION_BY_NAME,
                    {
                        "source_name": rel["source"],
                        "target_name": rel["target"],
                        "relation_type": rel["relation_type"],
                        "confidence": rel["confidence"],
                    },
                )
                created += 1
            except Exception as e:
                logger.debug(f"Failed to create relation by name: {e}")

    return created


async def process_message(
    memory: MemoryClient,
    extractor: GLiRELExtractor,
    message: dict,
) -> dict:
    """Process a single message to extract and store relationships.

    Args:
        memory: MemoryClient instance
        extractor: GLiREL extractor
        message: Message dict with content and entities

    Returns:
        Dict with processing stats
    """
    content = message["content"]
    entities_data = message["entities"]

    # Convert to ExtractedEntity objects for GLiREL
    entities = []
    entity_id_map = {}

    for e in entities_data:
        entity = ExtractedEntity(
            name=e["name"],
            type=e["type"],
            subtype=e.get("subtype"),
            confidence=1.0,  # Existing entities have implicit high confidence
        )
        entities.append(entity)
        # Build name-to-ID mapping for relationship storage
        entity_id_map[e["name"].lower()] = e["id"]

    if len(entities) < 2:
        return {"relations_extracted": 0, "relations_stored": 0}

    # Extract relations using GLiREL
    try:
        relations = await extractor.extract_relations(content, entities)
    except Exception as e:
        logger.warning(f"Failed to extract relations: {e}")
        return {"relations_extracted": 0, "relations_stored": 0, "error": str(e)}

    if not relations:
        return {"relations_extracted": 0, "relations_stored": 0}

    # Convert to dict format for storage
    relations_data = [
        {
            "source": r.source,
            "target": r.target,
            "relation_type": r.relation_type,
            "confidence": r.confidence,
        }
        for r in relations
    ]

    # Store relations
    stored = await store_relations(memory, relations_data, entity_id_map)

    return {
        "relations_extracted": len(relations),
        "relations_stored": stored,
    }


async def backfill_relationships(
    memory: MemoryClient,
    extractor: GLiRELExtractor,
    batch_size: int = 50,
    skip_processed: bool = True,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict:
    """Backfill RELATED_TO relationships for existing entities.

    Args:
        memory: MemoryClient instance
        extractor: GLiREL extractor
        batch_size: Number of messages to process per batch
        skip_processed: Skip messages that already have relationships
        dry_run: If True, only show what would be done
        limit: Optional limit on total messages to process

    Returns:
        Stats dict with total processed, relations extracted, etc.
    """
    # Get initial stats
    initial_stats = await get_relationship_stats(memory)
    total_messages = await get_message_count(memory, skip_processed)

    if limit:
        total_messages = min(total_messages, limit)

    print(f"\n{color('Relationship Backfill', Colors.BOLD + Colors.CYAN)}")
    print(f"{'=' * 50}")
    print(f"  Messages to process: {total_messages:,}")
    print(f"  Existing relationships: {initial_stats['total_relationships']:,}")
    print(f"  Batch size: {batch_size}")
    print(f"  Skip processed: {skip_processed}")
    if dry_run:
        print(f"  {color('DRY RUN MODE', Colors.YELLOW)}")
    print()

    if total_messages == 0:
        print(color("No messages need relationship extraction!", Colors.GREEN))
        return {
            "messages_processed": 0,
            "relations_extracted": 0,
            "relations_stored": 0,
        }

    if dry_run:
        # Show sample of messages that would be processed
        print("Sample of messages that would be processed:")
        sample = await get_messages_batch(memory, 0, 5, skip_processed)
        for msg in sample:
            entity_names = [e["name"] for e in msg["entities"]]
            print(f"  • {len(msg['entities'])} entities: {', '.join(entity_names[:5])}")
            if len(entity_names) > 5:
                print(f"    ... and {len(entity_names) - 5} more")
        print()
        return {
            "messages_processed": 0,
            "relations_extracted": 0,
            "relations_stored": 0,
            "dry_run": True,
        }

    # Process messages in batches
    start_time = time.time()
    processed = 0
    total_extracted = 0
    total_stored = 0
    errors = 0
    skip = 0
    # Guards against a batch that yields no new ids (e.g. a stamp write failed):
    # without it the loop would spin on the same page forever.
    seen: set[str] = set()

    while processed < total_messages:
        batch = await get_messages_batch(memory, skip, batch_size, skip_processed)

        if not batch:
            break

        new_ids = [msg["message_id"] for msg in batch if msg["message_id"] not in seen]
        if not new_ids:
            logger.warning("Batch contained no unseen messages; stopping to avoid a loop.")
            break

        batch_ids: list[str] = []
        for msg in batch:
            if msg["message_id"] in seen:
                continue
            seen.add(msg["message_id"])
            batch_ids.append(msg["message_id"])

            result = await process_message(memory, extractor, msg)

            total_extracted += result.get("relations_extracted", 0)
            total_stored += result.get("relations_stored", 0)
            if result.get("error"):
                errors += 1

            processed += 1

            # Progress update
            if processed % 10 == 0 or processed == total_messages:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                eta = (total_messages - processed) / rate if rate > 0 else 0

                sys.stdout.write(
                    f"\r  Progress: {processed}/{total_messages} "
                    f"({processed / total_messages * 100:.0f}%) "
                    f"| Relations: {total_stored:,} stored "
                    f"| {rate:.1f} msg/s "
                    f"| ETA: {format_duration(eta)}"
                )
                sys.stdout.flush()

            if limit and processed >= limit:
                break

        # Stamp the whole batch as visited -- including messages GLiREL found no
        # relations in, which is what makes the next page genuinely new.
        if skip_processed:
            await mark_processed(memory, batch_ids)
        else:
            skip += batch_size

        if limit and processed >= limit:
            break

    print()  # Newline after progress

    # Final stats
    elapsed = time.time() - start_time
    final_stats = await get_relationship_stats(memory)

    print()
    print(color("═" * 50, Colors.CYAN))
    print(color("  Backfill Complete!", Colors.BOLD + Colors.GREEN))
    print(color("═" * 50, Colors.CYAN))
    print()
    print(f"  {color('Messages processed:', Colors.DIM)} {processed:,}")
    print(f"  {color('Relations extracted:', Colors.DIM)} {total_extracted:,}")
    print(f"  {color('Relations stored:', Colors.DIM)} {total_stored:,}")
    print(f"  {color('Errors:', Colors.DIM)} {errors}")
    print(f"  {color('Elapsed time:', Colors.DIM)} {format_duration(elapsed)}")
    print(f"  {color('Throughput:', Colors.DIM)} {processed / elapsed:.1f} msg/s")
    print()
    print(
        f"  {color('Total relationships now:', Colors.DIM)} {final_stats['total_relationships']:,}"
    )
    print(
        f"  {color('New relationships:', Colors.DIM)} {final_stats['total_relationships'] - initial_stats['total_relationships']:,}"
    )
    print()

    return {
        "messages_processed": processed,
        "relations_extracted": total_extracted,
        "relations_stored": total_stored,
        "errors": errors,
        "elapsed_seconds": elapsed,
        "new_relationships": final_stats["total_relationships"]
        - initial_stats["total_relationships"],
    }


async def show_status(memory: MemoryClient) -> None:
    """Show current relationship extraction status."""
    # Get counts
    total_with_entities = await get_message_count(memory, skip_processed=False)
    pending = await get_message_count(memory, skip_processed=True)
    processed = total_with_entities - pending

    stats = await get_relationship_stats(memory)

    print()
    print(color("Relationship Extraction Status", Colors.BOLD + Colors.CYAN))
    print("=" * 50)
    print()
    print(f"  {color('Messages with 2+ entities:', Colors.DIM)} {total_with_entities:,}")
    print(f"  {color('Messages processed:', Colors.DIM)} {processed:,}")
    print(f"  {color('Messages pending:', Colors.DIM)} {pending:,}")
    print()
    print(
        f"  {color('Total RELATED_TO relationships:', Colors.DIM)} {stats['total_relationships']:,}"
    )
    print(f"  {color('Unique relationship types:', Colors.DIM)} {stats['unique_types']}")
    print()

    if pending > 0:
        print(
            f"  Run {color('make backfill-relationships', Colors.YELLOW)} to process pending messages."
        )
    else:
        print(f"  {color('All messages have been processed!', Colors.GREEN)}")
    print()


async def main():
    parser = argparse.ArgumentParser(
        description="Backfill RELATED_TO relationships for existing entities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show current status
  %(prog)s --status

  # Run backfill (skip already processed)
  %(prog)s

  # Reprocess all messages (including already processed)
  %(prog)s --reprocess

  # Process only 100 messages
  %(prog)s --limit 100

  # Preview without making changes
  %(prog)s --dry-run
""",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of messages per batch (default: 50)",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Reprocess all messages, not just pending ones",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit total messages to process",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current status and exit",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Confidence threshold for relations (default: 0.5)",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        choices=["cpu", "cuda", "mps"],
        help="Device to run GLiREL on (default: cpu)",
    )

    add_neo4j_args(parser)
    add_model_args(parser)
    args = parser.parse_args()

    # Check GLiREL availability
    if not args.status and not is_glirel_available():
        print(color("Error: GLiREL is not installed.", Colors.RED))
        print("Install it with: pip install glirel")
        sys.exit(1)

    # One settings builder for the whole pipeline (same embedding space).
    settings = build_memory_settings(args, quiet=True)

    print()
    print(color("Connecting to Neo4j...", Colors.DIM), end=" ", flush=True)

    try:
        memory_client = MemoryClient(settings)
        async with memory_client as memory:
            print(color("Connected!", Colors.GREEN))

            if args.status:
                await show_status(memory)
                return

            # Initialize GLiREL extractor
            print(color("Loading GLiREL model...", Colors.DIM), end=" ", flush=True)
            extractor = GLiRELExtractor.for_poleo(
                threshold=args.threshold,
                device=args.device,
            )
            # Force model load
            _ = extractor.model
            print(color("Loaded!", Colors.GREEN))

            # Run backfill
            await backfill_relationships(
                memory,
                extractor,
                batch_size=args.batch_size,
                skip_processed=not args.reprocess,
                dry_run=args.dry_run,
                limit=args.limit,
            )

    except KeyboardInterrupt:
        print()
        print(color("\nInterrupted by user.", Colors.YELLOW))
        sys.exit(130)
    except Exception as e:
        print(color(f"Error: {e}", Colors.RED))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
