#!/usr/bin/env python3
"""
Background Entity Enrichment Example.

This example demonstrates the entity enrichment system that fetches
additional data from external services (Wikipedia, Diffbot) about
extracted entities.

Key features demonstrated:
- Configuring enrichment providers (Wikimedia, Diffbot)
- Automatic background enrichment when adding entities
- Direct provider usage for manual enrichment
- Caching and composite providers
- Checking enrichment results

Requirements:
    - pip install neo4j-agent-memory httpx
    - For Diffbot: set DIFFBOT_API_KEY environment variable
    - Optional: Neo4j running for full integration demo

Environment variables can be set in examples/.env file.
"""

import asyncio
import os
from pathlib import Path


def load_env_files():
    """Load environment variables from .env files."""
    try:
        from dotenv import load_dotenv

        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            load_dotenv(env_file)
            print(f"Loaded environment from {env_file}")
    except ImportError:
        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip("\"'")
                        if key and key not in os.environ:
                            os.environ[key] = value
            print(f"Loaded environment from {env_file}")


load_env_files()


async def demo_direct_provider_usage():
    """Demonstrate using enrichment providers directly without Neo4j."""
    print("=" * 60)
    print("DEMO 1: Direct Provider Usage (No Neo4j Required)")
    print("=" * 60)

    # Check if httpx is available
    try:
        import httpx  # noqa: F401
    except ImportError:
        print("\nERROR: httpx is required for enrichment providers")
        print("Install with: pip install httpx")
        return

    from neo4j_agent_memory.enrichment import (
        EnrichmentStatus,
        WikimediaProvider,
    )

    # Create Wikimedia provider (free, no API key needed)
    print("\n--- WikimediaProvider ---")
    provider = WikimediaProvider(
        rate_limit=0.5,  # 0.5s between requests (2 req/sec max)
        language="en",
    )

    # Enrich a person
    print("\nEnriching 'Albert Einstein' (PERSON)...")
    result = await provider.enrich("Albert Einstein", "PERSON")

    if result.status == EnrichmentStatus.SUCCESS:
        print(f"  Status: {result.status.value}")
        print(
            f"  Description: {result.description[:100]}..."
            if result.description
            else "  Description: None"
        )
        print(f"  Wikipedia URL: {result.wikipedia_url}")
        print(f"  Wikidata ID: {result.wikidata_id}")
        print(
            f"  Image URL: {result.image_url[:60]}..." if result.image_url else "  Image URL: None"
        )
    else:
        print(f"  Status: {result.status.value}")
        print(f"  Error: {result.error_message}")

    # Enrich an organization
    print("\nEnriching 'Apple Inc' (ORGANIZATION)...")
    result = await provider.enrich("Apple Inc", "ORGANIZATION")

    if result.status == EnrichmentStatus.SUCCESS:
        print(f"  Status: {result.status.value}")
        print(
            f"  Description: {result.description[:100]}..."
            if result.description
            else "  Description: None"
        )
        print(f"  Wikipedia URL: {result.wikipedia_url}")
    else:
        print(f"  Status: {result.status.value}")

    # Enrich a location
    print("\nEnriching 'Paris' (LOCATION)...")
    result = await provider.enrich("Paris", "LOCATION")

    if result.status == EnrichmentStatus.SUCCESS:
        print(f"  Status: {result.status.value}")
        print(
            f"  Description: {result.description[:100]}..."
            if result.description
            else "  Description: None"
        )
        print(f"  Wikipedia URL: {result.wikipedia_url}")
    else:
        print(f"  Status: {result.status.value}")

    # Test unsupported type
    print("\nEnriching 'Meeting' (MEETING - unsupported type)...")
    result = await provider.enrich("Meeting", "MEETING")
    print(f"  Status: {result.status.value}")  # Should be SKIPPED


async def demo_diffbot_provider():
    """Demonstrate using Diffbot provider (requires API key)."""
    print("\n" + "=" * 60)
    print("DEMO 2: Diffbot Provider (Requires API Key)")
    print("=" * 60)

    diffbot_api_key = os.getenv("DIFFBOT_API_KEY")
    if not diffbot_api_key:
        print("\nSkipping Diffbot demo - DIFFBOT_API_KEY not set")
        print("Set DIFFBOT_API_KEY in your environment to enable this demo")
        return

    try:
        import httpx  # noqa: F401
    except ImportError:
        print("\nERROR: httpx is required for enrichment providers")
        return

    from neo4j_agent_memory.enrichment import DiffbotProvider, EnrichmentStatus

    print("\n--- DiffbotProvider ---")
    provider = DiffbotProvider(
        api_key=diffbot_api_key,
        rate_limit=0.2,  # 5 req/sec
    )

    # Enrich an organization
    print("\nEnriching 'Microsoft' (ORGANIZATION)...")
    result = await provider.enrich("Microsoft", "ORGANIZATION")

    if result.status == EnrichmentStatus.SUCCESS:
        print(f"  Status: {result.status.value}")
        print(
            f"  Description: {result.description[:100]}..."
            if result.description
            else "  Description: None"
        )
        print(
            f"  Related entities: {result.related_entities[:5]}"
            if result.related_entities
            else "  Related: None"
        )
        print(f"  Metadata types: {result.metadata.get('types', [])}")
        print(f"  Importance: {result.metadata.get('importance', 'N/A')}")
    else:
        print(f"  Status: {result.status.value}")
        print(f"  Error: {result.error_message}")


async def demo_caching_and_composite():
    """Demonstrate caching and composite providers."""
    print("\n" + "=" * 60)
    print("DEMO 3: Caching and Composite Providers")
    print("=" * 60)

    try:
        import httpx  # noqa: F401
    except ImportError:
        print("\nERROR: httpx is required for enrichment providers")
        return

    from neo4j_agent_memory.enrichment import (
        CachedEnrichmentProvider,
        CompositeEnrichmentProvider,
        EnrichmentStatus,
        WikimediaProvider,
    )

    # Create a cached provider
    print("\n--- CachedEnrichmentProvider ---")
    base_provider = WikimediaProvider()
    cached_provider = CachedEnrichmentProvider(
        base_provider,
        ttl_hours=24,  # Cache for 24 hours
    )

    # First call - fetches from API
    import time

    start = time.time()
    result1 = await cached_provider.enrich("Elon Musk", "PERSON")
    time1 = time.time() - start
    print(f"\nFirst call (API): {time1:.3f}s - Status: {result1.status.value}")

    # Second call - returns from cache
    start = time.time()
    result2 = await cached_provider.enrich("Elon Musk", "PERSON")
    time2 = time.time() - start
    print(f"Second call (cached): {time2:.3f}s - Status: {result2.status.value}")
    print(f"  Cache speedup: {time1 / time2:.1f}x faster")

    # Composite provider - tries multiple providers
    print("\n--- CompositeEnrichmentProvider ---")

    # Create providers (Diffbot would be first if API key available)
    providers = [WikimediaProvider()]

    diffbot_api_key = os.getenv("DIFFBOT_API_KEY")
    if diffbot_api_key:
        from neo4j_agent_memory.enrichment import DiffbotProvider

        providers.insert(0, DiffbotProvider(api_key=diffbot_api_key))
        print("Using: DiffbotProvider -> WikimediaProvider (fallback)")
    else:
        print("Using: WikimediaProvider only (DIFFBOT_API_KEY not set)")

    composite = CompositeEnrichmentProvider(providers)

    result = await composite.enrich("Amazon", "ORGANIZATION")
    if result.status == EnrichmentStatus.SUCCESS:
        print(
            f"  Enriched 'Amazon': {result.description[:80]}..."
            if result.description
            else "  No description"
        )


async def demo_with_neo4j():
    """Demonstrate full integration with Neo4j and background enrichment."""
    print("\n" + "=" * 60)
    print("DEMO 4: Full Integration with Neo4j")
    print("=" * 60)

    # Check for required dependencies
    try:
        import httpx  # noqa: F401
    except ImportError:
        print("\nERROR: httpx is required for enrichment providers")
        return

    neo4j_uri = os.getenv("NEO4J_URI")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "password")

    if not neo4j_uri:
        print("\nSkipping Neo4j demo - NEO4J_URI not set")
        print("Set NEO4J_URI and NEO4J_PASSWORD to enable this demo")
        print("Example: NEO4J_URI=bolt://localhost:7687")
        return

    from pydantic import SecretStr

    from neo4j_agent_memory import (
        EmbeddingConfig,
        EmbeddingProvider,
        MemoryClient,
        MemorySettings,
        Neo4jConfig,
    )
    from neo4j_agent_memory.config.settings import EnrichmentConfig, EnrichmentProvider

    # Check for OpenAI API key
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if openai_api_key:
        embedding_config = EmbeddingConfig(
            provider=EmbeddingProvider.OPENAI,
            model="text-embedding-3-small",
        )
    else:
        try:
            import sentence_transformers  # noqa: F401

            embedding_config = EmbeddingConfig(
                provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
                model="all-MiniLM-L6-v2",
                dimensions=384,
            )
        except ImportError:
            print("\nERROR: Need either OPENAI_API_KEY or sentence-transformers")
            return

    # Configure settings with enrichment enabled
    settings = MemorySettings(
        neo4j=Neo4jConfig(
            uri=neo4j_uri,
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(neo4j_password),
        ),
        embedding=embedding_config,
        enrichment=EnrichmentConfig(
            enabled=True,
            providers=[EnrichmentProvider.WIKIMEDIA],
            background_enabled=True,
            cache_results=True,
            entity_types=["PERSON", "ORGANIZATION", "LOCATION", "EVENT"],
            min_confidence=0.5,
        ),
    )

    print("\nConnecting to Neo4j with enrichment enabled...")
    async with MemoryClient(settings) as client:
        print("Connected!")

        # Add some entities - they will be enriched in the background
        print("\nAdding entities (enrichment happens in background)...")

        entities_to_add = [
            ("Marie Curie", "PERSON", "Nobel Prize-winning physicist"),
            ("Google", "ORGANIZATION", "Technology company"),
            ("Tokyo", "LOCATION", "Capital of Japan"),
        ]

        added_entities = []
        for name, entity_type, description in entities_to_add:
            entity, dedup_result = await client.long_term.add_entity(
                name=name,
                entity_type=entity_type,
                description=description,
                confidence=0.9,
            )
            added_entities.append(entity)
            print(f"  Added: {name} ({entity_type})")

        # Wait a moment for background enrichment
        print("\nWaiting for background enrichment to complete...")
        await asyncio.sleep(3)

        # Check if entities were enriched
        print("\nChecking enrichment results:")
        for entity in added_entities:
            # Fetch updated entity by name (same node, now with enrichment fields)
            updated = await client.long_term.get_entity_by_name(entity.name)
            if updated:
                enriched_desc = getattr(updated, "enriched_description", None)
                wiki_url = getattr(updated, "wikipedia_url", None)
                enriched_at = getattr(updated, "enriched_at", None)

                if enriched_desc or wiki_url:
                    print(f"\n  {updated.name}:")
                    print(f"    Enriched: Yes (at {enriched_at})")
                    if enriched_desc:
                        print(f"    Description: {enriched_desc[:80]}...")
                    if wiki_url:
                        print(f"    Wikipedia: {wiki_url}")
                else:
                    print(f"\n  {updated.name}: Not yet enriched (may still be processing)")

        print("\n" + "-" * 40)
        print("Note: Background enrichment is asynchronous.")
        print("Entities may take a few seconds to be enriched.")
        print("Check Neo4j for 'enriched_description' property on Entity nodes.")


async def main():
    """Run all demos."""
    print("=" * 60)
    print("Neo4j Agent Memory - Entity Enrichment Examples")
    print("=" * 60)

    # Demo 1: Direct provider usage (no external services needed except HTTP)
    await demo_direct_provider_usage()

    # Demo 2: Diffbot provider (requires API key)
    await demo_diffbot_provider()

    # Demo 3: Caching and composite providers
    await demo_caching_and_composite()

    # Demo 4: Full Neo4j integration
    await demo_with_neo4j()

    print("\n" + "=" * 60)
    print("Enrichment Examples Complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
