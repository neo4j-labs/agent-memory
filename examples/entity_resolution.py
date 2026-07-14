#!/usr/bin/env python3
"""
Entity resolution example for neo4j-agent-memory.

This example demonstrates the entity resolution capabilities:
- Exact matching
- Fuzzy matching (requires rapidfuzz)
- Semantic matching (requires embeddings)
- Composite resolution

Requirements:
    - pip install neo4j-agent-memory[fuzzy,openai]
    - OPENAI_API_KEY environment variable set (for semantic matching)
"""

import asyncio

from neo4j_agent_memory.resolution import (
    CompositeResolver,
    ExactMatchResolver,
    FuzzyMatchResolver,
)


async def main() -> None:
    print("=" * 60)
    print("Neo4j Agent Memory - Entity Resolution Demo")
    print("=" * 60)

    # Sample existing entities
    existing_entities = [
        "John Smith",
        "Acme Corporation",
        "New York City",
        "Microsoft",
        "San Francisco",
    ]

    print(f"\nExisting entities: {existing_entities}")

    # =================================================================
    # Exact Match Resolution
    # =================================================================
    print("\n" + "=" * 40)
    print("1. Exact Match Resolution")
    print("=" * 40)

    exact_resolver = ExactMatchResolver()

    test_cases = [
        ("John Smith", "PERSON"),  # Exact match
        ("john smith", "PERSON"),  # Case-insensitive
        ("Jon Smith", "PERSON"),  # Typo - no match
        ("Microsoft", "ORGANIZATION"),  # Exact match
    ]

    for name, entity_type in test_cases:
        result = await exact_resolver.resolve(
            name, entity_type, existing_entities=existing_entities
        )
        # Check if resolved to an existing entity (exact or case-insensitive match)
        matched = result.canonical_name.lower() in [e.lower() for e in existing_entities]
        match_info = "✓ matched" if matched else "✗ no match"
        print(f"   '{name}' -> '{result.canonical_name}' ({match_info})")

    # =================================================================
    # Fuzzy Match Resolution
    # =================================================================
    print("\n" + "=" * 40)
    print("2. Fuzzy Match Resolution")
    print("=" * 40)

    try:
        fuzzy_resolver = FuzzyMatchResolver(threshold=0.8)

        fuzzy_cases = [
            ("Jon Smith", "PERSON"),  # Typo
            ("Jhon Smith", "PERSON"),  # Another typo
            ("Acme Corp", "ORGANIZATION"),  # Abbreviation
            ("ACME CORPORATION", "ORGANIZATION"),  # All caps
            ("New York", "LOCATION"),  # Partial
            ("Random Company", "ORGANIZATION"),  # No match
        ]

        for name, entity_type in fuzzy_cases:
            result = await fuzzy_resolver.resolve(
                name, entity_type, existing_entities=existing_entities
            )
            if result.canonical_name != name:
                print(
                    f"   '{name}' -> '{result.canonical_name}' "
                    f"(confidence: {result.confidence:.2f})"
                )
            else:
                print(f"   '{name}' -> no match found")

    except Exception as e:
        print(f"   ⚠️  Fuzzy matching not available: {e}")
        print("   Install with: pip install neo4j-agent-memory[fuzzy]")

    # =================================================================
    # Composite Resolution
    # =================================================================
    print("\n" + "=" * 40)
    print("3. Composite Resolution (Exact -> Fuzzy)")
    print("=" * 40)

    try:
        composite_resolver = CompositeResolver(
            fuzzy_threshold=0.8,
        )

        composite_cases = [
            ("John Smith", "PERSON"),  # Exact match
            ("Jon Smith", "PERSON"),  # Fuzzy match
            ("Totally Different", "PERSON"),  # No match
        ]

        for name, entity_type in composite_cases:
            result = await composite_resolver.resolve(
                name, entity_type, existing_entities=existing_entities
            )
            if result.canonical_name != name:
                print(
                    f"   '{name}' -> '{result.canonical_name}' "
                    f"(type: {result.match_type}, confidence: {result.confidence:.2f})"
                )
            else:
                print(f"   '{name}' -> new entity (no match)")

    except Exception as e:
        print(f"   ⚠️  Composite resolution error: {e}")

    # =================================================================
    # Batch Resolution
    # =================================================================
    print("\n" + "=" * 40)
    print("4. Batch Resolution with Deduplication")
    print("=" * 40)

    try:
        resolver = CompositeResolver()

        # Entities to resolve (note duplicates with different cases/typos)
        batch_entities = [
            ("John Smith", "PERSON"),
            ("Jane Doe", "PERSON"),
            ("john smith", "PERSON"),  # Duplicate
            ("Jon Smith", "PERSON"),  # Typo duplicate
            ("Jane Doe", "PERSON"),  # Exact duplicate
        ]

        print(f"   Input: {[e[0] for e in batch_entities]}")

        results = await resolver.resolve_batch(batch_entities)

        unique_canonical = set(r.canonical_name for r in results)
        print(f"   Unique entities: {sorted(unique_canonical)}")

        for original, result in zip(batch_entities, results):
            if original[0] != result.canonical_name:
                print(f"   '{original[0]}' resolved to '{result.canonical_name}'")

    except Exception as e:
        print(f"   ⚠️  Batch resolution error: {e}")

    print("\n✅ Demo complete!")


if __name__ == "__main__":
    asyncio.run(main())
