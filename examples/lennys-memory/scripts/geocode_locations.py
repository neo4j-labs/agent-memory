#!/usr/bin/env python3
"""Geocode Location entities in the memory graph.

Adds latitude/longitude to Location entities that don't have coordinates yet,
using one public batch API (``long_term.geocode_locations``). Nominatim
(OpenStreetMap) is the default: free, and rate-limited to ~1 request/second.

Usage:
    python geocode_locations.py [options]

Options:
    --provider nominatim|google     Geocoding provider (default: nominatim)
    --api-key KEY                   API key (required for Google)
    --batch-size N                  Batch size for processing (default: 50)
    --skip-existing / --no-skip-existing
                                    Skip locations that already have coordinates
                                    (default: skip). --no-skip-existing exists but
                                    the library always selects only un-geocoded
                                    locations today -- see the flag's help text.
    -v, --verbose                   Show detailed progress
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import (  # noqa: E402
    Colors,
    add_model_args,
    add_neo4j_args,
    build_memory_settings,
    color,
    load_backend_env,
)
from pydantic import SecretStr  # noqa: E402

from neo4j_agent_memory import (  # noqa: E402
    GeocodingConfig,
    GeocodingProvider,
    MemoryClient,
)

load_backend_env()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Geocode Location entities in the memory graph")
    parser.add_argument(
        "--provider",
        choices=["nominatim", "google"],
        default="nominatim",
        help="Geocoding provider (default: nominatim)",
    )
    parser.add_argument(
        "--api-key",
        help="API key for geocoding provider (required for Google)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch size for processing (default: 50)",
    )
    parser.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Skip locations that already have coordinates (default: enabled). "
            "NOTE: the library's geocode_locations() currently only ever selects "
            "locations with no coordinates, so --no-skip-existing is forwarded "
            "but cannot yet force a re-geocode; clear e.location first."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed progress",
    )
    add_neo4j_args(parser)
    add_model_args(parser)
    args = parser.parse_args()

    if args.provider == "google" and not args.api_key:
        api_key = os.getenv("GOOGLE_GEOCODING_API_KEY")
        if not api_key:
            print("Error: Google geocoding requires an API key.")
            print("Set GOOGLE_GEOCODING_API_KEY environment variable or use --api-key")
            sys.exit(1)
        args.api_key = api_key

    # The MemoryClient wires the geocoder to LongTermMemory from this config.
    geocoding_config = GeocodingConfig(
        enabled=True,
        provider=GeocodingProvider.GOOGLE
        if args.provider == "google"
        else GeocodingProvider.NOMINATIM,
        api_key=SecretStr(args.api_key) if args.api_key else None,
        cache_results=True,
        user_agent="lennys-memory/1.0",
    )

    print(f"{color('Geocoding Location entities', Colors.BOLD + Colors.CYAN)}")
    print(f"{color('Provider', Colors.DIM)}   {args.provider.title()}")
    settings = build_memory_settings(args, geocoding=geocoding_config)

    async with MemoryClient(settings) as client:

        def on_progress(processed: int, total: int) -> None:
            if args.verbose or processed % 10 == 0 or processed == total:
                print(f"  Progress: {processed}/{total} locations processed")

        stats = await client.long_term.geocode_locations(
            batch_size=args.batch_size,
            skip_existing=args.skip_existing,
            on_progress=on_progress,
        )

        print()
        print("Geocoding complete!")
        # geocode_locations() returns {"processed", "geocoded"} -- anything
        # processed but not geocoded is a provider miss.
        processed = stats["processed"]
        geocoded = stats["geocoded"]
        print(f"  Processed:  {processed} locations")
        print(f"  Geocoded:   {geocoded} locations")
        print(f"  Not found:  {processed - geocoded} locations")

        if stats["geocoded"] > 0:
            print()
            print("You can now use spatial queries like:")
            print("  await memory.long_term.search_locations_near(lat, lon, radius_km=10)")
            print("  await memory.long_term.get_location_coordinates(entity_id)")


if __name__ == "__main__":
    asyncio.run(main())
