"""Smoke tests for the ontology-lifecycle example.

The ontology surface (``import_`` / ``diff`` / ``migrate`` / ``get_migration``)
is hosted-only and — before this example — was exercised by nothing outside
``tests/unit/nams``. So the whole script body runs here against a ``respx``-mocked
NAMS: no API key, no network, no Neo4j.

The fake is **stateful** on the two things the example actually teaches:

* ``GET /ontologies/active`` starts unbound (404), then reports whichever
  version the script last activated — so the test fails if the script stops
  re-reading the active ontology after ``activate()``.
* migration jobs report ``pending`` → ``running`` → ``completed``, so the test
  fails if the polling loop is replaced by a fixed sleep.

Route payloads mirror ``tests/unit/nams/*`` fixtures (verified against the live
API) and ``src/neo4j_agent_memory/nams/ontology.py`` (verified empirically
against the staging deployment — the ontology routes are absent from the
OpenAPI spec).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("respx", reason="respx not installed")

import httpx  # noqa: E402
import respx  # noqa: E402

from tests.examples._manifests import assert_library_pin  # noqa: E402

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
EXAMPLE_DIR = EXAMPLES_DIR / "ontology-lifecycle"
MAIN_PY = EXAMPLE_DIR / "main.py"
ARROWS_FILE = EXAMPLE_DIR / "schemas" / "support-desk.arrows.json"

ENDPOINT = "https://memory.test/v1"
ONTOLOGY_ID = "ont_support_desk"
CONVERSATION_ID = "00000000-0000-0000-0000-0000000000aa"

SYSTEM_TEMPLATES = [
    {
        "id": "ont_general",
        "name": "general",
        "display_name": "General",
        "is_system": True,
        "current_revision": 1,
        "is_active": False,
    },
    {
        "id": "ont_healthcare",
        "name": "healthcare",
        "display_name": "Healthcare",
        "is_system": True,
        "current_revision": 1,
        "is_active": False,
    },
]

# The shape `POST /ontologies/import` returns for the shipped Arrows document:
# a *draft* body (nothing persisted) plus conversion warnings.
IMPORTED_DOCUMENT = {
    "domain": {
        "id": "support-desk-import",
        "name": "Support Desk Import",
        "description": "Converted from Arrows",
    },
    "entity_types": [
        {
            "label": "Customer",
            "pole_type": "PERSON",
            "properties": [
                {"name": "email", "type": "string", "unique": True},
                {"name": "tier", "type": "string"},
            ],
        },
        {
            "label": "Order",
            "pole_type": "EVENT",
            "properties": [{"name": "order_number", "type": "string", "required": True}],
        },
        {
            "label": "Product",
            "pole_type": "OBJECT",
            "properties": [{"name": "sku", "type": "string", "unique": True}],
        },
        {
            "label": "Ticket",
            "pole_type": "EVENT",
            "properties": [{"name": "reference", "type": "string", "required": True}],
        },
    ],
    "relationships": [
        {"type": "PLACED", "source": "Customer", "target": "Order"},
        {"type": "CONTAINS", "source": "Order", "target": "Product"},
        {"type": "OPENED", "source": "Customer", "target": "Ticket"},
        {"type": "ABOUT", "source": "Ticket", "target": "Order"},
        {"type": "CONCERNS", "source": "Ticket", "target": "Product"},
    ],
}

CONVERSATION = {
    "id": CONVERSATION_ID,
    "userId": "demo",
    "createdAt": "2026-09-10T12:00:00Z",
    "updatedAt": "2026-09-10T12:00:00Z",
}
ENTITIES = [
    {
        "id": "00000000-0000-0000-0000-0000000000e1",
        "name": "Priya Raman",
        "type": "customer",
        "createdAt": "2026-09-10T12:00:05Z",
        "updatedAt": "2026-09-10T12:00:05Z",
    },
    {
        "id": "00000000-0000-0000-0000-0000000000e2",
        "name": "TK-2210",
        "type": "ticket",
        "createdAt": "2026-09-10T12:00:05Z",
        "updatedAt": "2026-09-10T12:00:05Z",
    },
    {
        "id": "00000000-0000-0000-0000-0000000000e3",
        "name": "SO-4417",
        "type": "order",
        "createdAt": "2026-09-10T12:00:05Z",
        "updatedAt": "2026-09-10T12:00:05Z",
    },
]


class FakeNams:
    """A stateful stand-in for the NAMS ontology + memory routes."""

    def __init__(self, *, polls_before_completion: int = 2) -> None:
        self.polls_before_completion = polls_before_completion
        self.revisions: list[dict[str, Any]] = []
        self.active_version_id: str | None = None
        self.extraction_polls = 0
        self.migrations: list[dict[str, Any]] = []
        self.migration_polls: dict[str, int] = {}
        self.import_bodies: list[dict[str, Any]] = []
        self.diff_params: list[dict[str, Any]] = []
        self.cypher_queries: list[str] = []
        self.stored_messages: list[dict[str, Any]] = []

    # -- helpers ----------------------------------------------------------
    def _version(self, document: dict[str, Any], validation_mode: str) -> dict[str, Any]:
        revision = len(self.revisions) + 1
        version = {
            "id": f"ov_{revision}",
            "ontology_id": ONTOLOGY_ID,
            "revision": revision,
            "validation_mode": validation_mode,
            # The service double-encodes the body as a `schema_json` string;
            # the SDK parses it back into an OntologyDocument.
            "schema_json": json.dumps(document),
        }
        self.revisions.append(version)
        return version

    @property
    def current(self) -> dict[str, Any] | None:
        return self.revisions[-1] if self.revisions else None

    def _active(self) -> dict[str, Any] | None:
        return next((v for v in self.revisions if v["id"] == self.active_version_id), None)

    def summaries(self) -> list[dict[str, Any]]:
        rows = list(SYSTEM_TEMPLATES)
        if self.revisions:
            rows.append(
                {
                    "id": ONTOLOGY_ID,
                    "name": "support-desk",
                    "display_name": "Support Desk",
                    "is_system": False,
                    "current_revision": self.revisions[-1]["revision"],
                    "is_active": self.active_version_id is not None,
                }
            )
        return rows

    # -- routes -----------------------------------------------------------
    def list_ontologies(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ontologies": self.summaries()})

    def get_ontology(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "record": {"id": ONTOLOGY_ID, "name": "support-desk", "is_system": False},
                "versions": self.revisions,
            },
        )

    def get_active(self, request: httpx.Request) -> httpx.Response:
        active = self._active()
        if active is None:
            # A workspace with nothing bound: the live service 404s here.
            return httpx.Response(404, json={"detail": "no active ontology"})
        return httpx.Response(200, json={"ontology": json.loads(active["schema_json"])})

    def import_ontology(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.import_bodies.append(body)
        return httpx.Response(
            200,
            json={
                "ontology": IMPORTED_DOCUMENT,
                "detected_format": "arrows",
                "suggested_name": "support-desk-import",
                "warnings": [
                    {
                        "code": "pole_type_inferred",
                        "message": "Inferred pole_type EVENT for label 'Ticket'.",
                        "path": "nodes[3]",
                    }
                ],
            },
        )

    def create_ontology(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            201, json=self._version(body["ontology"], body.get("validation_mode", "permissive"))
        )

    def update_ontology(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200, json=self._version(body["ontology"], body.get("validation_mode", "permissive"))
        )

    def activate_ontology(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.active_version_id = body["version_id"]
        active = self._active()
        assert active is not None, "the script activated a version the fake never minted"
        return httpx.Response(200, json=active)

    def diff_ontology(self, request: httpx.Request) -> httpx.Response:
        self.diff_params.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "from_revision": int(request.url.params.get("from", 1)),
                "to_revision": int(request.url.params.get("to", 2)),
                "entity_types": {
                    "added": [],
                    "removed": [],
                    "renamed": [{"from": "Ticket", "to": "SupportCase"}],
                    "modified": [],
                },
                "relationships": {
                    "added": [],
                    "removed": [],
                    "renamed": [],
                    "modified": [
                        {"type": "OPENED", "source": "Customer", "target": "SupportCase"},
                        {"type": "ABOUT", "source": "SupportCase", "target": "Order"},
                        {"type": "CONCERNS", "source": "SupportCase", "target": "Product"},
                    ],
                },
                "mode_change": {"from": "permissive", "to": "strict"},
            },
        )

    def migrate_ontology(self, request: httpx.Request) -> httpx.Response:
        spec = json.loads(request.content)["spec"]
        job_id = f"mig_{len(self.migrations) + 1}"
        job = {
            "id": job_id,
            "ontology_id": ONTOLOGY_ID,
            "status": "pending",
            "total": 2,
            "processed": 0,
            "errored": 0,
            "spec": spec,
        }
        self.migrations.append(job)
        self.migration_polls[job_id] = 0
        return httpx.Response(202, json=job)

    def get_migration(self, request: httpx.Request) -> httpx.Response:
        job_id = request.url.path.rsplit("/", 1)[-1]
        job = next(j for j in self.migrations if j["id"] == job_id)
        self.migration_polls[job_id] += 1
        polls = self.migration_polls[job_id]
        if polls < self.polls_before_completion:
            return httpx.Response(200, json={**job, "status": "running", "processed": 1})
        return httpx.Response(200, json={**job, "status": "completed", "processed": job["total"]})

    def bulk_add_messages(self, request: httpx.Request) -> httpx.Response:
        """Echo the batch back, the way the live service does."""
        batch = json.loads(request.content)["messages"]
        self.stored_messages.extend(batch)
        return httpx.Response(
            201,
            json={
                "messages": [
                    {
                        "id": f"00000000-0000-0000-0000-00000000000{index}",
                        "conversationId": CONVERSATION_ID,
                        "role": message["role"],
                        "content": message["content"],
                        "createdAt": "2026-09-10T12:00:01Z",
                    }
                    for index, message in enumerate(batch, start=1)
                ]
            },
        )

    def extraction_status(self, request: httpx.Request) -> httpx.Response:
        self.extraction_polls += 1
        summary = {"pending": 6} if self.extraction_polls == 1 else {"completed": 6}
        return httpx.Response(200, json={"summary": summary, "messages": []})

    def cypher(self, request: httpx.Request) -> httpx.Response:
        self.cypher_queries.append(json.loads(request.content)["cypher"])
        return httpx.Response(
            200,
            json={
                "columns": ["labels", "count"],
                "rows": [{"labels": ["Entity", "SupportCase"], "count": 2}],
                "stats": {},
            },
        )


def _mock_nams(router: respx.Router, fake: FakeNams) -> None:
    """Register every route the script drives."""
    # connect() auth probe -> list_conversations(limit=1)
    router.get(f"{ENDPOINT}/conversations").respond(200, json={"conversations": []})
    router.post(f"{ENDPOINT}/conversations").respond(201, json=CONVERSATION)
    router.post(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/messages/bulk").mock(
        side_effect=fake.bulk_add_messages
    )
    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/extraction-status").mock(
        side_effect=fake.extraction_status
    )
    router.post(f"{ENDPOINT}/entities/search").respond(
        200, json={"entities": ENTITIES, "searchType": "vector"}
    )
    # Ontology lifecycle.
    router.get(f"{ENDPOINT}/ontologies").mock(side_effect=fake.list_ontologies)
    router.get(f"{ENDPOINT}/ontologies/active").mock(side_effect=fake.get_active)
    router.post(f"{ENDPOINT}/ontologies/active").mock(side_effect=fake.activate_ontology)
    router.post(f"{ENDPOINT}/ontologies/import").mock(side_effect=fake.import_ontology)
    router.post(f"{ENDPOINT}/ontologies").mock(side_effect=fake.create_ontology)
    router.put(f"{ENDPOINT}/ontologies/{ONTOLOGY_ID}").mock(side_effect=fake.update_ontology)
    router.get(f"{ENDPOINT}/ontologies/{ONTOLOGY_ID}").mock(side_effect=fake.get_ontology)
    router.get(f"{ENDPOINT}/ontologies/{ONTOLOGY_ID}/diff").mock(side_effect=fake.diff_ontology)
    router.post(f"{ENDPOINT}/ontologies/{ONTOLOGY_ID}/migrate").mock(
        side_effect=fake.migrate_ontology
    )
    router.get(url__regex=rf"{ENDPOINT}/ontologies/migrations/.+").mock(
        side_effect=fake.get_migration
    )
    router.post(f"{ENDPOINT}/query").mock(side_effect=fake.cypher)


def _load_main_module():
    """Import ``examples/ontology-lifecycle/main.py`` (a dashed, non-package dir)."""
    spec = importlib.util.spec_from_file_location("ontology_lifecycle_main", MAIN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def example_module(monkeypatch):
    """The example module with its own ``.env`` ignored and polling sped up."""
    monkeypatch.setenv("MEMORY_API_KEY", "nams_test")
    monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)
    monkeypatch.delenv("MEMORY_WORKSPACE_ID", raising=False)
    try:
        module = _load_main_module()
        # A developer's own examples/ontology-lifecycle/.env must not leak in.
        monkeypatch.setattr(module, "load_env", lambda: None)
        monkeypatch.setattr(module, "MIGRATION_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(module, "EXTRACTION_POLL_INTERVAL", 0.0)
        yield module
    finally:
        sys.modules.pop("ontology_lifecycle_main", None)


@pytest.mark.syntax
class TestOntologyLifecycleStructure:
    def test_required_files_exist(self):
        for filename in ("main.py", "README.md", "requirements.txt", ".env.example"):
            assert (EXAMPLE_DIR / filename).exists(), f"Missing: {filename}"
        assert ARROWS_FILE.exists(), "the Arrows document the example imports must ship with it"

    def test_main_compiles(self):
        ast.parse(MAIN_PY.read_text(encoding="utf-8"))

    def test_requirements_pin_the_library_with_the_nams_extra(self):
        pin = assert_library_pin(EXAMPLE_DIR / "requirements.txt")
        assert "nams" in pin.extras, "the ontology surface is hosted-only"

    def test_arrows_document_is_valid_json_with_the_four_domain_labels(self):
        document = json.loads(ARROWS_FILE.read_text(encoding="utf-8"))
        labels = {label for node in document["nodes"] for label in node["labels"]}
        assert {"Customer", "Order", "Product", "Ticket"} <= labels
        assert document["relationships"], "relationships become typed relationships on import"

    def test_main_drives_the_whole_ontology_surface(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        for call in (
            "ontology.list(",
            "ontology.get_active(",
            "ontology.import_(",
            "ontology.create(",
            "ontology.update(",
            "ontology.activate(",
            "ontology.diff(",
            "ontology.migrate(",
            "ontology.get_migration(",
            "bulk_add_messages(",
            "wait_for_extraction(",
            "search_entities(",
            "query.cypher(",
        ):
            assert call in source, f"missing the {call!r} demonstration"

    def test_main_polls_the_migration_instead_of_sleeping_blind(self):
        source = MAIN_PY.read_text(encoding="utf-8")
        assert "TERMINAL_STATUSES" in source
        assert "dry_run=True" in source, "the dry run is the safe half of the lesson"
        assert "dry_run=False" in source

    def test_main_never_touches_a_bolt_uri(self):
        assert "bolt://" not in MAIN_PY.read_text(encoding="utf-8")

    def test_env_example_documents_the_hosted_variables(self):
        content = (EXAMPLE_DIR / ".env.example").read_text(encoding="utf-8")
        assert "MEMORY_API_KEY" in content
        assert "MEMORY_ENDPOINT" in content
        assert "MEMORY_WORKSPACE_ID" in content
        assert "nams_xxx" in content, "placeholder only — never a real key"

    def test_readme_follows_labs_conventions(self):
        readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j-Labs" in readme, "missing the Labs badge"
        assert "Neo4j Labs Project" in readme, "missing the Labs disclaimer"
        assert "## Prerequisites" in readme
        assert "## Support" in readme
        assert "Expected output" in readme
        assert "Verified against" in readme
        assert "MEMORY_API_KEY" in readme, "the README must show the live command"
        assert "tutorials/ontology-quickstart" in readme, "cross-link the paired tutorial"
        assert "docs/.../" not in readme


@pytest.mark.imports
class TestOntologyLifecycleImports:
    def test_the_surface_the_example_imports_exists(self):
        from neo4j_agent_memory import NamsSettings, connect  # noqa: F401
        from neo4j_agent_memory.core.exceptions import (  # noqa: F401
            NotFoundError,
            NotSupportedError,
        )
        from neo4j_agent_memory.nams import (  # noqa: F401
            MigrationJob,
            OntologyDocument,
            OntologyVersion,
        )

    def test_module_imports_with_a_fake_key(self, example_module):
        assert example_module.DOMAIN_ID == "support-desk"
        assert example_module.OLD_TYPE == "Ticket"
        assert example_module.NEW_TYPE == "SupportCase"
        assert len(example_module.TRANSCRIPT) >= 4

    def test_rename_entity_type_rewrites_relationship_endpoints(self, example_module):
        from neo4j_agent_memory.nams import OntologyDocument

        document = OntologyDocument.model_validate(IMPORTED_DOCUMENT)
        revised = example_module.rename_entity_type(document, "Ticket", "SupportCase")

        assert [e.label for e in revised.entity_types] == [
            "Customer",
            "Order",
            "Product",
            "SupportCase",
        ]
        assert all("Ticket" not in (r.source, r.target) for r in revised.relationships), (
            "a dangling endpoint makes revision 2 invalid"
        )
        # The original is untouched — the diff has to have something to compare.
        assert any(e.label == "Ticket" for e in document.entity_types)


class TestOntologyLifecycleRun:
    """Execute ``main()`` end to end against the stateful mocked NAMS."""

    async def test_main_runs_the_whole_lifecycle(self, example_module, capsys):
        fake = FakeNams()
        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router, fake)
            await example_module.main()

        out = capsys.readouterr().out
        # 1. survey — a fresh workspace has nothing bound
        assert f"Connected to {ENDPOINT} (backend=nams)" in out
        assert "2 system template(s), 0 workspace-owned" in out
        assert "none bound yet" in out
        # 2. import
        assert "detected format: arrows" in out
        assert "Customer -> PERSON" in out
        assert "Ticket -> EVENT" in out
        assert "warning [pole_type_inferred]" in out
        # 3. create + activate
        assert "Created support-desk revision 1 (permissive)" in out
        assert "Activated: Support Desk revision 1 (permissive)" in out
        # 4. ingest under the active ontology
        assert f"Conversation {CONVERSATION_ID}: stored 6 message(s) in one request" in out
        assert "Extraction settled: True; 3 entity(ies) searchable" in out
        assert "Priya Raman (CUSTOMER)" in out
        # 5/6. revise + diff
        assert "Revision 2: Ticket -> SupportCase, validation mode permissive -> strict" in out
        assert "renamed=1 [Ticket -> SupportCase]" in out
        assert "modified=3" in out
        assert "'from': 'permissive', 'to': 'strict'" in out
        # 7. migrate, dry run then real, polled to completion
        assert "migration mig_1 queued (dry run), status=pending" in out
        assert "migration completed (dry run): 2 re-labelled, 0 errored" in out
        assert "migration mig_2 queued (for real), status=pending" in out
        assert "migration completed (for real): 2 re-labelled, 0 errored" in out
        # 8. read back under revision 2
        assert "Active: Support Desk revision 2 (strict)" in out
        assert "'count': 2" in out

        # The identity the script persists comes from the document's `domain`
        # block, not from the converter's suggestion.
        created = json.loads(fake.revisions[0]["schema_json"])
        assert created["domain"]["id"] == "support-desk"
        # Revision 2 carries the rename, revision 1 does not.
        revised = json.loads(fake.revisions[1]["schema_json"])
        assert {e["label"] for e in created["entity_types"]} >= {"Ticket"}
        assert {e["label"] for e in revised["entity_types"]} >= {"SupportCase"}
        assert fake.revisions[1]["validation_mode"] == "strict"

    async def test_the_migration_is_driven_by_the_job_spec_the_diff_implies(
        self, example_module, capsys
    ):
        fake = FakeNams()
        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router, fake)
            await example_module.main()

        assert len(fake.migrations) == 2, "one dry run, one real migration"
        dry, real = (job["spec"] for job in fake.migrations)
        assert dry["dry_run"] is True
        assert real["dry_run"] is False
        for spec in (dry, real):
            assert spec["type_mappings"] == [{"from": "Ticket", "to": "SupportCase"}]
            assert spec["from_version_id"] == "ov_1"
            assert spec["to_version_id"] == "ov_2"
        # Both jobs were polled past their non-terminal status.
        assert all(count >= 2 for count in fake.migration_polls.values())
        # The diff asked for the two revisions the script actually minted.
        assert fake.diff_params == [{"from": "1", "to": "2"}]

    async def test_import_sends_the_shipped_arrows_document(self, example_module):
        fake = FakeNams()
        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router, fake)
            await example_module.main()

        assert len(fake.import_bodies) == 1
        body = fake.import_bodies[0]
        assert body["format"] == "arrows"
        assert json.loads(body["content"]) == json.loads(ARROWS_FILE.read_text(encoding="utf-8")), (
            "the example must import the document it ships, not an inline copy"
        )

    async def test_cypher_read_back_is_read_only(self, example_module):
        fake = FakeNams()
        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router, fake)
            await example_module.main()

        assert len(fake.cypher_queries) == 1
        query = fake.cypher_queries[0].upper()
        assert "SUPPORTCASE" in query
        for keyword in ("CREATE", "MERGE", "DELETE", "SET "):
            assert keyword not in query, "the NAMS query endpoint is read-only by contract"

    async def test_a_workspace_that_already_owns_the_ontology_is_versioned_not_duplicated(
        self, example_module, capsys
    ):
        """Re-running must mint the next revision, never a second ontology."""
        fake = FakeNams()
        # Pre-seed revision 1 as if a previous run had created it.
        fake._version(IMPORTED_DOCUMENT, "permissive")
        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router, fake)
            create = router.post(f"{ENDPOINT}/ontologies").mock(side_effect=fake.create_ontology)
            await example_module.main()

        out = capsys.readouterr().out
        assert "Reused support-desk: new revision 2 (permissive)" in out
        assert not create.called, "an existing ontology must be updated, not re-created"
        assert fake.diff_params == [{"from": "2", "to": "3"}], (
            "the diff must use the revisions this run actually minted"
        )

    async def test_main_exits_without_an_api_key(self, example_module, monkeypatch):
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)
        with pytest.raises(SystemExit, match="MEMORY_API_KEY"):
            await example_module.main()

    async def test_a_failed_conversion_stops_with_a_readable_error(
        self, example_module, monkeypatch
    ):
        """An unconvertible document must not be persisted as an empty ontology."""
        fake = FakeNams()
        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router, fake)
            router.post(f"{ENDPOINT}/ontologies/import").respond(
                200,
                json={
                    "ontology": None,
                    "warnings": [{"code": "unsupported", "message": "Unrecognised document"}],
                },
            )
            with pytest.raises(SystemExit, match="Unrecognised document"):
                await example_module.main()

        assert fake.revisions == [], "nothing may be persisted when the import fails"
