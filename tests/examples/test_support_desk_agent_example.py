"""Smoke tests for the support-desk-agent example (backend and seed data).

The fast cases need no Neo4j and no model weights: the backend's files and
pin, parity with ``examples/ontology-lifecycle-bolt`` (same Arrows document,
same first transcript, same ``REPAIRS``), the seed data's deliberate overlaps,
and the agent built offline on PydanticAI's ``TestModel``.

``test_backend_runs_end_to_end`` seeds a real database with real GLiNER2.5
inference, then drives the FastAPI app through ``httpx.ASGITransport`` with
``AGENT_MODEL=test``: a chat turn must leave a reasoning trace with tool calls
and TOUCHED edges, and the Ticket -> SupportCase rename must migrate the seeded
tickets. It asserts what the flow has to show rather than exact entity lists,
which depend on the model. The seed activates its ontology (activation is per
database), so the test removes everything it wrote and restores the binding it
found: CI runs every example test against one database.

The frontend is covered by its own type-check/lint/build in CI; the README is
the orchestrator's (``test_examples_registry.py``).
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib
import json
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import tomllib

from tests.examples._manifests import assert_library_pin

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_DIR = EXAMPLES_DIR / "support-desk-agent"
BACKEND_DIR = EXAMPLE_DIR / "backend"
SRC_DIR = BACKEND_DIR / "src"
DATA_DIR = EXAMPLE_DIR / "data"
BOLT_TWIN_DIR = EXAMPLES_DIR / "ontology-lifecycle-bolt"

BACKEND_FILES = (
    "pyproject.toml",
    "uv.lock",
    ".env.example",
    "src/__init__.py",
    "src/main.py",
    "src/config.py",
    "src/memory.py",
    "src/ontology.py",
    "src/seed.py",
    "src/agent/__init__.py",
    "src/agent/agent.py",
    "src/agent/deps.py",
    "src/agent/tools.py",
    "src/api/__init__.py",
    "src/api/schemas.py",
    "src/api/routes/__init__.py",
    "src/api/routes/chat.py",
    "src/api/routes/threads.py",
    "src/api/routes/memory.py",
    "src/api/routes/graph.py",
    "src/api/routes/ontology.py",
    "src/api/routes/traces.py",
)

TOOL_NAMES = {
    "recall_similar_tasks",
    "find_customer",
    "get_ticket",
    "list_tickets",
    "get_order",
    "search_support_history",
    "get_ontology",
}


def _literal(path: Path, name: str) -> Any:
    """Evaluate one module-level literal assignment without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id == name and node.value is not None:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path} defines no module-level {name}")


def _conversations() -> list[dict[str, Any]]:
    data = json.loads((DATA_DIR / "conversations.json").read_text(encoding="utf-8"))
    return data["conversations"]


@contextlib.contextmanager
def _backend_on_path() -> Iterator[None]:
    """Import this backend's ``src`` package without leaking it.

    Every full-stack backend names its package ``src`` and the repository root
    has a ``src/`` directory too, so the import is bracketed: drop cached
    ``src*`` modules, put this backend first on ``sys.path``, restore after.
    """
    saved = {n: m for n, m in sys.modules.items() if n == "src" or n.startswith("src.")}
    for name in saved:
        del sys.modules[name]
    sys.path.insert(0, str(BACKEND_DIR))
    importlib.invalidate_caches()
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
            del sys.modules[name]
        with contextlib.suppress(ValueError):
            sys.path.remove(str(BACKEND_DIR))
        sys.modules.update(saved)
        importlib.invalidate_caches()


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


class TestBackendStructure:
    @pytest.mark.syntax
    def test_the_backend_and_data_files_exist(self):
        for name in BACKEND_FILES:
            assert (BACKEND_DIR / name).exists(), f"support-desk-agent/backend/{name} is missing"
        for name in ("support-desk.arrows.json", "conversations.json"):
            assert (DATA_DIR / name).exists(), f"support-desk-agent/data/{name} is missing"

    @pytest.mark.syntax
    def test_every_backend_module_is_valid_python(self):
        for path in SRC_DIR.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    @pytest.mark.syntax
    def test_pyproject_pins_the_library_with_the_extras_it_needs(self):
        pin = assert_library_pin(BACKEND_DIR / "pyproject.toml", minimum="0.7.0")
        assert set(pin.extras) == {"gliner2", "sentence-transformers"}

    @pytest.mark.syntax
    def test_pyproject_resolves_the_library_from_the_checkout(self):
        data = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))
        source = data["tool"]["uv"]["sources"]["neo4j-agent-memory"]
        assert source == {"path": "../../..", "editable": True}
        dependencies = " ".join(data["project"]["dependencies"])
        for requirement in (
            "fastapi>=0.115,<1",
            "pydantic-ai-slim[openai]>=2.0,<3",
            "sse-starlette>=3.4,<4",
            "pydantic-settings>=2.6,<3",
        ):
            assert requirement in dependencies, requirement

    @pytest.mark.syntax
    def test_the_lockfile_resolves_the_editable_library(self):
        lock = (BACKEND_DIR / "uv.lock").read_text(encoding="utf-8")
        assert 'name = "neo4j-agent-memory"' in lock
        assert 'source = { editable = "../../../" }' in lock

    @pytest.mark.syntax
    def test_env_example_documents_every_setting(self):
        content = (BACKEND_DIR / ".env.example").read_text(encoding="utf-8")
        for name in (
            "NEO4J_URI=",
            "NEO4J_USERNAME=",
            "NEO4J_PASSWORD=",
            "NEO4J_DATABASE=",
            "AGENT_MODEL=",
            "OPENAI_API_KEY=",
            "LOCAL_EMBEDDING_MODEL=",
            "CORS_ORIGINS=",
        ):
            assert name in content, name
        assert "AGENT_MODEL=test" in content and "NOT a real agent" in content

    @pytest.mark.syntax
    def test_settings_have_no_credential_defaults(self):
        source = (SRC_DIR / "config.py").read_text(encoding="utf-8")
        for field in ("neo4j_uri: str\n", "neo4j_username: str\n", "neo4j_password: SecretStr\n"):
            assert field in source, f"{field.strip()} must be required (no default)"
        assert "test-password" not in source

    @pytest.mark.syntax
    def test_the_client_takes_its_ontology_from_the_database(self):
        """An ontology file or template would outrank the active stored version."""
        source = (SRC_DIR / "memory.py").read_text(encoding="utf-8")
        assert "use_active_ontology=True" in source
        assert "llm=None" in source
        assert "enable_llm_fallback=False" in source
        for override in ("ontology_path", "custom_schema_path", "ontology_template"):
            assert override not in source, f"{override} would bypass the activated version"

    @pytest.mark.syntax
    def test_graph_reads_use_the_read_only_accessor(self):
        """Reads go through client.query.cypher(); nothing opens its own driver."""
        for path in SRC_DIR.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "GraphDatabase" not in source, path
            assert "graph.execute_read" not in source, path


# ---------------------------------------------------------------------------
# Parity with ontology-lifecycle-bolt
# ---------------------------------------------------------------------------


class TestParityWithTheLifecycleExample:
    @pytest.mark.syntax
    def test_the_arrows_document_is_identical(self):
        twin = BOLT_TWIN_DIR / "schemas" / "support-desk.arrows.json"
        assert (DATA_DIR / "support-desk.arrows.json").read_bytes() == twin.read_bytes()

    @pytest.mark.syntax
    def test_the_first_seed_conversation_is_the_lifecycle_transcript(self):
        transcript = _literal(BOLT_TWIN_DIR / "main.py", "TRANSCRIPT")
        assert _conversations()[0]["messages"] == transcript

    @pytest.mark.syntax
    def test_the_repairs_are_identical(self):
        ours = SRC_DIR / "ontology.py"
        twin = BOLT_TWIN_DIR / "main.py"
        assert _literal(ours, "REPAIRS") == _literal(twin, "REPAIRS")
        for name in ("DOMAIN_ID", "DOMAIN_NAME"):
            assert _literal(ours, name) == _literal(twin, name)


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------


class TestSeedData:
    @pytest.mark.syntax
    def test_conversations_are_seed_sessions_with_titles(self):
        conversations = _conversations()
        assert 6 <= len(conversations) <= 10
        ids = [c["session_id"] for c in conversations]
        assert len(set(ids)) == len(ids)
        for conversation in conversations:
            assert conversation["session_id"].startswith("seed-")
            assert conversation["title"].strip()
            roles = [m["role"] for m in conversation["messages"]]
            assert roles[0] == "user"
            assert set(roles) == {"user", "assistant"}

    @pytest.mark.syntax
    def test_the_overlaps_resolution_has_to_work_through(self):
        def sessions_mentioning(text: str) -> int:
            return sum(any(text in m["content"] for m in c["messages"]) for c in _conversations())

        everything = " ".join(m["content"] for c in _conversations() for m in c["messages"])
        assert sessions_mentioning("Marcus Bell") >= 2, "the same customer across sessions"
        assert sessions_mentioning("TK-2210") >= 2, "a ticket referenced from a later session"
        assert "Halcyon Ergonomic Chair" in everything and "Halcyon Chair " in everything
        assert "Sorry about that, Priya." in everything, "a first-name-only mention"

    @pytest.mark.syntax
    def test_seeded_traces_use_real_tools_and_real_user_messages(self):
        traces = _literal(SRC_DIR / "seed.py", "SEEDED_TRACES")
        assert 2 <= len(traces) <= 3
        by_session = {c["session_id"]: c["messages"] for c in _conversations()}
        for trace in traces:
            session_id, index = trace["message"]
            assert by_session[session_id][index]["role"] == "user", trace["task"]
            assert trace["steps"], trace["task"]
            for _thought, tool_name, _arguments in trace["steps"]:
                assert tool_name in TOOL_NAMES, tool_name
            assert trace["summary"]


# ---------------------------------------------------------------------------
# Offline: imports, the agent on TestModel, the ontology helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def backend(monkeypatch) -> Iterator[SimpleNamespace]:
    """The backend's modules, with settings that need no database to construct."""
    pytest.importorskip("pydantic_ai")
    pytest.importorskip("fastapi")
    pytest.importorskip("sse_starlette")
    for key, value in {
        "NEO4J_URI": "bolt://localhost:1",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "unused",
        "AGENT_MODEL": "test",
        "OPENAI_API_KEY": "",
        "CORS_ORIGINS": "http://localhost:3000",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MEMORY_API_KEY", raising=False)
    with _backend_on_path():
        modules = SimpleNamespace(
            config=importlib.import_module("src.config"),
            memory=importlib.import_module("src.memory"),
            ontology=importlib.import_module("src.ontology"),
            agent=importlib.import_module("src.agent.agent"),
            tools=importlib.import_module("src.agent.tools"),
            seed=importlib.import_module("src.seed"),
        )
        modules.config.get_settings.cache_clear()
        modules.agent.get_agent.cache_clear()
        try:
            yield modules
        finally:
            modules.config.get_settings.cache_clear()
            modules.agent.get_agent.cache_clear()


class TestOfflineBackend:
    @pytest.mark.imports
    def test_the_agent_registers_every_tool(self, backend):
        agent = backend.agent.build_agent("test")
        registered = {name for toolset in agent.toolsets for name in getattr(toolset, "tools", {})}
        assert registered == TOOL_NAMES
        assert set(backend.agent.TOOL_NAMES) == TOOL_NAMES

    @pytest.mark.imports
    def test_agent_model_test_selects_the_keyless_test_model(self, backend):
        from pydantic_ai.models.test import TestModel

        assert backend.config.get_settings().uses_test_model
        assert isinstance(backend.agent.get_agent().model, TestModel)

    @pytest.mark.imports
    def test_the_seed_drives_the_agents_own_tools(self, backend):
        assert set(backend.seed.TOOL_FUNCTIONS) == TOOL_NAMES

    @pytest.mark.imports
    def test_memory_settings_are_keyless_bolt_with_gliner(self, backend):
        settings = backend.memory.build_memory_settings(backend.config.get_settings())
        assert settings.llm is None
        assert settings.schema_config.use_active_ontology is True
        assert settings.extraction.enable_gliner is True
        assert settings.extraction.enable_llm_fallback is False

    @pytest.mark.imports
    def test_ticket_label_follows_the_rename(self, backend):
        from neo4j_agent_memory.ontology.store import BoltOntology

        # import_ converts locally and never touches the client.
        store = BoltOntology.__new__(BoltOntology)
        draft = asyncio.run(
            BoltOntology.import_(
                store,
                content=(DATA_DIR / "support-desk.arrows.json").read_text(encoding="utf-8"),
                format="arrows",
            )
        )
        document = backend.ontology.repair_draft(draft.document)
        assert document.validate_structure() == []
        assert backend.ontology.ticket_label(document) == "Ticket"
        revised = backend.ontology.rename_entity_type(document, "Ticket", "SupportCase")
        assert revised.validate_structure() == []
        assert backend.ontology.ticket_label(revised) == "SupportCase"
        assert backend.ontology.entity_labels(["Entity", "Event", "Ticket"]) == ["Ticket", "Event"]

    @pytest.mark.imports
    def test_untitled_threads_are_titled_after_their_first_message(self, backend):
        pytest.importorskip("fastapi")
        threads = importlib.import_module("src.api.routes.threads")
        assert threads.title_from("  Where is order SO-4471?\nThanks ") == (
            "Where is order SO-4471? Thanks"
        )
        question = (
            "Which open tickets does Grace Liu have, and is the duplicate charge "
            "on SO-4503 refunded yet?"
        )
        title = threads.title_from(question)
        assert len(title) <= threads.TITLE_LENGTH and title.endswith("…")
        assert question.startswith(title[:-1])
        assert threads.title_from("   ") == threads.DEFAULT_TITLE


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

#: Chat threads the test creates, with their traces and the entities only they mention.
CHAT_CLEANUP = """
MATCH (c:Conversation) WHERE c.session_id IN $sessions
OPTIONAL MATCH (rt:ReasoningTrace) WHERE rt.session_id = c.session_id
OPTIONAL MATCH (rt)-[:HAS_STEP]->(s:ReasoningStep)
OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
OPTIONAL MATCH (m)-[:MENTIONS]->(e:Entity)
WHERE NOT EXISTS {
  MATCH (e)<-[:MENTIONS]-(:Message)<-[:HAS_MESSAGE]-(other:Conversation)
  WHERE NOT other.session_id IN $sessions
}
WITH collect(DISTINCT c) + collect(DISTINCT rt) + collect(DISTINCT s) + collect(DISTINCT tc)
     + collect(DISTINCT m) + collect(DISTINCT e) AS owned
FOREACH (node IN owned | DETACH DELETE node)
"""


def _sse_events(body: str) -> list[dict[str, Any]]:
    """Parse an SSE body into the JSON payloads it carried."""
    return [
        json.loads(line[len("data:") :].strip())
        for line in body.splitlines()
        if line.startswith("data:") and line[len("data:") :].strip()
    ]


@contextlib.asynccontextmanager
async def _http(app: Any) -> AsyncIterator[Any]:
    """An httpx client bound to ``app``, with the lifespan actually run."""
    import httpx

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.mark.requires_neo4j
def test_backend_runs_end_to_end(neo4j_env, monkeypatch):
    pytest.importorskip("gliner2")
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("httpx")
    pytest.importorskip("pydantic_ai")
    pytest.importorskip("sse_starlette")

    for key, value in {
        "NEO4J_DATABASE": "neo4j",
        "AGENT_MODEL": "test",
        "OPENAI_API_KEY": "",
        "CORS_ORIGINS": "http://localhost:3000",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MEMORY_API_KEY", raising=False)

    with _backend_on_path():
        config = importlib.import_module("src.config")
        memory = importlib.import_module("src.memory")
        seed = importlib.import_module("src.seed")
        agent = importlib.import_module("src.agent.agent")
        config.get_settings.cache_clear()
        agent.get_agent.cache_clear()
        main = importlib.import_module("src.main")
        settings = config.get_settings()

        from neo4j_agent_memory import connect
        from neo4j_agent_memory.core.exceptions import NotSupportedError

        async def active_version_id() -> str | None:
            client = await connect(memory.build_memory_settings(settings))
            try:
                return (await client.ontology.get_active()).version_id
            except NotSupportedError:
                return None
            finally:
                await client.close()

        before = asyncio.run(active_version_id())
        chat_sessions: list[str] = []
        try:
            summary = asyncio.run(seed.seed(settings, reset=True))

            # The seed: revision 1, the transcript, tickets under :Ticket,
            # resolution's review queue, and earlier agent work.
            assert summary["revision"] == 1
            assert summary["validation_mode"] == "permissive"
            assert "seed-priya-damaged-lamp" in summary["sessions"]
            tickets = summary["entities_by_label"].get("Ticket", [])
            assert any(name.startswith("TK-") for name in tickets), summary["entities_by_label"]
            assert summary["entities_by_label"].get("Customer"), summary["entities_by_label"]
            assert summary["pending_review_pairs"], "a first name next to a full name waits"
            assert len(summary["traces"]) == len(seed.SEEDED_TRACES)
            assert all(trace["touched"] > 0 for trace in summary["traces"]), summary["traces"]

            async def drive() -> dict[str, Any]:
                app = main.create_app()
                async with _http(app) as http:
                    health = (await http.get("/api/health")).json()
                    assert health["neo4j"] is True, health
                    assert health["agent_model"] == "test"
                    assert health["ontology"] == {
                        "domain_id": "support-desk",
                        "revision": 1,
                        "validation_mode": "permissive",
                    }

                    threads = (await http.get("/api/threads")).json()
                    transcript = next(t for t in threads if t["id"] == "seed-priya-damaged-lamp")
                    assert transcript["seeded"] is True and transcript["message_count"] == 6

                    created = await http.post("/api/threads", json={})
                    assert created.status_code == 200, created.text
                    assert created.json()["title"] == "New chat"
                    thread_id = created.json()["id"]
                    chat_sessions.append(thread_id)
                    assert thread_id.startswith("chat-")

                    # One chat turn: stored + extracted, traced, tools streamed.
                    response = await http.post(
                        "/api/chat",
                        json={
                            "thread_id": thread_id,
                            "message": "Which tickets does Priya Raman have?",
                        },
                    )
                    assert response.status_code == 200, response.text
                    events = _sse_events(response.text)
                    kinds = [event["type"] for event in events]
                    assert "error" not in kinds, events
                    assert kinds[:2] == ["message_stored", "trace_started"], kinds
                    assert kinds[-1] == "done", kinds
                    stored = events[0]
                    assert any(e["name"] == "Priya Raman" for e in stored["entities"]), stored
                    calls = [e for e in events if e["type"] == "tool_call"]
                    results = [e for e in events if e["type"] == "tool_result"]
                    assert {e["name"] for e in calls} == TOOL_NAMES
                    assert len(results) == len(calls)
                    assert any(e["touched"] for e in results), "list_tickets names tickets"
                    # The chat labels touched entities like the panels do.
                    streamed = [t for e in results for t in e["touched"]]
                    assert all(t["labels"] for t in streamed), streamed
                    done = events[-1]
                    assert done["trace_id"] == events[1]["trace_id"]

                    traces = (await http.get(f"/api/traces?thread_id={thread_id}")).json()
                    assert len(traces) == 1, traces
                    trace = traces[0]
                    assert trace["id"] == done["trace_id"]
                    assert trace["success"] is True and trace["seeded"] is False
                    assert trace["tool_call_count"] == len(calls) == trace["step_count"]
                    assert trace["message_id"] == stored["message_id"]

                    detail = (await http.get(f"/api/traces/{trace['id']}")).json()
                    assert detail["metrics"] == {"tool_calls": float(len(calls))}
                    tool_calls = [c for step in detail["steps"] for c in step["tool_calls"]]
                    assert {c["tool_name"] for c in tool_calls} == TOOL_NAMES
                    touched = [t for c in tool_calls for t in c["touched"]]
                    assert touched and all(t["labels"] for t in touched), tool_calls

                    thread = (await http.get(f"/api/threads/{thread_id}")).json()
                    assert [m["role"] for m in thread["messages"]] == ["user", "assistant"]
                    assert thread["messages"][0]["trace_id"] == trace["id"]
                    # Untitled at creation, titled after the first message.
                    assert thread["title"] == "Which tickets does Priya Raman have?"
                    # A reopened chat rebuilds its tool cards from the trace.
                    recorded = thread["messages"][0]["tool_calls"]
                    assert {c["tool_name"] for c in recorded} == TOOL_NAMES
                    assert all(t["labels"] for c in recorded for t in c["touched"])

                    # search_support_history leaves the current conversation
                    # out (the question itself would be its best hit), and each
                    # hit names the tickets its conversation mentions.
                    tools = importlib.import_module("src.agent.tools")
                    client = await app.state.memory.get_client()
                    question = "Which tickets does Priya Raman have?"
                    everywhere = await tools.search_support_history(client, question)
                    assert any(m["session_id"] == thread_id for m in everywhere["messages"])
                    elsewhere = await tools.search_support_history(
                        client, question, exclude_session_id=thread_id
                    )
                    assert elsewhere["messages"], elsewhere
                    assert all(m["session_id"] != thread_id for m in elsewhere["messages"])
                    assert any(m["conversation_tickets"] for m in elsewhere["messages"]), elsewhere

                    similar = (
                        await http.get(
                            "/api/traces/similar",
                            params={"task": "Find the open tickets for a customer"},
                        )
                    ).json()
                    assert any(t["seeded"] for t in similar), similar
                    stats = (await http.get("/api/tool-stats")).json()
                    assert {s["name"] for s in stats} >= TOOL_NAMES

                    context = (
                        await http.get(
                            "/api/memory/context", params={"thread_id": "seed-priya-damaged-lamp"}
                        )
                    ).json()
                    assert any("Ticket" in e["labels"] for e in context["entities"]), context
                    graph = (
                        await http.get(
                            "/api/graph", params={"thread_id": "seed-priya-damaged-lamp"}
                        )
                    ).json()
                    kinds_in_graph = {node["kind"] for node in graph["nodes"]}
                    assert kinds_in_graph == {"conversation", "message", "entity"}
                    assert all("from" in r and "to" in r for r in graph["relationships"])

                    # The rename: a new strict revision, the graph migrated, the
                    # app's client reconnected onto it.
                    overview = (await http.get("/api/ontology")).json()
                    revision_1 = overview["active"]["version_id"]
                    tickets_before = overview["label_counts"]["Ticket"]
                    assert tickets_before >= 1
                    renamed = await http.post(
                        "/api/ontology/rename",
                        json={"old": "Ticket", "new": "SupportCase", "validation_mode": "strict"},
                    )
                    assert renamed.status_code == 200, renamed.text
                    result = renamed.json()
                    assert result["revision"] == 2
                    assert result["migration"]["status"] == "completed"
                    assert result["migration"]["processed"] >= 1
                    assert result["migration"]["processed"] == result["dry_run_total"]
                    assert result["client"] == {
                        "domain_id": "support-desk",
                        "validation_mode": "strict",
                    }
                    added = [t["label"] for t in result["diff"]["entity_types"]["added"]]
                    assert added == ["SupportCase"]
                    assert result["diff"]["mode_change"] == {"from": "permissive", "to": "strict"}

                    after = (await http.get("/api/ontology")).json()
                    assert after["label_counts"]["Ticket"] == 0
                    assert after["label_counts"]["SupportCase"] == tickets_before
                    assert [r["is_active"] for r in after["revisions"]] == [False, True]

                    again = await http.post(
                        "/api/ontology/rename", json={"old": "Ticket", "new": "SupportCase"}
                    )
                    assert again.status_code == 409, again.text

                    back = await http.post(
                        "/api/ontology/activate", json={"version_id": revision_1}
                    )
                    assert back.status_code == 200, back.text
                    assert back.json()["revision"] == 1
                    assert back.json()["client"]["validation_mode"] == "permissive"
                    return result

            asyncio.run(drive())
        finally:

            async def restore() -> None:
                client = await connect(memory.build_memory_settings(settings))
                try:
                    await seed.reset_seed(client)
                    if chat_sessions:
                        await client.graph.execute_write(CHAT_CLEANUP, {"sessions": chat_sessions})
                        await client.reasoning.migrate_tool_stats()
                    if before is not None:
                        await client.ontology.activate(before)
                finally:
                    await client.close()

            try:
                asyncio.run(restore())
            finally:
                config.get_settings.cache_clear()
                agent.get_agent.cache_clear()
