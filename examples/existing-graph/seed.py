"""Load the pre-library Movies domain graph from ``seed_domain_graph.cypher``.

Runs the statements in that file through the library's own Neo4j client, so
no host ``cypher-shell`` is required:

    uv run python examples/existing-graph/seed.py

Safety
------
The seed is **MERGE-only**: it never deletes anything, and re-running it is
a no-op. To start from a clean domain graph, ask for a reset explicitly:

    EXISTING_GRAPH_ALLOW_RESET=1 uv run python examples/existing-graph/seed.py --reset

``--reset`` deletes only nodes carrying the seed labels (``:Person``,
``:Movie``, ``:Genre``) and refuses to run without the confirmation
environment variable. It never issues an unscoped ``MATCH (n) DETACH
DELETE n`` — this example is aimed at production graphs, so it must be
safe to point at one.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Allow running as a standalone script (uv run python examples/existing-graph/seed.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_settings import SEED_LABELS, build_settings, describe_target

from neo4j_agent_memory import MemoryClient

SEED_FILE = Path(__file__).parent / "seed_domain_graph.cypher"
RESET_ENV_VAR = "EXISTING_GRAPH_ALLOW_RESET"


def load_statements(path: Path = SEED_FILE) -> list[str]:
    """Split the seed file into executable statements, dropping comments."""
    cypher = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("//")
    )
    return [statement.strip() for statement in cypher.split(";") if statement.strip()]


def seed_label_predicate(variable: str = "n") -> str:
    """``n:Person OR n:Movie OR n:Genre`` — the only nodes we ever delete."""
    return " OR ".join(f"{variable}:{label}" for label in SEED_LABELS)


async def seed(*, reset: bool) -> None:
    settings = build_settings()
    print(f"==> Seeding {describe_target()}")

    async with MemoryClient(settings) as client:
        totals = await client.query.cypher(
            f"""
            MATCH (n)
            RETURN count(n) AS total,
                   count(CASE WHEN {seed_label_predicate()} THEN 1 END) AS seed_nodes
            """
        )
        total, seed_nodes = totals[0]["total"], totals[0]["seed_nodes"]
        print(f"    database holds {total} nodes ({seed_nodes} with a seed label)")

        if reset:
            if os.getenv(RESET_ENV_VAR) != "1":
                raise SystemExit(
                    f"--reset would DETACH DELETE the {seed_nodes} node(s) labelled "
                    f"{', '.join(':' + label for label in SEED_LABELS)}. Re-run with "
                    f"{RESET_ENV_VAR}=1 to confirm."
                )
            # Scoped to the seed labels — never an unscoped delete of every node.
            await client.graph.execute_write(
                f"MATCH (n) WHERE {seed_label_predicate()} DETACH DELETE n"
            )
            print(f"    reset: deleted {seed_nodes} node(s) carrying a seed label")

        statements = load_statements()
        # `client.query` is read-only on both backends; write Cypher goes
        # through `client.graph`, which is bolt-only.
        for statement in statements:
            await client.graph.execute_write(statement)
        print(f"    applied {len(statements)} MERGE statements")

        rows = await client.query.cypher(
            f"""
            MATCH (n) WHERE {seed_label_predicate()}
            RETURN labels(n) AS labels, count(n) AS count
            ORDER BY labels
            """
        )
        for row in rows:
            print(f"    {':'.join(row['labels']):<10} {row['count']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(f"Delete nodes carrying the seed labels before loading. Requires {RESET_ENV_VAR}=1."),
    )
    args = parser.parse_args()
    asyncio.run(seed(reset=args.reset))


if __name__ == "__main__":
    main()
