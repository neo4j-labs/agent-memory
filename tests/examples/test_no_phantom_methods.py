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

Both backends count. `client.short_term` / `.long_term` / `.reasoning` resolve
to the bolt classes on a bolt connection and to the `nams/*` classes on a
hosted one, and the library deliberately ships methods on only one side of that
pair (`get_extraction_status` and `expand_graph` are NAMS-only, for instance).
So each layer's allow-list is the *union* of both implementations: an example
may call anything either backend really provides, and still fails on an
invented name. See cross-cutting finding xc-E01 and nams-quickstart-F12.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"


def _public_attrs(cls) -> set[str]:
    return {n for n in dir(cls) if not n.startswith("_")}


def _union(*classes) -> set[str]:
    """Allow-list spanning every backend implementation of one layer."""
    names: set[str] = set()
    for cls in classes:
        names |= _public_attrs(cls)
    return names


def _build_layer_apis() -> dict[str, set[str]]:
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
    from neo4j_agent_memory.nams.long_term import NamsLongTermMemory
    from neo4j_agent_memory.nams.reasoning import NamsReasoningMemory
    from neo4j_agent_memory.nams.short_term import NamsShortTermMemory

    return {
        # Three layers exist on both backends — union them.
        "short_term": _union(ShortTermMemory, NamsShortTermMemory),
        "long_term": _union(LongTermMemory, NamsLongTermMemory),
        "reasoning": _union(ReasoningMemory, NamsReasoningMemory),
        # Bolt-only layers.
        "users": _public_attrs(UserMemory),
        "buffered": _public_attrs(BufferedWriter),
        "consolidation": _public_attrs(ConsolidationMemory),
        "eval": _public_attrs(EvalMemory),
        "schema": _public_attrs(SchemaManager),
        "graph": _public_attrs(Neo4jClient),
    }


def _build_client_accessor_apis() -> dict[str, set[str]]:
    """Accessors that hang off `MemoryClient` itself, not off a memory layer.

    `client.query` is portable (bolt and NAMS); `client.ontology` and
    `client.auth` are NAMS-only. They are matched with a narrower pattern
    (see `_client_accessor_pattern`) because names like `query` collide with
    ordinary attributes in example app code (`request.query.upper()`).
    """
    from neo4j_agent_memory.core.query import BoltCypherQuery
    from neo4j_agent_memory.nams.auth_keys import NamsAuth
    from neo4j_agent_memory.nams.ontology import NamsOntology
    from neo4j_agent_memory.nams.query import NamsCypherQuery

    return {
        "query": _union(NamsCypherQuery, BoltCypherQuery),
        "ontology": _public_attrs(NamsOntology),
        "auth": _public_attrs(NamsAuth),
    }


@pytest.fixture(scope="module")
def layer_apis() -> dict[str, set[str]]:
    return _build_layer_apis()


@pytest.fixture(scope="module")
def client_accessor_apis() -> dict[str, set[str]]:
    return _build_client_accessor_apis()


def _layer_pattern(names) -> re.Pattern[str]:
    return re.compile(rf"\.({'|'.join(names)})\.(\w+)\(")


def _client_accessor_pattern(names) -> re.Pattern[str]:
    # Only on a receiver that looks like a MemoryClient (`client`, `memory_client`,
    # `self._client`), so `request.query.upper()` is not mistaken for a library call.
    return re.compile(rf"\b\w*client\.({'|'.join(names)})\.(\w+)\(")


def find_drift(source: str, apis: dict[str, set[str]], pattern: re.Pattern[str]) -> list[str]:
    """Return `"<line>  .<layer>.<method>()"` for every call that cannot resolve.

    Pure function over source text so the guard itself is testable (see
    `test_guard_catches_a_fake_method`).
    """
    drift: list[str] = []
    for match in pattern.finditer(source):
        layer, method = match.group(1), match.group(2)
        if method not in apis[layer]:
            line = source[: match.start()].count("\n") + 1
            drift.append(f"{line}  .{layer}.{method}()")
    return drift


def _iter_example_py_files():
    for p in EXAMPLES_DIR.rglob("*.py"):
        if ".venv" in p.parts or "node_modules" in p.parts:
            continue
        yield p


@pytest.mark.imports
def test_no_phantom_layer_methods_in_examples(layer_apis):
    """Every `.<layer>.<method>(` in examples must reference a real method."""
    pattern = _layer_pattern(layer_apis)

    drift: list[str] = []
    for path in _iter_example_py_files():
        source = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT)
        drift.extend(f"{rel}:{hit}" for hit in find_drift(source, layer_apis, pattern))

    assert not drift, (
        "Examples reference methods that don't exist on the corresponding "
        "memory-layer class (bolt or NAMS). Either rename to the real method, "
        "or add the method to the library:\n  " + "\n  ".join(drift)
    )


@pytest.mark.imports
def test_no_phantom_client_accessor_methods_in_examples(client_accessor_apis):
    """Every `client.query/ontology/auth.<method>(` must reference a real method."""
    pattern = _client_accessor_pattern(client_accessor_apis)

    drift: list[str] = []
    for path in _iter_example_py_files():
        source = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT)
        drift.extend(f"{rel}:{hit}" for hit in find_drift(source, client_accessor_apis, pattern))

    assert not drift, (
        "Examples call methods that don't exist on the corresponding "
        "MemoryClient accessor:\n  " + "\n  ".join(drift)
    )


# ---------------------------------------------------------------------------
# Tests for the guard itself — a guard that cannot fail protects nothing.
# ---------------------------------------------------------------------------


@pytest.mark.imports
def test_guard_catches_a_fake_method(layer_apis):
    """A method that exists on neither backend is reported, with its line number."""
    source = (
        "async def main(client):\n"
        "    await client.long_term.add_entity('Alice', 'PERSON')\n"
        "    await client.long_term.definitely_not_a_real_method()\n"
    )

    drift = find_drift(source, layer_apis, _layer_pattern(layer_apis))

    assert drift == ["3  .long_term.definitely_not_a_real_method()"]


@pytest.mark.imports
def test_guard_catches_a_fake_client_accessor_method(client_accessor_apis):
    source = "rows = await client.query.nope('MATCH (n) RETURN n')\n"

    drift = find_drift(source, client_accessor_apis, _client_accessor_pattern(client_accessor_apis))

    assert drift == ["1  .query.nope()"]


@pytest.mark.imports
@pytest.mark.parametrize(
    ("layer", "method"),
    [
        # NAMS-only methods: flagged before the allow-lists were unioned.
        ("short_term", "get_extraction_status"),
        ("short_term", "bulk_add_messages"),
        ("short_term", "get_observations"),
        ("short_term", "get_reflections"),
        ("short_term", "create_conversation"),
        ("short_term", "list_conversations"),
        ("long_term", "expand_graph"),
        # Present on both backends.
        ("long_term", "wait_for_extraction"),
        ("long_term", "add_entity"),
        ("reasoning", "start_trace"),
    ],
)
def test_guard_accepts_real_methods_from_either_backend(layer_apis, layer, method):
    source = f"await client.{layer}.{method}()\n"

    assert find_drift(source, layer_apis, _layer_pattern(layer_apis)) == []


@pytest.mark.imports
def test_guard_ignores_unrelated_query_attributes(client_accessor_apis):
    """`request.query.upper()` in an example's API layer is not a library call."""
    source = "query_upper = request.query.upper().strip()\n"

    assert (
        find_drift(source, client_accessor_apis, _client_accessor_pattern(client_accessor_apis))
        == []
    )


# ---------------------------------------------------------------------------
# Widened guards (xc-E01)
#
# The layer/accessor patterns above only see `receiver.<layer>.<method>(`.
# Three blind spots were being exercised by shipped example code:
#
#   1. top-level `client.<method>(` — `get_context`, `get_stats`, `get_graph`,
#      `flush`, `wait_for_pending` and friends live on `MemoryClient` itself;
#   2. defensive `getattr(client.short_term, "create_conversation", None)` —
#      a rename silently takes the `None` branch instead of failing;
#   3. `client._settings` / `client._client` reach-through — public accessors
#      (`client.graph`, `client.query`) exist for both.
#
# (1) and the adapter classes are handled by the same binding-based guard: a
# receiver name on its own proves nothing (`client` also names boto3, httpx,
# docker and fastmcp clients across these examples), so the local must be
# *demonstrably* bound to the library class in the same file — either
# `x = Cls(...)` or `async with Cls(...) as x`.
# ---------------------------------------------------------------------------

import io
import tokenize

#: Library classes examples bind to a local and then call methods on.
ADAPTER_IMPORTS = (
    ("neo4j_agent_memory", "MemoryClient"),
    ("neo4j_agent_memory.integrations.langchain", "Neo4jAgentMemory"),
    ("neo4j_agent_memory.integrations.langchain", "Neo4jMemoryRetriever"),
    ("neo4j_agent_memory.integrations.langchain", "Neo4jMemoryMiddleware"),
    ("neo4j_agent_memory.integrations.pydantic_ai", "MemoryDependency"),
    ("neo4j_agent_memory.integrations.google_adk", "Neo4jMemoryService"),
    ("neo4j_agent_memory.integrations.agentcore", "HybridMemoryProvider"),
    ("neo4j_agent_memory.integrations.microsoft_agent", "Neo4jMicrosoftMemory"),
    ("neo4j_agent_memory.integrations.strands", "Neo4jSessionManager"),
    ("neo4j_agent_memory.integrations.strands", "Neo4jMemoryStore"),
    ("neo4j_agent_memory", "MemoryIntegration"),
)


def strip_comments_and_strings(source: str) -> str:
    """Blank out comments and string literals, preserving line numbers.

    So a docstring that *mentions* ``client._client.execute_read`` (several now
    explain why the example moved off it) is not read as a call.
    """
    out: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):  # pragma: no cover
        return source
    prev_row, prev_col = 1, 0
    lines = source.splitlines(keepends=True)
    for tok in tokens:
        srow, scol = tok.start
        erow, ecol = tok.end
        # Re-emit the gap between tokens verbatim (whitespace only).
        while prev_row < srow:
            out.append(lines[prev_row - 1][prev_col:])
            prev_row, prev_col = prev_row + 1, 0
        out.append(" " * (scol - prev_col))
        text = tok.string
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            # Replace with same-shape blanks so columns and rows still line up.
            text = "".join("\n" if ch == "\n" else " " for ch in text)
        out.append(text)
        prev_row, prev_col = erow, ecol
    return "".join(out)


def _getattr_pattern(layers) -> re.Pattern[str]:
    return re.compile(rf"getattr\(\s*\w*\.?({'|'.join(layers)})\s*,\s*[\"'](\w+)[\"']")


def find_getattr_drift(source: str, apis: dict[str, set[str]]) -> list[str]:
    """Defensive `getattr(client.<layer>, "name")` lookups, checked like calls."""
    drift: list[str] = []
    for match in _getattr_pattern(apis).finditer(source):
        layer, name = match.group(1), match.group(2)
        if name not in apis[layer]:
            line = source[: match.start()].count("\n") + 1
            drift.append(f'{line}  getattr(.{layer}, "{name}")')
    return drift


PRIVATE_REACH_THROUGH = re.compile(r"(?<![\w.])(?:\w*client)\.(_\w+)")


def find_private_reach_through(source: str) -> list[str]:
    """`client._settings` / `client._client` — public accessors exist for both."""
    drift: list[str] = []
    for match in PRIVATE_REACH_THROUGH.finditer(source):
        attr = match.group(1)
        if attr.startswith("__"):
            continue
        line = source[: match.start()].count("\n") + 1
        drift.append(f"{line}  client.{attr}")
    return drift


def _resolve_adapters() -> dict[str, set[str]]:
    """Adapter class name -> public attribute names, for whatever is installed."""
    import importlib

    resolved: dict[str, set[str]] = {}
    for module_name, class_name in ADAPTER_IMPORTS:
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, class_name)
        except Exception:  # optional extra not installed
            continue
        resolved[class_name] = _public_attrs(cls)
    return resolved


def find_adapter_drift(source: str, adapters: dict[str, set[str]]) -> list[str]:
    """Methods called on a local bound to a library class in the same file."""
    if not adapters:
        return []
    names = "|".join(adapters)
    # `x = Cls(...)` / `x = await Cls(...)`
    assign = re.compile(rf"(\w+)\s*=\s*(?:await\s+)?({names})\(")
    # `async with Cls(...) as x:` / `with Cls(...) as x:`
    ctx = re.compile(rf"\bwith\s+(?:await\s+)?({names})\(.*?\)\s+as\s+(\w+)", re.DOTALL)
    bindings = {m.group(1): m.group(2) for m in assign.finditer(source)}
    bindings.update({m.group(2): m.group(1) for m in ctx.finditer(source)})
    if not bindings:
        return []
    drift: list[str] = []
    call = re.compile(rf"(?<![\w.])({'|'.join(re.escape(v) for v in bindings)})\.(\w+)\(")
    for match in call.finditer(source):
        var, method = match.group(1), match.group(2)
        cls = bindings[var]
        if method in adapters[cls]:
            continue
        if method.startswith("__"):  # __aenter__/__aexit__ on the context manager
            continue
        line = source[: match.start()].count("\n") + 1
        drift.append(f"{line}  {var}.{method}()  # {cls}")
    return drift


@pytest.fixture(scope="module")
def adapter_apis() -> dict[str, set[str]]:
    return _resolve_adapters()


@pytest.mark.imports
def test_no_phantom_getattr_lookups(layer_apis):
    drift: list[str] = []
    for path in _iter_example_py_files():
        source = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO_ROOT)
        drift.extend(f"{rel}:{hit}" for hit in find_getattr_drift(source, layer_apis))

    assert not drift, (
        "Examples probe memory-layer attributes that do not exist on either "
        "backend — the getattr() default silently hides the rename:\n  " + "\n  ".join(drift)
    )


@pytest.mark.imports
def test_no_private_client_reach_through_in_examples():
    # TODO: examples/lennys-memory/backend/tests/test_integration.py:240 still
    # uses client._client.execute_read; drop this exemption once it is fixed.
    drift: list[str] = []
    for path in _iter_example_py_files():
        if "tests" in path.parts:
            continue
        source = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO_ROOT)
        drift.extend(f"{rel}:{hit}" for hit in find_private_reach_through(source))

    assert not drift, (
        "Examples reach into MemoryClient internals. Use the public accessors: "
        "client.graph (bolt driver), client.query (portable reads), "
        "client.settings-derived values passed in explicitly:\n  " + "\n  ".join(drift)
    )


@pytest.mark.imports
def test_no_phantom_adapter_methods_in_examples(adapter_apis):
    drift: list[str] = []
    for path in _iter_example_py_files():
        source = strip_comments_and_strings(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO_ROOT)
        drift.extend(f"{rel}:{hit}" for hit in find_adapter_drift(source, adapter_apis))

    assert not drift, (
        "Examples call methods that do not exist on the library adapter the "
        "local was constructed from:\n  " + "\n  ".join(drift)
    )


# --- Tests for the widened guards -----------------------------------------


@pytest.mark.imports
def test_client_method_guard_catches_a_fake_method(adapter_apis):
    """Top-level MemoryClient methods are checked once the local is bound."""
    if "MemoryClient" not in adapter_apis:
        pytest.skip("MemoryClient not importable")
    source = (
        "client = MemoryClient(settings)\n"
        "ctx = await client.get_context('x')\n"
        "bad = await client.get_everything()\n"
    )

    assert find_adapter_drift(source, adapter_apis) == [
        "3  client.get_everything()  # MemoryClient"
    ]


@pytest.mark.imports
def test_client_method_guard_sees_async_with_bindings(adapter_apis):
    if "MemoryClient" not in adapter_apis:
        pytest.skip("MemoryClient not importable")
    source = "async with MemoryClient(settings) as client:\n    await client.get_everything()\n"

    assert find_adapter_drift(source, adapter_apis) == [
        "2  client.get_everything()  # MemoryClient"
    ]


@pytest.mark.imports
def test_client_method_guard_ignores_unrelated_clients(adapter_apis):
    """boto3/httpx/docker/fastmcp locals are not a MemoryClient."""
    source = "client = boto3.client('s3')\nclient.put_object(Bucket='b')\nhttp.get('/')\n"

    assert find_adapter_drift(source, adapter_apis) == []


@pytest.mark.imports
def test_getattr_guard_catches_a_fake_attribute(layer_apis):
    source = 'fn = getattr(client.short_term, "make_conversation", None)\n'

    assert find_getattr_drift(source, layer_apis) == [
        '1  getattr(.short_term, "make_conversation")'
    ]


@pytest.mark.imports
def test_getattr_guard_accepts_a_real_attribute(layer_apis):
    source = 'fn = getattr(client.short_term, "create_conversation", None)\n'

    assert find_getattr_drift(source, layer_apis) == []


@pytest.mark.imports
def test_private_guard_catches_reach_through():
    source = "rows = await client._client.execute_read(q)\n"

    assert find_private_reach_through(source) == ["1  client._client"]


@pytest.mark.imports
def test_private_guard_ignores_prose():
    """A docstring explaining the old private call must not trip the guard."""
    source = '"""We used to call client._client.execute_read here."""\nx = 1\n'

    assert find_private_reach_through(strip_comments_and_strings(source)) == []


@pytest.mark.imports
def test_adapter_guard_catches_a_fake_method(adapter_apis):
    if "MemoryIntegration" not in adapter_apis:
        pytest.skip("MemoryIntegration not importable")
    source = "memory = MemoryIntegration(neo4j_uri='x')\nawait memory.store_nothing('hi')\n"

    drift = find_adapter_drift(source, adapter_apis)

    assert drift == ["2  memory.store_nothing()  # MemoryIntegration"]


@pytest.mark.imports
def test_adapter_guard_accepts_a_real_method(adapter_apis):
    if "MemoryIntegration" not in adapter_apis:
        pytest.skip("MemoryIntegration not importable")
    source = "memory = MemoryIntegration(neo4j_uri='x')\nawait memory.store_message('user', 'hi')\n"

    assert find_adapter_drift(source, adapter_apis) == []


@pytest.mark.imports
def test_comment_stripper_preserves_line_numbers():
    source = 'a = 1  # client._secret\nb = """\nmulti\n"""\nc = 3\n'

    stripped = strip_comments_and_strings(source)

    assert stripped.count("\n") == source.count("\n")
    assert "_secret" not in stripped
    assert "multi" not in stripped
