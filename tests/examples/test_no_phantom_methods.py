"""Cross-reference scan: every memory-layer method call in `examples/` must
reference a real public method on the actual class.

This catches the kind of drift that broke `get_entity`, `get_messages`,
`get_entity_coordinates`, and `delete_conversation` calls in examples that
otherwise compiled fine and passed structural smoke tests. The bugs were
silent at import time because the calls go through `await client.<layer>.X()`
where `X` is only resolved at runtime — and several of the broken examples
swallowed the resulting AttributeError in `except Exception: pass`, so they
ran to "completion" without doing anything.

The scan is purely static (regex over source) — it does not import the
example files or require Neo4j. It only needs the `neo4j_agent_memory`
package importable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"


def _public_attrs(cls) -> set[str]:
    return {n for n in dir(cls) if not n.startswith("_")}


@pytest.fixture(scope="module")
def layer_apis() -> dict[str, set[str]]:
    """Public method/attribute names on each memory layer class.

    Note: `client.schema` returns `graph.schema.SchemaManager` (which has
    `adopt_existing_graph`), not `schema.SchemaManager` (the persistence
    one). Two classes share the name; pick the right one.
    """
    from neo4j_agent_memory.graph.client import Neo4jClient
    from neo4j_agent_memory.graph.schema import SchemaManager
    from neo4j_agent_memory.memory.buffered import BufferedWriter
    from neo4j_agent_memory.memory.consolidation import ConsolidationMemory
    from neo4j_agent_memory.memory.eval import EvalMemory
    from neo4j_agent_memory.memory.long_term import LongTermMemory
    from neo4j_agent_memory.memory.reasoning import ReasoningMemory
    from neo4j_agent_memory.memory.short_term import ShortTermMemory
    from neo4j_agent_memory.memory.users import UserMemory

    return {
        "short_term": _public_attrs(ShortTermMemory),
        "long_term": _public_attrs(LongTermMemory),
        "reasoning": _public_attrs(ReasoningMemory),
        "users": _public_attrs(UserMemory),
        "buffered": _public_attrs(BufferedWriter),
        "consolidation": _public_attrs(ConsolidationMemory),
        "eval": _public_attrs(EvalMemory),
        "schema": _public_attrs(SchemaManager),
        "graph": _public_attrs(Neo4jClient),
    }


def _iter_example_py_files():
    for p in EXAMPLES_DIR.rglob("*.py"):
        if ".venv" in p.parts or "node_modules" in p.parts:
            continue
        yield p


@pytest.mark.imports
def test_no_phantom_layer_methods_in_examples(layer_apis):
    """Every `.<layer>.<method>(` in examples must reference a real method."""
    layer_names = "|".join(layer_apis.keys())
    pattern = re.compile(rf"\.({layer_names})\.(\w+)\(")

    drift: list[str] = []
    for path in _iter_example_py_files():
        source = path.read_text()
        for match in pattern.finditer(source):
            layer, method = match.group(1), match.group(2)
            if method not in layer_apis[layer]:
                line = source[: match.start()].count("\n") + 1
                rel = path.relative_to(REPO_ROOT)
                drift.append(f"{rel}:{line}  .{layer}.{method}()")

    assert not drift, (
        "Examples reference methods that don't exist on the corresponding "
        "memory-layer class. Either rename to the real method, or add the "
        "method to the library:\n  " + "\n  ".join(drift)
    )
