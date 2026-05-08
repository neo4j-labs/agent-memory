#!/usr/bin/env bash
# End-to-end runner for the existing-graph example.
#
# Loads a tiny Movies domain graph, adopts it as long-term memory entities,
# then writes messages and verifies the resulting MENTIONS edges link to
# the pre-existing nodes.

set -euo pipefail

# Run from the repo root so module-relative imports work.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
cd "$REPO_ROOT"

NEO4J_URI=${NEO4J_URI:-bolt://localhost:7687}
NEO4J_USERNAME=${NEO4J_USERNAME:-neo4j}
NEO4J_PASSWORD=${NEO4J_PASSWORD:-password}

if ! command -v cypher-shell > /dev/null; then
    echo "cypher-shell not on PATH. Install Neo4j or run inside neo4j Docker." >&2
    exit 1
fi

echo "==> Loading seed Movies graph..."
cypher-shell -a "$NEO4J_URI" -u "$NEO4J_USERNAME" -p "$NEO4J_PASSWORD" \
    < examples/existing-graph/seed_domain_graph.cypher

echo "==> Running adopt_existing_graph()..."
uv run python examples/existing-graph/adopt.py

echo "==> Writing messages and verifying MENTIONS edges..."
uv run python examples/existing-graph/memory_io.py

echo "==> Done."
