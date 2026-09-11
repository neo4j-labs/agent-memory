"""Adopt the seed domain graph as long-term memory entities.

Run this *after* ``seed.py`` has loaded the Movies domain. Idempotent —
re-running on an already-adopted graph reports ``already adopted`` and
changes nothing.

    uv run python examples/existing-graph/adopt.py --dry-run   # projection only
    uv run python examples/existing-graph/adopt.py             # mutate

**Bolt only** — ``client.schema`` raises ``NotSupportedError`` on NAMS.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Allow running as a standalone script (uv run python examples/existing-graph/adopt.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_settings import (
    LABEL_TO_TYPE,
    NAME_PROPERTY_PER_LABEL,
    build_settings,
    describe_target,
)

from neo4j_agent_memory import MemoryClient


async def adopt(*, dry_run: bool = False) -> None:
    settings = build_settings()
    mode = "dry run (nothing will be written)" if dry_run else "writing"
    print(f"==> adopt_existing_graph — {mode} against {describe_target()}")

    async with MemoryClient(settings) as client:
        report = await client.schema.adopt_existing_graph(
            label_to_type=LABEL_TO_TYPE,
            # :Movie nodes use `title` rather than `name` for their display
            # name; :Person and :Genre already use `name`.
            name_property_per_label=NAME_PROPERTY_PER_LABEL,
            dry_run=dry_run,
        )

        verb = "Would adopt" if dry_run else "Adopted"
        print(
            f"{verb} {report.total_migrated} nodes "
            f"({report.total_already_adopted} already adopted, "
            f"{report.total_skipped} skipped)."
        )
        for label_report in report.by_label:
            print(
                f"  {label_report.label} → {label_report.type}: "
                f"+{label_report.migrated_count} new, "
                f"={label_report.already_adopted_count} already, "
                f"~{label_report.skipped_count} skipped"
            )

        if dry_run:
            print("  (projection only — re-run without --dry-run to apply)")
            return

        # The library properties adoption attached. Nodes that already had an
        # `id` keep it; the rest get a deterministic `<label_lc>:<name>` id.
        label_filter = " OR ".join(f"e:{label}" for label in LABEL_TO_TYPE)
        rows = await client.query.cypher(
            f"""
            MATCH (e:Entity)
            WHERE ({label_filter}) AND e.type IN $types
            RETURN e.type AS type, e.name AS name, e.id AS id, labels(e) AS labels
            ORDER BY type, name
            """,
            {"types": list(LABEL_TO_TYPE.values())},
        )
        print("\nAdopted nodes (label set, library id):")
        for row in rows:
            print(f"  {row['name']:<16} {':'.join(row['labels']):<24} id={row['id']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what adoption would change without mutating the graph.",
    )
    args = parser.parse_args()
    asyncio.run(adopt(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
