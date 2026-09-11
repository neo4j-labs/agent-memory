#!/usr/bin/env bash
# End-to-end runner for the existing-graph example.
#
#   1. seed.py      — load a tiny pre-library Movies domain graph (MERGE only)
#   2. adopt.py     — dry run, then adopt the graph as long-term memory entities
#   3. memory_io.py — write messages and verify no duplicates were created
#   4. retrieve.py  — backfill embeddings, search, relate, traverse
#
# No host cypher-shell required: every step runs through the library's own
# Neo4j client. Nothing here deletes data — pass --reset to seed.py yourself
# (with EXISTING_GRAPH_ALLOW_RESET=1) if you want a clean domain graph.

set -euo pipefail

# Run from the repo root so the documented `uv run python examples/...` paths work.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
cd "$REPO_ROOT"

# Defaults match the repo's test container (docker-compose.test.yml,
# `make neo4j-start`). Override by exporting these before running.
export NEO4J_URI=${NEO4J_URI:-bolt://localhost:7687}
export NEO4J_USERNAME=${NEO4J_USERNAME:-neo4j}
export NEO4J_PASSWORD=${NEO4J_PASSWORD:-test-password}

echo "==> Target: $NEO4J_URI (user: $NEO4J_USERNAME)"

echo
echo "==> 1/4 Loading the seed Movies graph..."
uv run python examples/existing-graph/seed.py

echo
echo "==> 2/4 Projecting adoption (dry run), then adopting..."
uv run python examples/existing-graph/adopt.py --dry-run
uv run python examples/existing-graph/adopt.py

echo
echo "==> 3/4 Writing messages and verifying MENTIONS edges..."
uv run python examples/existing-graph/memory_io.py

echo
echo "==> 4/4 Searching, relating and traversing the adopted graph..."
uv run python examples/existing-graph/retrieve.py

echo
echo "==> Done. Re-run this script to confirm idempotency."
