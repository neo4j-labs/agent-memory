"""Smoke tests for the support-desk-agent example (backend, STATE-Bench data, ontology).

The fast cases need no Neo4j and no model weights: the backend's files and
pin, the STATE-Bench data and the vendored environment (provenance, and every
recorded tool call replaying to the recorded result), the ontology document,
and the agent built offline on PydanticAI's ``TestModel``.

``test_backend_runs_end_to_end`` seeds a real database with real GLiNER2.5
inference, then drives the FastAPI app through ``httpx.ASGITransport`` with
``AGENT_MODEL=test``: a chat turn must leave a reasoning trace with tool calls
and TOUCHED edges, a return run through the graph-backed environment must
change the order line in the graph, and the Warranty -> WarrantyCoverage
rename must migrate the seeded warranties. It asserts what the flow has to
show rather than exact entity lists, which depend on the model. The seed
activates its ontology (activation is per database), so the test removes
everything it wrote and restores the binding it found: CI runs every example
test against one database.

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
STATE_BENCH_DIR = DATA_DIR / "state-bench"
VENDOR_DIR = SRC_DIR / "statebench" / "vendor"

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
    "src/agent/thoughts.py",
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
    "src/statebench/__init__.py",
    "src/statebench/dataset.py",
    "src/statebench/world.py",
    "src/statebench/vendor/__init__.py",
    "src/statebench/vendor/LICENSE",
    "src/statebench/vendor/base.py",
    "src/statebench/vendor/environment.py",
    "src/statebench/vendor/policies.py",
    "src/statebench/vendor/schemas.py",
    "src/statebench/vendor/tools.py",
)

#: The STATE-Bench environment's tools, under their own names.
STATE_BENCH_TOOLS = {
    "get_order",
    "get_customer",
    "search_products",
    "get_product_details",
    "get_policies",
    "get_warranty_status",
    "process_return",
    "process_refund",
    "cancel_order",
    "process_exchange",
    "process_warranty_claim",
}
MEMORY_TOOLS = {"recall_similar_tasks", "find_customer", "search_support_history", "get_ontology"}
TOOL_NAMES = STATE_BENCH_TOOLS | MEMORY_TOOLS

#: The task families the 24 seeded tasks cover (STATE-Bench ``task_type``).
TASK_TYPES = {
    "return_item",
    "exchange_item",
    "shipping_claim",
    "warranty_claim",
    "cancel_order",
    "price_match_refund",
    "compound",
    "edge_case",
}


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _task_ids() -> list[str]:
    return sorted(path.stem for path in (STATE_BENCH_DIR / "trajectories").glob("*.json"))


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
        assert (DATA_DIR / "customer-support.ontology.yaml").exists()
        assert (STATE_BENCH_DIR / "LICENSE").exists()

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
    def test_the_vendored_environment_is_excluded_from_lint_and_strict_typing(self):
        """It is copied unchanged, so the example's own checks leave it alone."""
        data = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))
        assert "src/statebench/vendor" in data["tool"]["ruff"]["extend-exclude"]
        overrides = data["tool"]["mypy"]["overrides"]
        assert any(
            o["module"] == "src.statebench.vendor.*" and o["ignore_errors"] for o in overrides
        )

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
# STATE-Bench data and the vendored environment
# ---------------------------------------------------------------------------


class TestStateBenchProvenance:
    @pytest.mark.syntax
    def test_the_data_and_the_code_carry_the_mit_licence(self):
        data_licence = (STATE_BENCH_DIR / "LICENSE").read_text(encoding="utf-8")
        assert data_licence.startswith("MIT License")
        assert "STATE-Bench contributors" in data_licence
        assert (VENDOR_DIR / "LICENSE").read_text(encoding="utf-8") == data_licence

    @pytest.mark.syntax
    def test_vendored_files_name_their_source_commit(self):
        source = (SRC_DIR / "statebench" / "dataset.py").read_text(encoding="utf-8")
        commit = source.split('SOURCE_COMMIT = "', 1)[1].split('"', 1)[0]
        assert len(commit) == 40
        for name in ("base.py", "environment.py", "policies.py", "schemas.py", "tools.py"):
            text = (VENDOR_DIR / name).read_text(encoding="utf-8")
            assert text.startswith(f"# Copied from microsoft/STATE-Bench @ {commit[:12]}"), name
            imports = [
                line
                for line in text.splitlines()
                if line.lstrip().startswith(("from ", "import ")) and "state_bench" in line
            ]
            assert imports == [], (name, imports)
        assert commit in (VENDOR_DIR / "__init__.py").read_text(encoding="utf-8")

    @pytest.mark.syntax
    def test_every_task_has_its_trajectory_definition_and_environment(self):
        ids = _task_ids()
        assert len(ids) == 24
        for folder in ("tasks", "task_envs"):
            assert sorted(p.stem for p in (STATE_BENCH_DIR / folder).glob("*.json")) == ids


class TestStateBenchData:
    @pytest.mark.syntax
    def test_the_tasks_cover_every_family_customer_and_tool(self):
        tasks = [_json(STATE_BENCH_DIR / "tasks" / f"{i}.json") for i in _task_ids()]
        assert {task["task_type"] for task in tasks} == TASK_TYPES
        assert {task["user_id"] for task in tasks} == {f"cust_00{n}" for n in range(1, 6)}
        tools = {
            call["name"]
            for i in _task_ids()
            for message in _json(STATE_BENCH_DIR / "trajectories" / f"{i}.json")["conversation"]
            for call in message.get("tool_calls") or []
        }
        assert tools == STATE_BENCH_TOOLS

    @pytest.mark.syntax
    def test_orders_belong_to_one_task_and_customers_agree(self):
        orders: dict[str, str] = {}
        customers: dict[str, str] = {}
        for task_id in _task_ids():
            env = _json(STATE_BENCH_DIR / "task_envs" / f"{task_id}.json")
            for order in env["orders"]:
                assert orders.setdefault(order["order_id"], task_id) == task_id, order["order_id"]
            for customer in env["customers"]:
                record = json.dumps(customer, sort_keys=True)
                assert customers.setdefault(customer["customer_id"], record) == record


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
            thoughts=importlib.import_module("src.agent.thoughts"),
            dataset=importlib.import_module("src.statebench.dataset"),
            world=importlib.import_module("src.statebench.world"),
            environment=importlib.import_module("src.statebench.vendor.environment"),
            schemas=importlib.import_module("src.statebench.vendor.schemas"),
            seed=importlib.import_module("src.seed"),
        )
        modules.config.get_settings.cache_clear()
        modules.agent.get_agent.cache_clear()
        try:
            yield modules
        finally:
            modules.config.get_settings.cache_clear()
            modules.agent.get_agent.cache_clear()


class TestVendoredEnvironment:
    @pytest.mark.imports
    def test_every_recorded_tool_call_replays_to_the_recorded_result(self, backend):
        """The copied environment is the one the trajectories were recorded against."""
        replayed = 0
        for task in backend.dataset.load_tasks():
            env = backend.environment.CustomerSupportEnvironment(
                backend.schemas.CSEnvironmentData.from_dict(task.env), task.now
            )
            for message in task.conversation:
                for call in message.get("tool_calls") or []:
                    result = env.tool_handlers[call["name"]](call["arguments"])
                    assert json.loads(json.dumps(result)) == call["result"], (task.id, call)
                    replayed += 1
        assert replayed == 140

    @pytest.mark.imports
    def test_the_merged_world_keeps_each_orders_task_date(self, backend):
        tasks = backend.dataset.load_tasks()
        merged = backend.dataset.merge_world(tasks)
        assert len(merged.records["orders"]) == 25
        assert len(merged.records["customers"]) == 5
        task = next(t for t in tasks if t.id == "66-challenge_seasonal_electronics")
        order_id = task.env["orders"][0]["order_id"]
        assert merged.as_of[order_id] == task.now


# ---------------------------------------------------------------------------
# The ontology
# ---------------------------------------------------------------------------


class TestOntology:
    @pytest.mark.imports
    def test_the_document_validates_and_declares_the_records(self, backend):
        document = backend.ontology.load_document()
        assert document.domain.id == backend.ontology.DOMAIN_ID == "customer-support"
        assert document.labels() == ["Customer", "Order", "OrderLine", "Product", "Warranty"]
        assert set(document.relationship_types()) == {
            "PLACED",
            "CONTAINS",
            "OF_PRODUCT",
            "COVERS",
            "REPLACED_BY",
        }
        # Policies are records, not extraction targets (see the YAML's header).
        assert "Policy" not in document.labels()

    @pytest.mark.imports
    def test_every_record_role_has_a_label(self, backend):
        document = backend.ontology.load_document()
        for role in backend.world.RECORD_ROLES.values():
            assert document.node_label(*role), role

    @pytest.mark.imports
    def test_the_revision_renames_a_declared_type(self, backend):
        ontology = backend.ontology
        document = ontology.load_document()
        assert ontology.RENAME_FROM in document.labels()
        assert ontology.RENAME_TO not in document.labels()
        revised = ontology.rename_entity_type(document, ontology.RENAME_FROM, ontology.RENAME_TO)
        assert revised.validate_structure() == []
        assert ontology.role_label(revised, ontology.WARRANTY) == ontology.RENAME_TO
        assert ontology.entity_labels(["Entity", "Object", "OrderLine"]) == ["OrderLine", "Object"]


# ---------------------------------------------------------------------------
# Offline: the agent on TestModel, the graph adapter's pure parts
# ---------------------------------------------------------------------------


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
    def test_memory_settings_are_keyless_bolt_with_gliner(self, backend):
        settings = backend.memory.build_memory_settings(backend.config.get_settings())
        assert settings.llm is None
        assert settings.schema_config.use_active_ontology is True
        assert settings.extraction.enable_gliner is True
        assert settings.extraction.enable_llm_fallback is False

    @pytest.mark.imports
    def test_write_steps_read_as_preview_or_confirmation(self, backend):
        thought_for = backend.thoughts.thought_for
        assert thought_for("process_return", {"confirm": False}).startswith("Preview")
        assert thought_for("process_return", {"confirm": True}).startswith("Confirm")
        assert thought_for("get_order", {"order_id": "ORD-6014"}).startswith("Look up")
        assert set(backend.thoughts.TOOL_THOUGHTS) == TOOL_NAMES

    @pytest.mark.imports
    def test_calls_are_evaluated_at_their_orders_task_date(self, backend):
        world = backend.world
        graph = world.GraphWorld()
        graph.records["order_items"]["ITEM-1"] = {"item_id": "ITEM-1", "order_id": "ORD-1"}
        graph.as_of = {"ORD-1": "2026-01-10T10:00:00", "ORD-2": "2026-07-20T10:00:00"}
        assert world.now_for(graph, {"order_id": "ORD-1"}) == "2026-01-10T10:00:00"
        assert world.now_for(graph, {"item_id": "ITEM-1"}) == "2026-01-10T10:00:00"
        assert world.now_for(graph, {"topic": "return"}) == "2026-07-20T10:00:00"

    @pytest.mark.imports
    def test_touched_maps_record_ids_and_policy_topics_to_entities(self, backend):
        world = backend.world
        graph = world.GraphWorld()
        graph.entity_of = {"ORD-1": "e-order", "PROD-1": "e-product", "policy:return": "e-policy"}
        graph.entities = {
            "e-order": ("ORD-1", "EVENT"),
            "e-product": ("Phone Case", "OBJECT"),
            "e-policy": ("Return policy", "OBJECT"),
        }
        result = {"items": [{"product_id": "PROD-1"}]}
        order = world.touched(graph, "get_order", {"order_id": "ORD-1"}, result)
        assert [t["name"] for t in order] == ["ORD-1", "Phone Case"]
        policy = world.touched(graph, "get_policies", {"topic": "return"}, {"topic": "return"})
        assert [t["name"] for t in policy] == ["Return policy"]

    @pytest.mark.imports
    def test_untitled_threads_are_titled_after_their_first_message(self, backend):
        threads = importlib.import_module("src.api.routes.threads")
        assert threads.title_from("  Where is order ORD-6014?\nThanks ") == (
            "Where is order ORD-6014? Thanks"
        )
        question = (
            "I want to return everything from order ORD-6014: the shirt, the book and "
            "the phone case."
        )
        title = threads.title_from(question)
        assert len(title) <= threads.TITLE_LENGTH and title.endswith("…")
        assert question.startswith(title[:-1])
        assert threads.title_from("   ") == threads.DEFAULT_TITLE

    @pytest.mark.imports
    def test_seed_sessions_are_titled_and_unique(self, backend):
        tasks = backend.dataset.load_tasks()
        sessions = [task.session_id for task in tasks]
        assert len(set(sessions)) == len(sessions) == 24
        assert all(session.startswith(backend.seed.SEED_PREFIX) for session in sessions)
        topics = {task.id: task.topic for task in tasks}
        assert topics["10-return_full_order"] == "Return full order"
        assert topics["111-hard_exchange_downgrade_cash_demand"] == "Exchange downgrade cash demand"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


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
        world = importlib.import_module("src.statebench.world")
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
        try:
            summary = asyncio.run(seed.seed(settings, reset=True))

            # The seed: revision 1, the records, the conversations and every
            # trajectory's tool calls as earlier agent work.
            assert summary["revision"] == 1
            assert summary["validation_mode"] == "permissive"
            assert len(summary["sessions"]) == 24
            assert summary["records"] == {
                "customers": 5,
                "products": 38,
                "orders": 25,
                "order_items": 32,
                "warranties": 3,
            }
            assert sum(t["tool_calls"] for t in summary["traces"]) == 140
            assert all(t["touched"] > 0 for t in summary["traces"]), summary["traces"]
            labels = summary["entities_by_label"]
            assert len(labels["Policy"]) == 6
            assert "Priya Patel" in labels["Customer"]
            # Id-shaped mentions land on their records, never as stray entities.
            assert not [n for n in labels.get("Product", []) if n.startswith(("ORD-", "ITEM-"))]

            async def drive() -> None:
                app = main.create_app()
                async with _http(app) as http:
                    health = (await http.get("/api/health")).json()
                    assert health["neo4j"] is True, health
                    assert health["agent_model"] == "test"
                    assert health["ontology"] == {
                        "domain_id": "customer-support",
                        "revision": 1,
                        "validation_mode": "permissive",
                    }

                    thread_id = "seed-10-return-full-order"
                    threads = (await http.get("/api/threads")).json()
                    full_return = next(t for t in threads if t["id"] == thread_id)
                    assert full_return["seeded"] is True
                    assert full_return["title"] == "Priya Patel: Return full order"

                    context = (
                        await http.get("/api/memory/context", params={"thread_id": thread_id})
                    ).json()
                    names = {e["name"] for e in context["entities"]}
                    assert {"Priya Patel", "ORD-6014"} <= names, names

                    graph = (await http.get("/api/graph", params={"thread_id": thread_id})).json()
                    captions = {node["caption"] for node in graph["nodes"]}
                    # The record context: the order's lines, one typed edge away.
                    assert {"ITEM-9090", "ITEM-9091", "ITEM-9092"} <= captions, captions
                    relations = {r["caption"] for r in graph["relationships"]}
                    assert {"PLACED", "CONTAINS"} <= relations, relations

                    created = await http.post("/api/threads", json={})
                    assert created.status_code == 200, created.text
                    chat_id = created.json()["id"]
                    assert chat_id.startswith("chat-")

                    # One chat turn: stored + extracted, traced, tools streamed.
                    question = "Hi, I'm Priya Patel. I want to return everything from ORD-6014."
                    response = await http.post(
                        "/api/chat", json={"thread_id": chat_id, "message": question}
                    )
                    assert response.status_code == 200, response.text
                    events = _sse_events(response.text)
                    kinds = [event["type"] for event in events]
                    assert "error" not in kinds, events
                    assert kinds[:2] == ["message_stored", "trace_started"], kinds
                    assert kinds[-1] == "done", kinds
                    stored = events[0]
                    assert any(e["name"] == "ORD-6014" for e in stored["entities"]), stored
                    calls = [e for e in events if e["type"] == "tool_call"]
                    results = [e for e in events if e["type"] == "tool_result"]
                    assert {e["name"] for e in calls} == TOOL_NAMES
                    assert len(results) == len(calls)

                    traces = (await http.get(f"/api/traces?thread_id={chat_id}")).json()
                    assert len(traces) == 1, traces
                    trace = traces[0]
                    assert trace["success"] is True and trace["seeded"] is False
                    assert trace["tool_call_count"] == len(calls)

                    thread = (await http.get(f"/api/threads/{chat_id}")).json()
                    assert question.startswith(thread["title"].rstrip("…"))
                    recorded = thread["messages"][0]["tool_calls"]
                    assert {c["tool_name"] for c in recorded} == TOOL_NAMES

                    similar = (
                        await http.get(
                            "/api/traces/similar",
                            params={"task": "I want to return everything from my order"},
                        )
                    ).json()
                    assert any(t["seeded"] for t in similar), similar

                    # A real return through the graph-backed environment: the
                    # policy gate, the preview, the confirmation, the graph write.
                    client = await app.state.memory.get_client()
                    session = "chat-e2e-return"
                    item = {"item_id": "ITEM-9090", "reason": "changed_mind"}
                    blocked = await world.run_tool(
                        client, session, "process_return", {**item, "amount": 51, "confirm": True}
                    )
                    assert "get_policies" in blocked["error"]
                    await world.run_tool(client, session, "get_policies", {"topic": "return"})
                    preview = await world.run_tool(
                        client, session, "process_return", {**item, "amount": 0, "confirm": False}
                    )
                    assert preview["status"] == "preview" and preview["refund_amount"] == 51
                    done = await world.run_tool(
                        client, session, "process_return", {**item, "amount": 51, "confirm": True}
                    )
                    assert done["status"] == "returned"
                    order = await world.run_tool(
                        client, session, "get_order", {"order_id": "ORD-6014"}
                    )
                    assert order["status"] == "partially_returned"
                    rows = await client.query.cypher(
                        "MATCH (e:Entity {name: 'ITEM-9090'}) RETURN e.description AS d"
                    )
                    assert "returned" in rows[0]["d"], rows

                    # The revision: Warranty -> WarrantyCoverage, strict, migrated.
                    overview = (await http.get("/api/ontology")).json()
                    revision_1 = overview["active"]["version_id"]
                    warranties_before = overview["label_counts"]["Warranty"]
                    assert warranties_before >= 3
                    renamed = await http.post("/api/ontology/rename", json={})
                    assert renamed.status_code == 200, renamed.text
                    result = renamed.json()
                    assert result["revision"] == 2
                    assert result["migration"]["status"] == "completed"
                    assert result["migration"]["processed"] == result["dry_run_total"] >= 3
                    assert result["client"] == {
                        "domain_id": "customer-support",
                        "validation_mode": "strict",
                    }
                    added = [t["label"] for t in result["diff"]["entity_types"]["added"]]
                    assert added == ["WarrantyCoverage"]

                    after = (await http.get("/api/ontology")).json()
                    assert after["label_counts"]["Warranty"] == 0
                    assert after["label_counts"]["WarrantyCoverage"] == warranties_before

                    again = await http.post("/api/ontology/rename", json={})
                    assert again.status_code == 409, again.text

                    back = await http.post(
                        "/api/ontology/activate", json={"version_id": revision_1}
                    )
                    assert back.status_code == 200, back.text
                    assert back.json()["revision"] == 1

            asyncio.run(drive())
        finally:

            async def restore() -> None:
                client = await connect(memory.build_memory_settings(settings))
                try:
                    await seed.reset_seed(client)
                    if before is not None:
                        await client.ontology.activate(before)
                finally:
                    await client.close()

            try:
                asyncio.run(restore())
            finally:
                config.get_settings.cache_clear()
                agent.get_agent.cache_clear()
