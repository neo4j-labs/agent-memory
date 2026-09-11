#!/usr/bin/env python3
"""Enrich entities with data from Wikipedia and (optionally) Diffbot.

Fills in, per Entity node:
- ``enriched_description`` (Wikipedia summary or Diffbot description)
- ``wikipedia_url``, ``wikidata_id``, ``image_url``
- ``enriched_at`` / ``enrichment_provider``

Features:
- Real-time progress bar with ETA
- Configurable rate limiting (respects the Wikimedia ToS)
- ``--provider wikimedia|diffbot|composite`` with result caching
- Retries rate-limited entities instead of silently skipping them
- Skips already-enriched entities; filter by entity type
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (  # noqa: E402
    USE_COLORS,
    Colors,
    add_model_args,
    add_neo4j_args,
    build_memory_settings,
    color,
    format_duration,
    load_backend_env,
)

from neo4j_agent_memory import MemoryClient  # noqa: E402
from neo4j_agent_memory.enrichment.base import EnrichmentStatus  # noqa: E402
from neo4j_agent_memory.enrichment.factory import (  # noqa: E402
    CachedEnrichmentProvider,
    CompositeEnrichmentProvider,
    create_enrichment_provider,
)

load_backend_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Suppress noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("neo4j").setLevel(logging.WARNING)

# Re-exported so ``patch("_common.USE_COLORS", ...)`` is the only knob.
__all__ = ["USE_COLORS", "Colors", "color", "enrich_entities"]

MAX_ATTEMPTS = 3


@dataclass
class EnrichmentStats:
    """Statistics for enrichment run."""

    total: int = 0
    enriched: int = 0
    not_found: int = 0
    skipped: int = 0
    errors: int = 0
    rate_limited: int = 0

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        processed = self.enriched + self.not_found + self.errors
        if processed == 0:
            return 0.0
        return self.enriched / processed * 100


# ``format_duration`` lives in scripts/_common.py so all five scripts agree.
format_time = format_duration


def print_progress(
    current: int,
    total: int,
    stats: EnrichmentStats,
    start_time: float,
    current_entity: str = "",
) -> None:
    """Print progress bar with stats."""
    if total == 0:
        return

    # Calculate progress
    progress = current / total
    bar_width = 30
    filled = int(bar_width * progress)
    bar = "█" * filled + "░" * (bar_width - filled)

    # Calculate ETA
    elapsed = time.time() - start_time
    if current > 0:
        eta = (elapsed / current) * (total - current)
        eta_str = format_time(eta)
    else:
        eta_str = "calculating..."

    # Truncate entity name
    entity_display = current_entity[:30] + "..." if len(current_entity) > 30 else current_entity

    # Build status line
    status = (
        f"\r{color('Progress', Colors.CYAN)} [{bar}] "
        f"{current}/{total} ({progress * 100:.1f}%) "
        f"ETA: {eta_str} "
        f"| {color('✓', Colors.GREEN)}{stats.enriched} "
        f"{color('✗', Colors.RED)}{stats.not_found} "
        f"{color('!', Colors.YELLOW)}{stats.errors} "
        f"| {entity_display:<35}"
    )

    print(status, end="", flush=True)


async def get_unenriched_entities(
    client: MemoryClient,
    entity_types: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Get entities that haven't been enriched yet.

    Args:
        client: Memory client
        entity_types: Filter by entity types (e.g., ["PERSON", "ORGANIZATION"])
        limit: Maximum number of entities to return

    Returns:
        List of entity dicts with id, name, type
    """
    # Fully parameterized: user-supplied --types/--limit values never reach the
    # query text. ``client.query.cypher`` is the portable read-only accessor and
    # validates that the statement contains no write clauses.
    query = """
    MATCH (e:Entity)
    WHERE e.enriched_description IS NULL
      AND e.enrichment_error IS NULL
      AND ($types IS NULL OR e.type IN $types)
    RETURN e.id AS id, e.name AS name, e.type AS type, e.description AS description
    ORDER BY e.name
    LIMIT $limit
    """

    result = await client.query.cypher(
        query,
        {
            "types": [t.upper() for t in entity_types] if entity_types else None,
            "limit": limit if limit else 100_000,
        },
    )
    return [dict(record) for record in result]


async def get_enrichment_status(client: MemoryClient) -> dict:
    """Get current enrichment status of all entities.

    Returns:
        Dict with counts: total, enriched, pending, errors
    """
    query = """
    MATCH (e:Entity)
    RETURN
        count(e) AS total,
        count(CASE WHEN e.enriched_description IS NOT NULL THEN 1 END) AS enriched,
        count(CASE WHEN e.enriched_description IS NULL AND e.enrichment_error IS NULL THEN 1 END) AS pending,
        count(CASE WHEN e.enrichment_error IS NOT NULL THEN 1 END) AS errors
    """

    result = await client.query.cypher(query)
    record = result[0] if result else {}
    return {
        "total": record.get("total", 0),
        "enriched": record.get("enriched", 0),
        "pending": record.get("pending", 0),
        "errors": record.get("errors", 0),
    }


async def update_entity_enrichment(
    client: MemoryClient,
    entity_id: str,
    enrichment_data: dict,
) -> None:
    """Update entity with enrichment data.

    Args:
        client: Memory client
        entity_id: Entity UUID
        enrichment_data: Dict with enrichment fields
    """
    query = """
    MATCH (e:Entity {id: $id})
    SET e.enriched_description = $enriched_description,
        e.wikipedia_url = $wikipedia_url,
        e.wikidata_id = $wikidata_id,
        e.image_url = $image_url,
        e.enriched_at = datetime(),
        e.enrichment_provider = $provider
    """

    # No public writer exists for enrichment properties, so this goes through
    # ``client.graph`` (bolt only) rather than the private driver attribute.
    await client.graph.execute_write(
        query,
        {
            "id": entity_id,
            "enriched_description": enrichment_data.get("description"),
            "wikipedia_url": enrichment_data.get("wikipedia_url"),
            "wikidata_id": enrichment_data.get("wikidata_id"),
            "image_url": enrichment_data.get("image_url"),
            "provider": enrichment_data.get("provider", "wikimedia"),
        },
    )


async def mark_entity_not_found(
    client: MemoryClient,
    entity_id: str,
    reason: str,
) -> None:
    """Mark entity as not found in enrichment source.

    Args:
        client: Memory client
        entity_id: Entity UUID
        reason: Reason for not finding
    """
    query = """
    MATCH (e:Entity {id: $id})
    SET e.enrichment_error = $reason,
        e.enrichment_attempted_at = datetime()
    """

    await client.graph.execute_write(
        query,
        {
            "id": entity_id,
            "reason": reason,
        },
    )


def build_provider(
    name: str,
    *,
    rate_limit: float,
    diffbot_api_key: str | None = None,
):
    """Build an enrichment provider, wrapped in the library's result cache.

    ``composite`` tries Diffbot first (richer, structured) and falls back to
    Wikipedia -- the same ordering the backend's ``EnrichmentConfig`` uses.
    """
    if name == "wikimedia":
        provider = create_enrichment_provider("wikimedia", rate_limit=rate_limit)
    elif name == "diffbot":
        if not diffbot_api_key:
            raise SystemExit("--provider diffbot requires DIFFBOT_API_KEY (or --diffbot-api-key)")
        provider = create_enrichment_provider("diffbot", api_key=diffbot_api_key)
    elif name == "composite":
        providers = []
        if diffbot_api_key:
            providers.append(create_enrichment_provider("diffbot", api_key=diffbot_api_key))
        providers.append(create_enrichment_provider("wikimedia", rate_limit=rate_limit))
        provider = CompositeEnrichmentProvider(providers)
    else:  # pragma: no cover - argparse restricts the choices
        raise SystemExit(f"Unknown provider: {name}")
    # Caching means a re-run (or a name that appears under several entities)
    # does not pay for the same API call twice.
    return CachedEnrichmentProvider(provider, ttl_hours=168)


async def enrich_entities(
    client: MemoryClient,
    entity_types: list[str] | None = None,
    limit: int | None = None,
    rate_limit: float = 0.5,
    dry_run: bool = False,
    provider_name: str = "wikimedia",
    diffbot_api_key: str | None = None,
) -> EnrichmentStats:
    """Enrich entities with data from the selected provider.

    Args:
        client: Memory client
        entity_types: Filter by entity types
        limit: Maximum entities to process
        rate_limit: Seconds between API calls (default 0.5 = 2 req/sec)
        dry_run: If True, don't actually update entities
        provider_name: "wikimedia", "diffbot" or "composite"
        diffbot_api_key: Diffbot key (required for diffbot/composite)

    Returns:
        EnrichmentStats with counts
    """
    stats = EnrichmentStats()

    # Get entities to enrich
    print(f"\n{color('Fetching entities to enrich...', Colors.CYAN)}")
    entities = await get_unenriched_entities(client, entity_types, limit)
    stats.total = len(entities)

    if stats.total == 0:
        print(f"{color('✓ All entities are already enriched!', Colors.GREEN)}")
        return stats

    print(f"Found {color(str(stats.total), Colors.BOLD)} entities to enrich")

    if dry_run:
        print(f"{color('DRY RUN - no changes will be made', Colors.YELLOW)}")
        for entity in entities[:10]:
            print(f"  Would enrich: {entity['name']} ({entity['type']})")
        if stats.total > 10:
            print(f"  ... and {stats.total - 10} more")
        return stats

    provider = build_provider(
        provider_name,
        rate_limit=rate_limit,
        diffbot_api_key=diffbot_api_key,
    )

    print(f"\n{color('Starting enrichment...', Colors.CYAN)}")
    print(f"Rate limit: {1 / rate_limit:.1f} requests/second")
    print()

    start_time = time.time()

    for i, entity in enumerate(entities):
        entity_name = entity["name"]
        entity_type = entity["type"]
        entity_id = entity["id"]

        # Print progress
        print_progress(i, stats.total, stats, start_time, entity_name)

        # RATE_LIMITED used to `continue`, which advanced the loop and left the
        # entity neither enriched nor marked. Retry the SAME entity instead.
        for attempt in range(MAX_ATTEMPTS):
            try:
                result = await provider.enrich(
                    entity_name,
                    entity_type,
                    context=entity.get("description"),
                )

                if result.status == EnrichmentStatus.SUCCESS and result.has_data():
                    await update_entity_enrichment(
                        client,
                        entity_id,
                        {
                            "description": result.description,
                            "wikipedia_url": result.wikipedia_url,
                            "wikidata_id": result.wikidata_id,
                            "image_url": result.image_url,
                            "provider": provider_name,
                        },
                    )
                    stats.enriched += 1
                    break

                if result.status == EnrichmentStatus.NOT_FOUND:
                    await mark_entity_not_found(client, entity_id, "Not found in enrichment source")
                    stats.not_found += 1
                    break

                if result.status == EnrichmentStatus.RATE_LIMITED:
                    stats.rate_limited += 1
                    if attempt == MAX_ATTEMPTS - 1:
                        await mark_entity_not_found(client, entity_id, "rate_limited")
                        stats.errors += 1
                        break
                    # Exponential backoff, then retry this entity.
                    await asyncio.sleep(rate_limit * 5 * (attempt + 1))
                    continue

                await mark_entity_not_found(
                    client, entity_id, result.error_message or "Unknown error"
                )
                stats.errors += 1
                break

            except Exception as e:
                logger.error(f"Error enriching {entity_name}: {e}")
                await mark_entity_not_found(client, entity_id, str(e))
                stats.errors += 1
                break

        # Rate limiting
        await asyncio.sleep(rate_limit)

    # Final progress
    print_progress(stats.total, stats.total, stats, start_time, "Complete!")
    print()  # New line after progress bar

    return stats


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enrich entities with Wikipedia data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Enrich all entities
  python enrich_entities.py

  # Enrich only PERSON and ORGANIZATION entities
  python enrich_entities.py --types PERSON ORGANIZATION

  # Enrich with slower rate limit (1 req/sec)
  python enrich_entities.py --rate-limit 1.0

  # Preview what would be enriched
  python enrich_entities.py --dry-run

  # Check enrichment status
  python enrich_entities.py --status
        """,
    )

    parser.add_argument(
        "--types",
        "-t",
        nargs="+",
        help="Entity types to enrich (e.g., PERSON ORGANIZATION LOCATION)",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        help="Maximum number of entities to enrich",
    )
    parser.add_argument(
        "--provider",
        "-p",
        choices=["wikimedia", "diffbot", "composite"],
        default="wikimedia",
        help=(
            "Enrichment source (default: wikimedia). 'diffbot' and 'composite' "
            "need DIFFBOT_API_KEY; 'composite' tries Diffbot then Wikipedia."
        ),
    )
    parser.add_argument(
        "--diffbot-api-key",
        default=os.getenv("DIFFBOT_API_KEY"),
        help="Diffbot API key (env: DIFFBOT_API_KEY)",
    )
    parser.add_argument(
        "--rate-limit",
        "-r",
        type=float,
        default=0.5,
        help="Seconds between API calls (default: 0.5 = 2 req/sec)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be enriched without making changes",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current enrichment status and exit",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )
    add_neo4j_args(parser)
    add_model_args(parser)

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Print header
    print()
    print(color("═" * 60, Colors.MAGENTA))
    print(color("  Entity Enrichment", Colors.BOLD))
    print(color("═" * 60, Colors.MAGENTA))

    # One helper builds the settings for every script, so the loader, the
    # backfills and the running backend all agree on the embedding space.
    settings = build_memory_settings(args)

    print(f"\n{color('Connecting to Neo4j...', Colors.CYAN)}")

    async with MemoryClient(settings) as client:
        print(f"{color('✓ Connected', Colors.GREEN)}")

        # Show status
        status = await get_enrichment_status(client)
        print(f"\n{color('Current Status:', Colors.BOLD)}")
        print(f"  Total entities:    {status['total']:,}")
        print(f"  {color('Enriched:', Colors.GREEN)}        {status['enriched']:,}")
        print(f"  {color('Pending:', Colors.YELLOW)}         {status['pending']:,}")
        print(f"  {color('Errors:', Colors.RED)}          {status['errors']:,}")

        if status["total"] > 0:
            pct = status["enriched"] / status["total"] * 100
            print(f"  Coverage:          {pct:.1f}%")

        if args.status:
            return

        if status["pending"] == 0:
            print(f"\n{color('✓ All entities are already enriched!', Colors.GREEN)}")
            return

        # Estimate time
        pending = status["pending"]
        if args.limit:
            pending = min(pending, args.limit)
        estimated_time = pending * args.rate_limit
        print(f"\n{color('Estimated time:', Colors.DIM)} {format_time(estimated_time)}")

        # Run enrichment
        stats = await enrich_entities(
            client,
            entity_types=args.types,
            limit=args.limit,
            rate_limit=args.rate_limit,
            dry_run=args.dry_run,
            provider_name=args.provider,
            diffbot_api_key=args.diffbot_api_key,
        )

        # Print summary
        print()
        print(color("═" * 60, Colors.MAGENTA))
        print(color("  Enrichment Complete", Colors.BOLD))
        print(color("═" * 60, Colors.MAGENTA))
        print(f"  Processed:     {stats.total:,}")
        print(f"  {color('Enriched:', Colors.GREEN)}     {stats.enriched:,}")
        print(f"  {color('Not found:', Colors.YELLOW)}    {stats.not_found:,}")
        print(f"  {color('Errors:', Colors.RED)}       {stats.errors:,}")
        if stats.rate_limited > 0:
            print(f"  {color('Rate limited:', Colors.YELLOW)} {stats.rate_limited:,}")
        print(f"  Success rate:  {stats.success_rate:.1f}%")
        print()


if __name__ == "__main__":
    asyncio.run(main())
