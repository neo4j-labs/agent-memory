"""Smoke tests for the NAMS quickstart example.

The example talks to the hosted service over HTTP, so the whole script body is
executed offline against a ``respx``-mocked NAMS: no API key, no network, no
Neo4j. That matters here more than anywhere else in ``examples/`` — this is the
first code a new user runs, nothing type-checked it before (``mypy``/``ty``
cover ``examples/*.py``, which excludes every subdirectory), and the calls it
teaches (``wait_for_extraction``, ``get_extraction_status``, ``expand_graph``)
resolve only at runtime.

Route payloads mirror ``tests/unit/nams/*`` fixtures, which were verified
against the live NAMS OpenAPI spec.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("respx", reason="respx not installed")

import httpx  # noqa: E402
import respx  # noqa: E402

from tests.examples._manifests import assert_library_pin  # noqa: E402

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_DIR = EXAMPLES_DIR / "nams-quickstart"
MAIN_PY = EXAMPLE_DIR / "main.py"

ENDPOINT = "https://memory.test/v1"
CONVERSATION_ID = "00000000-0000-0000-0000-0000000000aa"
ENTITY_ID = "00000000-0000-0000-0000-0000000000e1"
STEP_ID = "00000000-0000-0000-0000-00000000aaaa"

# NAMS speaks camelCase; these payloads mirror tests/unit/nams fixtures.
CONVERSATION = {
    "id": CONVERSATION_ID,
    "userId": "demo",
    "createdAt": "2026-09-10T12:00:00Z",
    "updatedAt": "2026-09-10T12:00:00Z",
}
MESSAGES = [
    {
        "id": f"00000000-0000-0000-0000-00000000000{index}",
        "conversationId": CONVERSATION_ID,
        "role": role,
        "content": content,
        "createdAt": f"2026-09-10T12:00:0{index}Z",
    }
    for index, (role, content) in enumerate(
        [
            ("user", "Hi, I'm Alice."),
            ("assistant", "Nice to meet you, Alice!"),
            ("user", "I love Italian food and dislike crowded restaurants."),
        ],
        start=1,
    )
]
ENTITY = {
    "id": ENTITY_ID,
    "name": "Alice",
    "type": "person",
    "description": "The user introducing themselves.",
    "createdAt": "2026-09-10T12:00:04Z",
    "updatedAt": "2026-09-10T12:00:04Z",
}
STEP = {
    "id": STEP_ID,
    "conversationId": CONVERSATION_ID,
    "reasoning": "Alice likes Italian and dislikes crowds.",
    "actionTaken": "Look up quiet Italian places.",
    "result": "Found 3 candidates.",
    "createdAt": "2026-09-10T12:00:05Z",
}
TOOL_CALL = {
    "id": "00000000-0000-0000-0000-00000000bbbb",
    "stepId": STEP_ID,
    "toolName": "restaurant_search",
    "status": "success",
    "input": '{"cuisine": "Italian"}',
    "output": '["Da Mario"]',
    "durationMs": 42,
    "createdAt": "2026-09-10T12:00:06Z",
}
ONTOLOGY_DOC = {
    "domain": {"id": "general", "name": "General", "tagline": "Default", "emoji": "🧠"},
    "entity_types": [{"label": "Person", "pole_type": "PERSON", "properties": []}],
    "relationships": [{"type": "KNOWS", "source": "Person", "target": "Person"}],
}
ONTOLOGY_SUMMARY = {
    "id": "ont_1",
    "name": "general",
    "display_name": "General",
    "is_system": True,
    "current_revision": 1,
    "is_active": True,
}
ONTOLOGY_VERSION = {
    "id": "ov_1",
    "ontology_id": "ont_1",
    "revision": 1,
    "validation_mode": "permissive",
    "ontology": ONTOLOGY_DOC,
}


def _load_main_module():
    """Import ``examples/nams-quickstart/main.py`` (a dashed, non-package dir)."""
    spec = importlib.util.spec_from_file_location("nams_quickstart_main", MAIN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mock_nams(router: respx.Router) -> None:
    """Register every route the script drives, in call order."""
    # connect() probe -> list_conversations(limit=1)
    router.get(f"{ENDPOINT}/conversations").respond(200, json={"conversations": []})
    router.post(f"{ENDPOINT}/conversations").respond(201, json=CONVERSATION)
    # 1. bulk_add_messages
    router.post(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/messages/bulk").respond(
        201, json={"messages": MESSAGES}
    )
    # 2. add_entity, extraction status, wait_for_extraction + search_entities
    router.post(f"{ENDPOINT}/entities").respond(201, json=ENTITY)
    # The first read reports work in flight, every later one reports completion:
    # `wait_for_extraction` has to actually poll to get past this.
    polls: list[int] = []

    def _extraction_status(request: httpx.Request) -> httpx.Response:
        polls.append(1)
        summary = {"pending": 3} if len(polls) == 1 else {"completed": 3}
        return httpx.Response(200, json={"summary": summary, "messages": []})

    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/extraction-status").mock(
        side_effect=_extraction_status
    )
    router.post(f"{ENDPOINT}/entities/search").respond(
        200, json={"entities": [ENTITY], "searchType": "vector"}
    )
    # 3. three-tier context
    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/context").respond(
        200,
        json={
            "reflections": [],
            "observations": [{"content": "Likes Italian"}],
            "recentMessages": MESSAGES,
        },
    )
    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/observations").respond(
        200, json={"observations": [{"content": "Likes Italian"}]}
    )
    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/reflections").respond(
        200, json={"reflections": []}
    )
    # 4. graph expansion
    router.post(f"{ENDPOINT}/graph/expand").respond(
        200,
        json={
            "nodes": [{"id": ENTITY_ID, "name": "Alice"}, {"id": "e2", "name": "Italian food"}],
            "edges": [{"from": ENTITY_ID, "to": "e2", "type": "LIKES"}],
        },
    )
    # 5. reasoning writes + server read-back
    router.post(f"{ENDPOINT}/reasoning/steps").respond(201, json=STEP)
    router.post(f"{ENDPOINT}/reasoning/tool-calls").respond(201, json=TOOL_CALL)
    router.get(f"{ENDPOINT}/reasoning/trace/{CONVERSATION_ID}").respond(
        200,
        json={"conversationId": CONVERSATION_ID, "steps": [STEP], "toolCalls": [TOOL_CALL]},
    )
    # 6. active ontology (get_active composes version metadata from list + get)
    router.get(f"{ENDPOINT}/ontologies/active").respond(200, json={"ontology": ONTOLOGY_DOC})
    router.get(f"{ENDPOINT}/ontologies").respond(200, json={"ontologies": [ONTOLOGY_SUMMARY]})
    router.get(f"{ENDPOINT}/ontologies/ont_1").respond(
        200, json={"record": {"id": "ont_1", "name": "general"}, "versions": [ONTOLOGY_VERSION]}
    )
    # 7. portable read-only Cypher
    router.post(f"{ENDPOINT}/query").respond(
        200, json={"columns": ["name"], "rows": [{"name": "Alice"}], "stats": {}}
    )


@pytest.mark.syntax
class TestNamsQuickstartStructure:
    def test_required_files_exist(self):
        for filename in ("main.py", "README.md", "requirements.txt", ".env.example"):
            assert (EXAMPLE_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse(MAIN_PY.read_text(encoding="utf-8"))

    def test_requirements_pin_the_library_with_the_nams_extra(self):
        pin = assert_library_pin(EXAMPLE_DIR / "requirements.txt")
        assert "nams" in pin.extras, "the hosted transport comes from the [nams] extra"

    def test_main_teaches_async_server_side_extraction(self):
        """The defining NAMS behaviour: a read after a write can be empty."""
        source = MAIN_PY.read_text(encoding="utf-8")
        assert "wait_for_extraction(" in source
        assert "get_extraction_status(" in source
        assert "search_entities(" in source

    def test_main_uses_the_nams_only_surface(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        for call in (
            "bulk_add_messages(",
            "get_context(",
            "get_observations(",
            "get_reflections(",
            "expand_graph(",
            "ontology.get_active(",
            "query.cypher(",
        ):
            assert call in source, f"missing the {call!r} demonstration"

    def test_main_drops_the_capability_hedges(self):
        """nams-quickstart-F05/F10/F11: no getattr probe, no bare except, no privates."""
        source = MAIN_PY.read_text(encoding="utf-8")
        assert 'getattr(client.short_term, "create_conversation"' not in source
        assert "create_conversation(" in source
        assert "except Exception" not in source
        assert "client._settings" not in source
        assert "isinstance(entity_result, tuple)" not in source

    def test_env_example_documents_the_workspace_header(self):
        content = (EXAMPLE_DIR / ".env.example").read_text(encoding="utf-8")
        assert "MEMORY_API_KEY" in content
        assert "MEMORY_WORKSPACE_ID" in content, (
            "header-scoped deployments need it; without it they answer 403"
        )
        assert "MEMORY_ENDPOINT" in content

    def test_readme_follows_labs_conventions(self):
        readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j-Labs" in readme, "missing the Labs badge"
        assert "Neo4j Labs Project" in readme, "missing the Labs disclaimer"
        assert "## Support" in readme
        assert "Verified against" in readme
        assert "Expected output" in readme
        # The old README linked to literal `docs/.../*.adoc` placeholder paths.
        assert "docs/.../" not in readme
        assert "https://neo4j.com/labs/agent-memory/how-to/use-nams" in readme


@pytest.mark.imports
class TestNamsQuickstartImports:
    def test_the_surface_the_example_imports_exists(self):
        from neo4j_agent_memory import NamsSettings, connect  # noqa: F401
        from neo4j_agent_memory.core.exceptions import (  # noqa: F401
            AuthenticationError,
            NotSupportedError,
            RateLimitError,
            TransportError,
        )

    def test_module_imports_with_a_fake_key(self, monkeypatch):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test_key")
        try:
            module = _load_main_module()
            assert module.CONVERSATION_NAME
            assert len(module.TRANSCRIPT) == 3
        finally:
            sys.modules.pop("nams_quickstart_main", None)


class TestNamsQuickstartRun:
    """Execute ``main()`` end to end against a mocked NAMS."""

    async def test_main_runs_against_mocked_nams(self, monkeypatch, capsys):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test_key")
        monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)
        monkeypatch.delenv("MEMORY_WORKSPACE_ID", raising=False)

        try:
            module = _load_main_module()
            # A developer's own examples/nams-quickstart/.env must not leak in.
            monkeypatch.setattr(module, "load_env", lambda: None)
            with respx.mock(assert_all_called=False) as router:
                _mock_nams(router)
                await module.main()
        finally:
            sys.modules.pop("nams_quickstart_main", None)

        out = capsys.readouterr().out
        assert f"Connected to {ENDPOINT} (backend=nams)" in out
        assert CONVERSATION_ID in out
        assert "Stored 3 messages in one request" in out
        assert "Wrote entity: Alice (PERSON)" in out
        assert "3 message(s) pending" in out
        assert "Extraction settled: True" in out
        assert "1 observation(s)" in out
        assert "2 node(s), 1 edge(s)" in out
        assert "Server-side reasoning steps for this conversation: 1" in out
        assert "Active ontology: General (revision 1, permissive)" in out
        assert "Cypher round-trip: [{'name': 'Alice'}]" in out

    async def test_main_exits_without_an_api_key(self, monkeypatch):
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)

        try:
            module = _load_main_module()
            monkeypatch.setattr(module, "load_env", lambda: None)
            with pytest.raises(SystemExit, match="MEMORY_API_KEY"):
                await module.main()
        finally:
            sys.modules.pop("nams_quickstart_main", None)

    async def test_missing_ontology_does_not_abort_the_script(self, monkeypatch, capsys):
        """A workspace with no bound ontology must still finish the walkthrough."""
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test_key")
        monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)

        try:
            module = _load_main_module()
            monkeypatch.setattr(module, "load_env", lambda: None)
            with respx.mock(assert_all_called=False) as router:
                _mock_nams(router)
                # Re-register: respx matches the most recently added route first.
                # A body whose document does not parse is what the library maps
                # to NotSupportedError("No active ontology bound ...").
                router.get(f"{ENDPOINT}/ontologies/active").respond(200, json={"ontology": "null"})
                await module.main()
        finally:
            sys.modules.pop("nams_quickstart_main", None)

        out = capsys.readouterr().out
        assert "No active ontology bound for this workspace" in out
        assert "Cypher round-trip" in out
