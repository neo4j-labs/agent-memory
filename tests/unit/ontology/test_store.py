"""Unit tests for ontology/store.py — the bolt-backed ontology accessor.

The Neo4j client is faked (the same shape ``tests/unit/test_schema_persistence.py``
mocks): every ``execute_read`` / ``execute_write`` call is recorded, and canned
rows are returned per query. That lets these tests assert *which* centralised
Cypher constant a method reached for and *what* parameters it sent, without a
database.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from neo4j_agent_memory.core.exceptions import (
    NotFoundError,
    NotSupportedError,
    SchemaError,
)
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY
from neo4j_agent_memory.ontology.models import (
    ActiveOntology,
    DomainInfo,
    EntityTypeDef,
    MigrationJob,
    Ontology,
    OntologyDocument,
    OntologyVersion,
    RelationshipDef,
)
from neo4j_agent_memory.ontology.store import (
    DEFAULT_VALIDATION_MODE,
    TEMPLATE_ID_PREFIX,
    BoltOntology,
)

Rows = list[dict[str, Any]]
Responder = Rows | Callable[[dict[str, Any]], Rows]


# -----------------------------------------------------------------------------
# Fake client
# -----------------------------------------------------------------------------


class FakeNeo4jClient:
    """Records queries and replays canned rows, keyed by query text."""

    def __init__(self) -> None:
        self.reads: list[tuple[str, dict[str, Any]]] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.read_rows: list[tuple[str, Responder]] = []
        self.write_rows: list[tuple[str, Responder]] = []

    def on_read(self, marker: str, rows: Responder) -> None:
        self.read_rows.append((marker, rows))

    def on_write(self, marker: str, rows: Responder) -> None:
        self.write_rows.append((marker, rows))

    async def execute_read(self, query: str, parameters: dict[str, Any] | None = None) -> Rows:
        self.reads.append((query, parameters or {}))
        return _resolve(self.read_rows, query, parameters or {})

    async def execute_write(self, query: str, parameters: dict[str, Any] | None = None) -> Rows:
        self.writes.append((query, parameters or {}))
        return _resolve(self.write_rows, query, parameters or {})

    # -- assertions helpers --

    def read_params(self, marker: str) -> dict[str, Any]:
        return _find(self.reads, marker)

    def write_params(self, marker: str) -> dict[str, Any]:
        return _find(self.writes, marker)

    @property
    def read_queries(self) -> list[str]:
        return [query for query, _ in self.reads]

    @property
    def write_queries(self) -> list[str]:
        return [query for query, _ in self.writes]


def _resolve(table: list[tuple[str, Responder]], query: str, params: dict[str, Any]) -> Rows:
    for marker, rows in table:
        if marker == query:
            return rows(params) if callable(rows) else rows
    for marker, rows in table:
        if marker in query:
            return rows(params) if callable(rows) else rows
    return []


def _find(calls: list[tuple[str, dict[str, Any]]], marker: str) -> dict[str, Any]:
    for query, params in calls:
        if marker == query or marker in query:
            return params
    raise AssertionError(f"No call matching {marker[:60]!r}; saw {len(calls)} calls.")


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

PERSON = EntityTypeDef(label="Person", pole_type="PERSON", description="A named human.")
COMPANY = EntityTypeDef(label="Company", pole_type="ORGANIZATION", subtype="COMPANY")
WORKS_AT = RelationshipDef(
    type="WORKS_AT", source="Person", target="Company", description="Employment."
)


def make_document(
    *,
    name: str = "support-desk",
    entity_types: list[EntityTypeDef] | None = None,
    relationships: list[RelationshipDef] | None = None,
) -> OntologyDocument:
    return OntologyDocument(
        domain=DomainInfo(id=name, name=name, description=f"{name} ontology"),
        entity_types=entity_types if entity_types is not None else [PERSON, COMPANY],
        relationships=relationships if relationships is not None else [WORKS_AT],
    )


def version_node(
    document: OntologyDocument,
    *,
    id: str = "v1",
    ontology_id: str = "o1",
    revision: int = 1,
    validation_mode: str = DEFAULT_VALIDATION_MODE,
    is_active: bool = False,
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "ontology_id": ontology_id,
        "revision": revision,
        "validation_mode": validation_mode,
        "document": document.model_dump_json(),
        "schema_hash": "deadbeef",
        "is_active": is_active,
        "created_at": "2026-01-01T00:00:00Z",
        "message": message,
    }


def ontology_node(
    *, id: str = "o1", name: str = "support-desk", description: str | None = None
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "description": description,
        "is_system": False,
        "created_at": "2026-01-01T00:00:00Z",
    }


@pytest.fixture
def client() -> FakeNeo4jClient:
    return FakeNeo4jClient()


@pytest.fixture
def store(client: FakeNeo4jClient) -> BoltOntology:
    # BoltOntology only ever calls execute_read / execute_write.
    return BoltOntology(client)  # type: ignore[arg-type]


def echo_version(params: dict[str, Any]) -> Rows:
    """Return the node CREATE_ONTOLOGY_VERSION would have written."""
    return [
        {
            "v": {
                **params,
                "is_active": False,
                "created_at": "2026-01-01T00:00:00Z",
            }
        }
    ]


# -----------------------------------------------------------------------------
# list()
# -----------------------------------------------------------------------------


class TestList:
    @pytest.mark.asyncio
    async def test_built_in_templates_come_first_and_are_flagged_system(self, store, client):
        client.on_read(queries.LIST_ONTOLOGIES, [])

        summaries = await store.list()

        assert summaries, "expected the built-in templates"
        assert all(s.is_system for s in summaries)
        assert all(s.id.startswith(TEMPLATE_ID_PREFIX) for s in summaries)
        assert all(s.current_revision is None for s in summaries)
        assert all(not s.is_active for s in summaries)

    @pytest.mark.asyncio
    async def test_stored_ontologies_follow_the_templates(self, store, client):
        client.on_read(
            queries.LIST_ONTOLOGIES,
            [{"ontology": ontology_node(), "current_revision": 3, "is_active": True}],
        )

        summaries = await store.list()

        stored = [s for s in summaries if not s.is_system]
        assert len(stored) == 1
        assert stored[0].id == "o1"
        assert stored[0].name == "support-desk"
        assert stored[0].current_revision == 3
        assert stored[0].is_active is True
        # Ordering: every system row precedes every stored row.
        assert summaries.index(stored[0]) == len(summaries) - 1

    @pytest.mark.asyncio
    async def test_uses_the_centralised_query(self, store, client):
        client.on_read(queries.LIST_ONTOLOGIES, [])
        await store.list()
        assert queries.LIST_ONTOLOGIES in client.read_queries


# -----------------------------------------------------------------------------
# get()
# -----------------------------------------------------------------------------


class TestGet:
    @pytest.mark.asyncio
    async def test_returns_record_and_versions_sorted_by_revision(self, store, client):
        document = make_document()
        client.on_read(
            queries.GET_ONTOLOGY,
            [
                {
                    "ontology": ontology_node(),
                    "versions": [
                        version_node(document, id="v2", revision=2),
                        version_node(document, id="v1", revision=1),
                    ],
                }
            ],
        )

        ontology = await store.get("o1")

        assert isinstance(ontology, Ontology)
        assert ontology.record.id == "o1"
        assert [v.revision for v in ontology.versions] == [1, 2]
        assert ontology.versions[0].document == document
        assert client.read_params(queries.GET_ONTOLOGY) == {"id": "o1"}

    @pytest.mark.asyncio
    async def test_unknown_id_raises_not_found(self, store, client):
        client.on_read(queries.GET_ONTOLOGY, [])
        with pytest.raises(NotFoundError, match="nope"):
            await store.get("nope")

    @pytest.mark.asyncio
    async def test_template_id_resolves_without_touching_the_database(self, store, client):
        ontology = await store.get(f"{TEMPLATE_ID_PREFIX}podcast")

        assert client.reads == []
        assert ontology.record.is_system is True
        assert ontology.record.name == "podcast"
        assert len(ontology.versions) == 1
        assert ontology.versions[0].revision == 1
        assert ontology.versions[0].document is not None
        assert ontology.versions[0].document.labels()

    @pytest.mark.asyncio
    async def test_unknown_template_raises_not_found(self, store):
        with pytest.raises(NotFoundError, match="not-a-template"):
            await store.get(f"{TEMPLATE_ID_PREFIX}not-a-template")


# -----------------------------------------------------------------------------
# get_active()
# -----------------------------------------------------------------------------


class TestGetActive:
    @pytest.mark.asyncio
    async def test_composes_document_and_version_metadata(self, store, client):
        document = make_document()
        client.on_read(
            queries.GET_ACTIVE_ONTOLOGY_VERSION,
            [
                {
                    "v": version_node(
                        document, id="v7", revision=4, validation_mode="strict", is_active=True
                    ),
                    "ontology": ontology_node(),
                }
            ],
        )

        active = await store.get_active()

        assert isinstance(active, ActiveOntology)
        assert active.document == document
        assert active.validation_mode == "strict"
        assert active.revision == 4
        assert active.ontology_id == "o1"
        assert active.version_id == "v7"

    @pytest.mark.asyncio
    async def test_nothing_active_raises_not_supported_like_nams(self, store, client):
        client.on_read(queries.GET_ACTIVE_ONTOLOGY_VERSION, [])

        with pytest.raises(NotSupportedError) as excinfo:
            await store.get_active()

        assert "No active ontology" in str(excinfo.value)
        assert excinfo.value.method == "ontology.get_active"

    @pytest.mark.asyncio
    async def test_does_not_silently_fall_back_to_poleo(self, store, client):
        client.on_read(queries.GET_ACTIVE_ONTOLOGY_VERSION, [])
        with pytest.raises(NotSupportedError):
            await store.get_active()

    @pytest.mark.asyncio
    async def test_unreadable_document_raises_schema_error(self, store, client):
        broken = version_node(make_document(), is_active=True)
        broken["document"] = "{not json"
        client.on_read(queries.GET_ACTIVE_ONTOLOGY_VERSION, [{"v": broken, "ontology": None}])

        with pytest.raises(SchemaError, match="unreadable"):
            await store.get_active()


# -----------------------------------------------------------------------------
# create()
# -----------------------------------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    async def test_writes_ontology_then_revision_one(self, store, client):
        document = make_document()
        client.on_write(queries.CREATE_ONTOLOGY, [{"o": ontology_node()}])
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        version = await store.create("ignored", document)

        assert isinstance(version, OntologyVersion)
        assert version.revision == 1
        assert version.validation_mode == DEFAULT_VALIDATION_MODE
        assert version.document == document

        created = client.write_params(queries.CREATE_ONTOLOGY)
        # Identity comes from schema.domain, not the ``name`` argument.
        assert created["name"] == "support-desk"
        assert created["description"] == "support-desk ontology"
        assert len(created["id"]) == 32  # uuid4().hex

        version_params = client.write_params(queries.CREATE_ONTOLOGY_VERSION)
        assert version_params["ontology_id"] == created["id"]
        assert version_params["revision"] == 1
        assert version_params["validation_mode"] == DEFAULT_VALIDATION_MODE
        assert json.loads(version_params["document"])["domain"]["id"] == "support-desk"
        assert len(version_params["schema_hash"]) == 64  # sha256 hexdigest

    @pytest.mark.asyncio
    async def test_falls_back_to_the_name_argument(self, store, client):
        document = OntologyDocument(domain=DomainInfo(id="", name=""), entity_types=[PERSON])
        client.on_write(queries.CREATE_ONTOLOGY, [{"o": ontology_node()}])
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        await store.create("from-argument", document)

        assert client.write_params(queries.CREATE_ONTOLOGY)["name"] == "from-argument"

    @pytest.mark.asyncio
    async def test_accepts_a_mapping(self, store, client):
        client.on_write(queries.CREATE_ONTOLOGY, [{"o": ontology_node()}])
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        version = await store.create("x", make_document().model_dump())

        assert version.document is not None
        assert version.document.labels() == ["Person", "Company"]

    @pytest.mark.asyncio
    async def test_validation_mode_is_honoured(self, store, client):
        client.on_write(queries.CREATE_ONTOLOGY, [{"o": ontology_node()}])
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        await store.create("x", make_document(), validation_mode="strict")

        assert client.write_params(queries.CREATE_ONTOLOGY_VERSION)["validation_mode"] == "strict"

    @pytest.mark.asyncio
    async def test_structural_problems_raise_value_error_and_write_nothing(self, store, client):
        broken = make_document(
            entity_types=[PERSON],
            relationships=[RelationshipDef(type="WORKS_AT", source="Person", target="Ghost")],
        )

        with pytest.raises(ValueError, match="structurally invalid") as excinfo:
            await store.create("broken", broken)

        assert "Ghost" in str(excinfo.value)
        assert client.writes == []

    @pytest.mark.asyncio
    async def test_reports_every_problem_at_once(self, store):
        broken = make_document(
            entity_types=[
                EntityTypeDef(label="Person", pole_type="NOPE"),
                EntityTypeDef(label="Person", pole_type="PERSON"),
            ],
            relationships=[],
        )
        with pytest.raises(ValueError) as excinfo:
            await store.create("broken", broken)

        message = str(excinfo.value)
        assert "duplicate entity label" in message
        assert "pole_type" in message


# -----------------------------------------------------------------------------
# update()
# -----------------------------------------------------------------------------


class TestUpdate:
    @pytest.mark.asyncio
    async def test_creates_revision_n_plus_one(self, store, client):
        client.on_read(
            queries.GET_ONTOLOGY_MAX_REVISION,
            [{"ontology_id": "o1", "max_revision": 4, "latest_validation_mode": "strict"}],
        )
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        version = await store.update("o1", make_document())

        assert version.revision == 5
        params = client.write_params(queries.CREATE_ONTOLOGY_VERSION)
        assert params["revision"] == 5
        assert params["ontology_id"] == "o1"

    @pytest.mark.asyncio
    async def test_inherits_the_previous_validation_mode(self, store, client):
        client.on_read(
            queries.GET_ONTOLOGY_MAX_REVISION,
            [{"ontology_id": "o1", "max_revision": 1, "latest_validation_mode": "strict"}],
        )
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        version = await store.update("o1", make_document())

        assert version.validation_mode == "strict"

    @pytest.mark.asyncio
    async def test_explicit_validation_mode_wins(self, store, client):
        client.on_read(
            queries.GET_ONTOLOGY_MAX_REVISION,
            [{"ontology_id": "o1", "max_revision": 1, "latest_validation_mode": "strict"}],
        )
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        version = await store.update("o1", make_document(), validation_mode="permissive")

        assert version.validation_mode == "permissive"

    @pytest.mark.asyncio
    async def test_does_not_activate_the_new_revision(self, store, client):
        client.on_read(
            queries.GET_ONTOLOGY_MAX_REVISION,
            [{"ontology_id": "o1", "max_revision": 1, "latest_validation_mode": None}],
        )
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        await store.update("o1", make_document())

        assert queries.ACTIVATE_ONTOLOGY_VERSION not in client.write_queries

    @pytest.mark.asyncio
    async def test_unknown_ontology_raises_not_found(self, store, client):
        client.on_read(queries.GET_ONTOLOGY_MAX_REVISION, [])
        with pytest.raises(NotFoundError):
            await store.update("missing", make_document())

    @pytest.mark.asyncio
    async def test_templates_are_read_only(self, store, client):
        with pytest.raises(ValueError, match="read-only"):
            await store.update(f"{TEMPLATE_ID_PREFIX}podcast", make_document())
        assert client.writes == []

    @pytest.mark.asyncio
    async def test_invalid_document_is_rejected_before_any_read(self, store, client):
        broken = make_document(
            entity_types=[PERSON],
            relationships=[RelationshipDef(type="X", source="Ghost", target="Person")],
        )
        with pytest.raises(ValueError, match="structurally invalid"):
            await store.update("o1", broken)
        assert client.reads == []
        assert client.writes == []


# -----------------------------------------------------------------------------
# activate() / delete()
# -----------------------------------------------------------------------------


class TestActivate:
    @pytest.mark.asyncio
    async def test_uses_the_single_write_activation_query(self, store, client):
        document = make_document()
        client.on_write(
            queries.ACTIVATE_ONTOLOGY_VERSION,
            [{"v": version_node(document, id="v9", is_active=True), "deactivated": 2}],
        )

        version = await store.activate("v9")

        assert version.id == "v9"
        assert client.write_params(queries.ACTIVATE_ONTOLOGY_VERSION) == {"id": "v9"}

    def test_the_activation_query_clears_every_other_version(self):
        # Single-active invariant lives in the Cypher: one write both sets the
        # flag on the target and clears it everywhere else.
        query = queries.ACTIVATE_ONTOLOGY_VERSION
        assert "SET v.is_active = true" in query
        assert "SET other.is_active = false" in query
        assert "other.id <> v.id" in query

    def test_the_activation_query_takes_a_write_lock_first(self):
        """``other.is_active`` is read unlocked, so writers need serialising.

        Without the shared-node write, two concurrent activations could each
        see the other's version as already inactive and both commit
        ``is_active = true``. Writing to ``(:OntologyLock {id: 'active'})``
        before the read makes Neo4j serialise them on that node.
        """
        query = queries.ACTIVATE_ONTOLOGY_VERSION
        assert "MERGE (lock:OntologyLock {id: 'active'})" in query
        assert "SET lock.updated_at = datetime()" in query
        # The lock must be taken before anything reads other.is_active.
        assert query.index("MERGE (lock:OntologyLock") < query.index("other.is_active = true")
        assert query.index("SET lock.updated_at") < query.index("SET other.is_active = false")

    def test_the_lock_node_has_a_uniqueness_constraint(self):
        """An index-backed MERGE is what actually locks; declare the constraint.

        The name starts with ``ontology_``, so ``SchemaManager.drop_all``
        already recognises it as a memory-schema element.
        """
        import inspect

        from neo4j_agent_memory.graph.schema import SchemaManager

        source = inspect.getsource(SchemaManager.setup_constraints)
        assert '("ontology_lock_id", "OntologyLock", "id")' in source
        assert SchemaManager(None)._is_memory_schema("ontology_lock_id") is True

    @pytest.mark.asyncio
    async def test_unknown_version_raises_not_found(self, store, client):
        client.on_write(queries.ACTIVATE_ONTOLOGY_VERSION, [])
        with pytest.raises(NotFoundError, match="v9"):
            await store.activate("v9")


class TestDelete:
    @pytest.mark.asyncio
    async def test_deletes_via_the_cascade_query(self, store, client):
        client.on_write(queries.DELETE_ONTOLOGY, [{"deleted_versions": 2, "deleted_migrations": 1}])

        assert await store.delete("o1") is None
        assert client.write_params(queries.DELETE_ONTOLOGY) == {"id": "o1"}

    @pytest.mark.asyncio
    async def test_unknown_ontology_raises_not_found(self, store, client):
        client.on_write(queries.DELETE_ONTOLOGY, [])
        with pytest.raises(NotFoundError):
            await store.delete("missing")

    @pytest.mark.asyncio
    async def test_templates_cannot_be_deleted(self, store, client):
        with pytest.raises(ValueError, match="read-only"):
            await store.delete(f"{TEMPLATE_ID_PREFIX}news")
        assert client.writes == []


# -----------------------------------------------------------------------------
# clone()
# -----------------------------------------------------------------------------


class TestClone:
    @pytest.mark.asyncio
    async def test_copies_a_template_at_revision_one(self, store, client):
        client.on_read(queries.LIST_ONTOLOGIES, [])
        client.on_write(queries.CREATE_ONTOLOGY, [{"o": ontology_node()}])
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        version = await store.clone("podcast")

        assert version.revision == 1
        assert client.write_params(queries.CREATE_ONTOLOGY)["name"] == "podcast"
        assert "podcast" in (client.write_params(queries.CREATE_ONTOLOGY_VERSION)["message"] or "")
        assert version.document is not None
        assert version.document.domain.id == "podcast"

    @pytest.mark.asyncio
    async def test_is_not_activated(self, store, client):
        client.on_read(queries.LIST_ONTOLOGIES, [])
        client.on_write(queries.CREATE_ONTOLOGY, [{"o": ontology_node()}])
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        await store.clone("podcast")

        assert queries.ACTIVATE_ONTOLOGY_VERSION not in client.write_queries

    @pytest.mark.asyncio
    async def test_disambiguates_a_taken_name(self, store, client):
        client.on_read(
            queries.LIST_ONTOLOGIES,
            [
                {"ontology": ontology_node(id="a", name="podcast")},
                {"ontology": ontology_node(id="b", name="podcast-copy-1")},
            ],
        )
        client.on_write(queries.CREATE_ONTOLOGY, [{"o": ontology_node()}])
        client.on_write(queries.CREATE_ONTOLOGY_VERSION, echo_version)

        version = await store.clone("podcast")

        assert client.write_params(queries.CREATE_ONTOLOGY)["name"] == "podcast-copy-2"
        assert version.document is not None
        assert version.document.domain.name == "podcast-copy-2"

    @pytest.mark.asyncio
    async def test_unknown_template_raises_not_found(self, store, client):
        with pytest.raises(NotFoundError, match="nope"):
            await store.clone("nope")
        assert client.writes == []


# -----------------------------------------------------------------------------
# import_()
# -----------------------------------------------------------------------------


class TestImport:
    @pytest.mark.asyncio
    async def test_url_is_a_nams_capability(self, store):
        with pytest.raises(NotSupportedError) as excinfo:
            await store.import_(url="https://example.com/o.json")
        assert "NAMS" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_missing_content_raises_value_error(self, store):
        with pytest.raises(ValueError, match="content="):
            await store.import_()

    @pytest.mark.asyncio
    async def test_unsupported_format_is_not_supported_and_names_nams(self, store):
        with pytest.raises(NotSupportedError) as excinfo:
            await store.import_(content="<rdf/>", format="rdf")
        assert "NAMS" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_native_json_round_trips(self, store, client):
        document = make_document()

        result = await store.import_(content=document.model_dump_json())

        assert client.reads == [] and client.writes == []  # non-persisted draft
        assert result.detected_format == "native"
        assert result.document == document
        assert result.suggested_name == "support-desk"
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_yaml_is_parsed(self, store):
        yaml = pytest.importorskip("yaml")
        payload = yaml.safe_dump(make_document().model_dump(mode="json"))

        result = await store.import_(content=payload, format="yaml")

        assert result.document is not None
        assert result.document.labels() == ["Person", "Company"]

    @pytest.mark.asyncio
    async def test_auto_detects_an_arrows_export(self, store):
        arrows = json.dumps(
            {
                "nodes": [
                    {"id": "n0", "labels": ["Person"], "properties": {"name": "string"}},
                    {"id": "n1", "labels": ["Company"], "properties": {}},
                ],
                "relationships": [{"type": "WORKS_AT", "fromId": "n0", "toId": "n1"}],
            }
        )

        result = await store.import_(content=arrows)

        assert result.detected_format == "arrows"
        assert result.document is not None
        assert result.document.labels() == ["Person", "Company"]
        assert ("Person", "WORKS_AT", "Company") in result.document.patterns()

    @pytest.mark.asyncio
    async def test_explicit_arrows_format(self, store):
        arrows = json.dumps({"nodes": [{"id": "n0", "labels": ["Person"]}], "relationships": []})
        result = await store.import_(content=arrows, format="arrows")
        assert result.detected_format == "arrows"

    @pytest.mark.parametrize("fmt", ["json", None, "auto"])
    @pytest.mark.asyncio
    async def test_arrows_shape_is_detected_for_every_syntax_only_format(self, store, fmt):
        """The regression: an arrows export *is* JSON.

        ``format="json"`` only names a syntax, so it used to skip shape
        detection, reach ``_parse_native`` and fail with a raw pydantic error
        about a missing ``domain``. Only ``native`` asserts the body really is
        an ontology document.
        """
        arrows = json.dumps(
            {
                "nodes": [
                    {"id": "n0", "labels": ["Person"]},
                    {"id": "n1", "labels": ["Company"]},
                ],
                "relationships": [{"type": "WORKS_AT", "fromId": "n0", "toId": "n1"}],
            }
        )

        result = await store.import_(content=arrows, format=fmt)

        assert result.detected_format == "arrows"
        assert result.document is not None
        assert result.document.labels() == ["Person", "Company"]

    @pytest.mark.asyncio
    async def test_yaml_arrows_is_detected_too(self, store):
        yaml = pytest.importorskip("yaml")
        arrows = yaml.safe_dump(
            {"nodes": [{"id": "n0", "labels": ["Person"]}], "relationships": []}
        )
        result = await store.import_(content=arrows, format="yaml")
        assert result.detected_format == "arrows"

    @pytest.mark.asyncio
    async def test_native_still_refuses_to_guess(self, store):
        arrows = json.dumps({"nodes": [{"id": "n0", "labels": ["Person"]}], "relationships": []})
        with pytest.raises(Exception):  # noqa: B017 — pydantic's own error is fine here
            await store.import_(content=arrows, format="native")

    @pytest.mark.asyncio
    async def test_a_legacy_entity_schema_body_is_converted(self, store):
        legacy = json.dumps(
            {
                "name": "medical",
                "entity_types": [{"name": "PERSON", "subtypes": ["PATIENT"]}],
                "relation_types": [],
            }
        )

        result = await store.import_(content=legacy)

        assert result.detected_format == "entity_schema"
        assert result.document is not None
        assert "PERSON" in result.document.labels()

    @pytest.mark.asyncio
    async def test_structural_problems_surface_as_warnings(self, store):
        broken = make_document(
            entity_types=[PERSON],
            relationships=[RelationshipDef(type="WORKS_AT", source="Person", target="Ghost")],
        )

        result = await store.import_(content=broken.model_dump_json())

        codes = {w.code for w in result.warnings}
        assert codes == {"invalid_structure"}
        assert any("Ghost" in (w.message or "") for w in result.warnings)

    @pytest.mark.asyncio
    async def test_unparseable_content_raises_value_error(self, store):
        with pytest.raises(ValueError):
            await store.import_(content="{not json", format="json")


# -----------------------------------------------------------------------------
# diff()
# -----------------------------------------------------------------------------


class TestDiff:
    @pytest.mark.asyncio
    async def test_diffs_two_revisions_and_labels_them(self, store, client):
        before = make_document(entity_types=[PERSON], relationships=[])
        after = make_document(entity_types=[PERSON, COMPANY], relationships=[])
        client.on_read(
            queries.GET_ONTOLOGY,
            [
                {
                    "ontology": ontology_node(),
                    "versions": [
                        version_node(before, id="v1", revision=1),
                        version_node(after, id="v2", revision=2),
                    ],
                }
            ],
        )

        diff = await store.diff("o1", 1, 2)

        assert diff.from_revision == 1
        assert diff.to_revision == 2
        assert [et["label"] for et in diff.entity_types["added"]] == ["Company"]
        assert diff.entity_types["removed"] == []

    @pytest.mark.asyncio
    async def test_unknown_revision_raises_not_found(self, store, client):
        client.on_read(
            queries.GET_ONTOLOGY,
            [
                {
                    "ontology": ontology_node(),
                    "versions": [version_node(make_document(), id="v1", revision=1)],
                }
            ],
        )
        with pytest.raises(NotFoundError, match="revision 9"):
            await store.diff("o1", 1, 9)


# -----------------------------------------------------------------------------
# migrate() / get_migration()
# -----------------------------------------------------------------------------

OLD_DOC = make_document(
    name="v1",
    entity_types=[EntityTypeDef(label="Client", pole_type="PERSON", subtype="INDIVIDUAL")],
    relationships=[],
)
NEW_DOC = make_document(
    name="v2",
    entity_types=[EntityTypeDef(label="Customer", pole_type="ORGANIZATION", subtype="COMPANY")],
    relationships=[],
)


def arm_migration(client: FakeNeo4jClient, *, old=OLD_DOC, new=NEW_DOC) -> None:
    def version(params: dict[str, Any]) -> Rows:
        document = old if params["id"] == "from-v" else new
        return [{"v": version_node(document, id=params["id"])}]

    client.on_read(queries.GET_ONTOLOGY_VERSION, version)
    client.on_write(
        queries.CREATE_ONTOLOGY_MIGRATION,
        lambda params: [{"m": {**params, "created_at": None, "completed_at": None}}],
    )


class TestMigrate:
    @pytest.mark.asyncio
    async def test_relabels_and_rewrites_type_and_subtype(self, store, client):
        arm_migration(client)
        client.on_write("REMOVE e:", [{"migrated": 7}])

        job = await store.migrate(
            "o1",
            from_version_id="from-v",
            to_version_id="to-v",
            type_mappings=[("Client", "Customer")],
        )

        assert isinstance(job, MigrationJob)
        assert job.status == "completed"
        assert job.total == 7
        assert job.processed == 7
        assert job.errored == 0

        relabel = next(q for q in client.write_queries if "REMOVE e:" in q)
        assert "MATCH (e:Entity:`Client`)" in relabel
        assert "REMOVE e:`Client`" in relabel
        assert "SET e:`Customer`" in relabel
        assert "e.type = $type" in relabel  # PERSON -> ORGANIZATION
        assert client.write_params("REMOVE e:") == {
            "subtype": "COMPANY",
            "type": "ORGANIZATION",
        }

    @pytest.mark.asyncio
    async def test_type_is_left_alone_when_the_pole_type_is_unchanged(self, store, client):
        same_pole = make_document(
            name="v2",
            entity_types=[EntityTypeDef(label="Customer", pole_type="PERSON", subtype="ALIAS")],
            relationships=[],
        )
        arm_migration(client, new=same_pole)
        client.on_write("REMOVE e:", [{"migrated": 1}])

        await store.migrate(
            "o1",
            from_version_id="from-v",
            to_version_id="to-v",
            type_mappings=[{"from": "Client", "to": "Customer"}],
        )

        relabel = next(q for q in client.write_queries if "REMOVE e:" in q)
        assert "e.type = $type" not in relabel
        assert client.write_params("REMOVE e:") == {"subtype": "ALIAS"}

    @pytest.mark.asyncio
    async def test_dry_run_only_counts(self, store, client):
        arm_migration(client)
        client.on_read("RETURN count(e) AS matched", [{"matched": 12}])

        job = await store.migrate(
            "o1",
            from_version_id="from-v",
            to_version_id="to-v",
            type_mappings=[("Client", "Customer")],
            dry_run=True,
        )

        assert job.total == 12
        assert job.processed == 0
        assert job.status == "completed"
        assert not any("REMOVE e:" in q for q in client.write_queries)
        assert any("count(e) AS matched" in q for q in client.read_queries)
        assert job.spec is not None and job.spec["dry_run"] is True

    @pytest.mark.asyncio
    async def test_records_the_spec_on_the_job_node(self, store, client):
        arm_migration(client)
        client.on_write("REMOVE e:", [{"migrated": 0}])

        await store.migrate(
            "o1",
            from_version_id="from-v",
            to_version_id="to-v",
            type_mappings=[("Client", "Customer")],
            batch_size=500,
        )

        params = client.write_params(queries.CREATE_ONTOLOGY_MIGRATION)
        spec = json.loads(params["spec"])
        assert spec["from_version_id"] == "from-v"
        assert spec["to_version_id"] == "to-v"
        assert spec["type_mappings"] == [{"from": "Client", "to": "Customer"}]
        assert spec["batch_size"] == 500
        assert params["ontology_id"] == "o1"
        assert params["status"] == "completed"

    @pytest.mark.asyncio
    async def test_a_failing_step_is_recorded_not_raised(self, store, client):
        arm_migration(client)

        async def boom(query: str, parameters: dict[str, Any] | None = None) -> Rows:
            client.writes.append((query, parameters or {}))
            if "REMOVE e:" in query:
                raise RuntimeError("deadlock")
            return _resolve(client.write_rows, query, parameters or {})

        client.execute_write = boom  # type: ignore[method-assign]

        job = await store.migrate(
            "o1",
            from_version_id="from-v",
            to_version_id="to-v",
            type_mappings=[("Client", "Customer")],
        )

        assert job.status == "failed"
        assert job.errored == 1
        assert job.error_message is not None and "deadlock" in job.error_message

    @pytest.mark.asyncio
    async def test_a_target_label_outside_the_ontology_is_rejected(self, store, client):
        arm_migration(client)
        with pytest.raises(ValueError, match="not declared"):
            await store.migrate(
                "o1",
                from_version_id="from-v",
                to_version_id="to-v",
                type_mappings=[("Client", "Unknown")],
            )

    @pytest.mark.asyncio
    async def test_an_unsafe_label_is_rejected(self, store, client):
        arm_migration(client)
        with pytest.raises(ValueError, match="valid Neo4j identifier"):
            await store.migrate(
                "o1",
                from_version_id="from-v",
                to_version_id="to-v",
                type_mappings=[("Client`) DETACH DELETE (n", "Customer")],
            )

    @pytest.mark.asyncio
    async def test_empty_mappings_are_rejected(self, store):
        with pytest.raises(ValueError, match="at least one"):
            await store.migrate("o1", from_version_id="a", to_version_id="b", type_mappings=[])

    @pytest.mark.asyncio
    async def test_a_subtypeless_target_strips_the_stale_subtype_labels(self, store, client):
        """The regression: ``subtype = null`` left the old subtype label behind.

        A node written as ``:Entity:Person:Individual`` kept ``:Individual``
        and went on matching every subtype-scoped query while reporting no
        subtype. Cypher cannot remove a label without naming it, so the
        candidates are derived from the *source* version's declarations.
        """
        old = make_document(
            name="v1",
            entity_types=[
                EntityTypeDef(label="Person", pole_type="PERSON"),
                EntityTypeDef(label="Client", pole_type="PERSON", subtype="INDIVIDUAL"),
                EntityTypeDef(label="Persona", pole_type="PERSON", subtype="ALIAS"),
                # A different pole type's subtype must not be dragged in.
                EntityTypeDef(label="Vendor", pole_type="ORGANIZATION", subtype="COMPANY"),
            ],
            relationships=[],
        )
        new = make_document(
            name="v2",
            entity_types=[EntityTypeDef(label="Party", pole_type="PERSON")],
            relationships=[],
        )
        arm_migration(client, old=old, new=new)
        client.on_write("REMOVE e:", [{"migrated": 3}])

        await store.migrate(
            "o1",
            from_version_id="from-v",
            to_version_id="to-v",
            type_mappings=[("Person", "Party")],
        )

        relabel = next(q for q in client.write_queries if "REMOVE e:" in q)
        assert "REMOVE e:`Person`:`Alias`:`Individual`" in relabel
        assert "Company" not in relabel  # other pole type
        assert client.write_params("REMOVE e:") == {"subtype": None}

    @pytest.mark.asyncio
    async def test_a_subtyped_target_removes_only_the_source_label(self, store, client):
        arm_migration(client)
        client.on_write("REMOVE e:", [{"migrated": 1}])

        await store.migrate(
            "o1",
            from_version_id="from-v",
            to_version_id="to-v",
            type_mappings=[("Client", "Customer")],
        )

        relabel = next(q for q in client.write_queries if "REMOVE e:" in q)
        assert "REMOVE e:`Client`\n" in relabel

    @pytest.mark.asyncio
    async def test_the_label_being_added_is_never_removed(self, store, client):
        """``to_label`` can coincide with a source subtype label; keep it."""
        old = make_document(
            name="v1",
            entity_types=[
                EntityTypeDef(label="Person", pole_type="PERSON"),
                EntityTypeDef(label="Individual", pole_type="PERSON", subtype="INDIVIDUAL"),
            ],
            relationships=[],
        )
        new = make_document(
            name="v2",
            entity_types=[EntityTypeDef(label="Individual", pole_type="PERSON")],
            relationships=[],
        )
        arm_migration(client, old=old, new=new)
        client.on_write("REMOVE e:", [{"migrated": 1}])

        await store.migrate(
            "o1",
            from_version_id="from-v",
            to_version_id="to-v",
            type_mappings=[("Person", "Individual")],
        )

        relabel = next(q for q in client.write_queries if "REMOVE e:" in q)
        assert "REMOVE e:`Person`\n" in relabel
        assert "SET e:`Individual`" in relabel

    @pytest.mark.asyncio
    async def test_an_undeclared_source_label_is_refused_when_subtypes_exist(self, store, client):
        """Nothing tells us which subtype labels those nodes carry."""
        new = make_document(
            name="v2",
            entity_types=[EntityTypeDef(label="Party", pole_type="PERSON")],
            relationships=[],
        )
        arm_migration(client, new=new)

        with pytest.raises(ValueError, match="not declared by the source ontology version"):
            await store.migrate(
                "o1",
                from_version_id="from-v",
                to_version_id="to-v",
                type_mappings=[("Ghost", "Party")],
            )

    @pytest.mark.asyncio
    async def test_an_undeclared_source_label_is_fine_when_nothing_is_subtyped(self, store, client):
        old = make_document(
            name="v1",
            entity_types=[EntityTypeDef(label="Person", pole_type="PERSON")],
            relationships=[],
        )
        new = make_document(
            name="v2",
            entity_types=[EntityTypeDef(label="Party", pole_type="PERSON")],
            relationships=[],
        )
        arm_migration(client, old=old, new=new)
        client.on_write("REMOVE e:", [{"migrated": 2}])

        job = await store.migrate(
            "o1",
            from_version_id="from-v",
            to_version_id="to-v",
            type_mappings=[("Ghost", "Party")],
        )

        assert job.status == "completed"
        relabel = next(q for q in client.write_queries if "REMOVE e:" in q)
        assert "REMOVE e:`Ghost`\n" in relabel

    @pytest.mark.asyncio
    async def test_unknown_version_raises_not_found(self, store, client):
        client.on_read(queries.GET_ONTOLOGY_VERSION, [])
        with pytest.raises(NotFoundError):
            await store.migrate(
                "o1",
                from_version_id="from-v",
                to_version_id="to-v",
                type_mappings=[("Client", "Customer")],
            )


class TestGetMigration:
    @pytest.mark.asyncio
    async def test_reads_a_job_back(self, store, client):
        client.on_read(
            queries.GET_ONTOLOGY_MIGRATION,
            [
                {
                    "m": {
                        "id": "job-1",
                        "ontology_id": "o1",
                        "status": "completed",
                        "total": 3,
                        "processed": 3,
                        "errored": 0,
                        "spec": json.dumps({"dry_run": False}),
                        "error_message": None,
                        "created_at": "2026-01-01T00:00:00Z",
                        "completed_at": "2026-01-01T00:00:01Z",
                    }
                }
            ],
        )

        job = await store.get_migration("job-1")

        assert job.id == "job-1"
        assert job.spec == {"dry_run": False}
        assert job.completed_at == "2026-01-01T00:00:01Z"
        assert client.read_params(queries.GET_ONTOLOGY_MIGRATION) == {"id": "job-1"}

    @pytest.mark.asyncio
    async def test_unknown_job_raises_not_found(self, store, client):
        client.on_read(queries.GET_ONTOLOGY_MIGRATION, [])
        with pytest.raises(NotFoundError):
            await store.get_migration("nope")


# -----------------------------------------------------------------------------
# Protocol conformance
# -----------------------------------------------------------------------------


class TestProtocol:
    def test_satisfies_the_ontology_api_protocol(self, store):
        from neo4j_agent_memory.ontology.protocol import OntologyAPI

        assert isinstance(store, OntologyAPI)

    def test_poleo_is_a_sound_starting_point(self):
        # The fallback document callers reach for when nothing is bound.
        assert POLEO_ONTOLOGY.validate_structure() == []
