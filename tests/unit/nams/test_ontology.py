"""Unit tests for nams/ontology.py — NamsOntology accessor + models.

Endpoint shapes include the active response’s authoritative version record.
"""

from __future__ import annotations

import json

import pytest
import respx
from pydantic import SecretStr

from neo4j_agent_memory.config.settings import NamsConfig
from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.nams import (
    ActiveOntology,
    HttpTransport,
    MigrationJob,
    NamsOntology,
    Ontology,
    OntologyDiff,
    OntologyDocument,
    OntologyImportResult,
    OntologySummary,
    OntologyVersion,
    StaticApiKeyAuth,
)
from neo4j_agent_memory.nams._unsupported import _NamsUnsupported

BASE = "https://memory.test/v1"

DOC = {
    "domain": {"id": "legal-clone", "name": "Legal (clone)", "tagline": "t", "emoji": "⚖️"},
    "entity_types": [
        {
            "label": "Case",
            "pole_type": "EVENT",
            "subtype": "CASE",
            "color": "#fff",
            "icon": "gavel",
            "properties": [
                {"name": "docket", "type": "string", "unique": True, "required": True},
                {"name": "status", "type": "string", "enum": ["open", "closed"]},
            ],
        }
    ],
    "relationships": [{"type": "FILED_BY", "source": "Case", "target": "Person"}],
}

SUMMARY = {
    "id": "ont_1",
    "name": "legal-clone",
    "display_name": "Legal (clone)",
    "description": "d",
    "emoji": "⚖️",
    "tagline": "t",
    "is_system": False,
    "current_revision": 2,
    "is_active": True,
}


def _version(revision=1, mode="permissive", doc=DOC):
    return {
        "id": f"ov_{revision}",
        "ontology_id": "ont_1",
        "revision": revision,
        "validation_mode": mode,
        "schema_json": json.dumps(doc),
        "schema_hash": "abc",
        "created_at": "2026-05-29T00:00:00Z",
        "message": "msg",
    }


@pytest.fixture
async def transport(nams_config):
    auth = StaticApiKeyAuth.from_config(nams_config)
    async with HttpTransport.from_config(nams_config, auth=auth) as t:
        yield t


@pytest.fixture
def ontology(transport) -> NamsOntology:
    return NamsOntology(transport)


class TestList:
    @respx.mock
    async def test_list_parses_summaries(self, ontology):
        respx.get(f"{BASE}/ontologies").respond(200, json={"ontologies": [SUMMARY]})
        result = await ontology.list()
        assert len(result) == 1
        assert isinstance(result[0], OntologySummary)
        assert result[0].name == "legal-clone"
        assert result[0].current_revision == 2
        assert result[0].is_active is True


class TestGet:
    @respx.mock
    async def test_get_parses_record_and_versions(self, ontology):
        respx.get(f"{BASE}/ontologies/ont_1").respond(
            200, json={"record": {"id": "ont_1", "name": "legal-clone"}, "versions": [_version(1)]}
        )
        result = await ontology.get("ont_1")
        assert isinstance(result, Ontology)
        assert result.record.id == "ont_1"
        assert len(result.versions) == 1
        # schema_json double-encoding is parsed into a typed document
        assert result.versions[0].document.entity_types[0].label == "Case"
        assert result.versions[0].document.entity_types[0].properties[0].unique is True


class TestGetActive:
    async def test_get_active_uses_bound_version_not_latest(self, ontology, respx_mock):
        active_route = respx_mock.get(f"{BASE}/ontologies/active").respond(
            200, json={"ontology": DOC, "version": _version(1, "permissive")}
        )
        summaries = respx_mock.get(f"{BASE}/ontologies").respond(
            200, json={"ontologies": [SUMMARY]}
        )
        detail = respx_mock.get(f"{BASE}/ontologies/ont_1").respond(
            200,
            json={
                "record": {"id": "ont_1", "name": "legal-clone"},
                "versions": [_version(1, "permissive"), _version(2, "strict")],
            },
        )
        active = await ontology.get_active()
        assert isinstance(active, ActiveOntology)
        assert active.document.domain.id == "legal-clone"
        assert active.validation_mode == "permissive"
        assert active.revision == 1
        assert active.version_id == "ov_1"
        assert active.ontology_id == "ont_1"
        assert active.schema_hash == "abc"
        assert active_route.call_count == 1
        assert not summaries.called and not detail.called

    @pytest.mark.parametrize(
        "payload", [{"ontology": DOC}, {"ontology": DOC, "version": None}, DOC]
    )
    @respx.mock
    async def test_legacy_document_has_no_inferred_binding(self, ontology, payload):
        route = respx.get(f"{BASE}/ontologies/active").respond(200, json=payload)
        active = await ontology.get_active()
        assert active.document.domain.id == "legal-clone"
        assert active.version_id is None
        assert active.ontology_id is None
        assert active.revision is None
        assert active.validation_mode is None
        assert active.schema_hash is None
        assert route.call_count == 1
        assert len(respx.calls) == 1

    @pytest.mark.parametrize("schema_json", [None, "omitted"])
    @respx.mock
    async def test_active_version_schema_is_optional(self, ontology, schema_json):
        version = _version()
        if schema_json == "omitted":
            del version["schema_json"]
        else:
            version["schema_json"] = schema_json
        respx.get(f"{BASE}/ontologies/active").respond(
            200, json={"ontology": DOC, "version": version, "server_extension": 1}
        )
        assert (await ontology.get_active()).version_id == "ov_1"

    @pytest.mark.parametrize(
        "version",
        [
            {},
            [],
            "ov_1",
            0,
            {**_version(), "id": ""},
            {**_version(), "ontology_id": " "},
            {k: v for k, v in _version().items() if k != "revision"},
            {**_version(), "revision": "1"},
            {**_version(), "revision": True},
            {**_version(), "revision": 1.5},
            {**_version(), "revision": 0},
            {**_version(), "validation_mode": "unexpected"},
            {**_version(), "schema_json": "not json"},
            {**_version(), "schema_json": "null"},
            {**_version(), "schema_json": "{}"},
            {**_version(), "schema_hash": 7},
        ],
    )
    @respx.mock
    async def test_rejects_invalid_supplied_binding(self, ontology, version):
        respx.get(f"{BASE}/ontologies/active").respond(
            200, json={"ontology": DOC, "version": version}
        )
        with pytest.raises(ValueError):
            await ontology.get_active()
        assert len(respx.calls) == 1

    @respx.mock
    async def test_rejects_conflicting_schema_documents(self, ontology):
        other = {**DOC, "domain": {"id": "other", "name": "Other"}}
        respx.get(f"{BASE}/ontologies/active").respond(
            200, json={"ontology": DOC, "version": _version(doc=other)}
        )
        with pytest.raises(ValueError, match="conflicts"):
            await ontology.get_active()

    @respx.mock
    async def test_normalizes_schema_defaults_and_ignores_server_extensions(self, ontology):
        minimal = {"domain": {"id": "schema-domain", "name": "Schema"}}
        expanded = {
            "relationships": None,
            "entity_types": [],
            "domain": {"name": "Schema", "id": "schema-domain", "new_field": True},
            "new_field": "ignored",
        }
        respx.get(f"{BASE}/ontologies/active").respond(
            200,
            json={"ontology": minimal, "version": {**_version(doc=expanded), "new_field": 1}},
        )
        active = await ontology.get_active()
        assert active.ontology_id == "ont_1"  # Opaque service ID differs from domain ID.
        assert active.document.entity_types == []
        assert active.document.relationships == []


class TestWriteOps:
    @respx.mock
    async def test_clone_returns_version(self, ontology):
        respx.post(f"{BASE}/ontologies/legal/clone").respond(201, json=_version(1))
        v = await ontology.clone("legal")
        assert isinstance(v, OntologyVersion)
        assert v.ontology_id == "ont_1"
        assert v.document.domain.id == "legal-clone"

    @respx.mock
    async def test_create_wraps_ontology_key(self, ontology):
        route = respx.post(f"{BASE}/ontologies").respond(201, json=_version(1))
        doc = OntologyDocument.model_validate(DOC)
        await ontology.create("legal-clone", doc, validation_mode="strict")
        body = json.loads(route.calls.last.request.content)
        assert "ontology" in body
        assert body["ontology"]["domain"]["id"] == "legal-clone"
        assert body["validation_mode"] == "strict"

    @respx.mock
    async def test_update_wraps_ontology_key_and_bumps_revision(self, ontology):
        route = respx.put(f"{BASE}/ontologies/ont_1").respond(200, json=_version(2, "strict"))
        v = await ontology.update("ont_1", OntologyDocument.model_validate(DOC))
        body = json.loads(route.calls.last.request.content)
        assert "ontology" in body  # update uses the same wrapper as create
        assert v.revision == 2

    @respx.mock
    async def test_activate_posts_version_id(self, ontology):
        route = respx.post(f"{BASE}/ontologies/active").respond(200, json=_version(2, "strict"))
        await ontology.activate("ov_2")
        body = json.loads(route.calls.last.request.content)
        assert body == {"version_id": "ov_2"}

    @respx.mock
    async def test_delete(self, ontology):
        route = respx.delete(f"{BASE}/ontologies/ont_1").respond(204)
        await ontology.delete("ont_1")
        assert route.called


class TestImportDiffMigrate:
    @respx.mock
    async def test_import_sends_body_and_parses_draft(self, ontology):
        route = respx.post(f"{BASE}/ontologies/import").respond(
            200,
            json={
                "ontology": DOC,
                "warnings": [{"code": "unmapped", "message": "no pole_type", "path": "Widget"}],
                "detected_format": "arrows",
                "suggested_name": "Legal",
            },
        )
        result = await ontology.import_(content='{"nodes":[]}', format="arrows")
        body = json.loads(route.calls.last.request.content)
        assert body == {"format": "arrows", "content": '{"nodes":[]}'}
        assert isinstance(result, OntologyImportResult)
        assert result.document.entity_types[0].label == "Case"
        assert result.detected_format == "arrows"
        assert result.suggested_name == "Legal"
        assert result.warnings[0].code == "unmapped"

    async def test_import_requires_content_or_url(self, ontology):
        with pytest.raises(ValueError, match="content= or url="):
            await ontology.import_()

    @respx.mock
    async def test_diff_passes_query_params(self, ontology):
        route = respx.get(f"{BASE}/ontologies/ont_1/diff").respond(
            200,
            json={
                "from_revision": 1,
                "to_revision": 2,
                "entity_types": {
                    "added": [{"label": "Widget"}],
                    "removed": [],
                    "renamed": [],
                    "modified": [],
                },
                "relationships": {"added": [], "removed": [], "renamed": [], "modified": []},
            },
        )
        diff = await ontology.diff("ont_1", 1, 2)
        assert dict(route.calls.last.request.url.params) == {"from": "1", "to": "2"}
        assert isinstance(diff, OntologyDiff)
        assert diff.from_revision == 1
        assert diff.to_revision == 2
        assert diff.entity_types["added"][0]["label"] == "Widget"

    @respx.mock
    async def test_migrate_sends_spec_and_parses_job(self, ontology):
        route = respx.post(f"{BASE}/ontologies/ont_1/migrate").respond(
            202,
            json={"id": "mig_1", "ontology_id": "ont_1", "status": "pending", "total": 0},
        )
        job = await ontology.migrate(
            "ont_1",
            from_version_id="ov_1",
            to_version_id="ov_2",
            type_mappings=[("Widget", "Gadget")],
            dry_run=True,
        )
        body = json.loads(route.calls.last.request.content)
        assert body["spec"] == {
            "from_version_id": "ov_1",
            "to_version_id": "ov_2",
            "type_mappings": [{"from": "Widget", "to": "Gadget"}],
            "dry_run": True,
        }
        assert isinstance(job, MigrationJob)
        assert job.id == "mig_1"
        assert job.ontology_id == "ont_1"
        assert job.status == "pending"

    @respx.mock
    async def test_get_migration(self, ontology):
        respx.get(f"{BASE}/ontologies/migrations/mig_1").respond(
            200, json={"id": "mig_1", "status": "running", "processed": 5, "total": 10}
        )
        job = await ontology.get_migration("mig_1")
        assert job.processed == 5
        assert job.total == 10


class TestModelResilience:
    def test_null_collections_coerced(self):
        doc = OntologyDocument.model_validate(
            {"domain": {"id": "x", "name": "X"}, "entity_types": None, "relationships": None}
        )
        assert doc.entity_types == []
        assert doc.relationships == []

    def test_extra_fields_ignored(self):
        s = OntologySummary.model_validate({**SUMMARY, "unknown_future_field": 123})
        assert s.name == "legal-clone"


class TestBackendGuards:
    @respx.mock
    async def test_bridge_transport_raises_not_supported(self):
        config = NamsConfig(
            endpoint="https://memory.test",
            api_key=SecretStr("k"),  # bridge shape (no /vN)
            transport_mode="bridge",
            validate_on_connect=False,
        )
        async with HttpTransport.from_config(
            config, auth=StaticApiKeyAuth.from_config(config)
        ) as t:
            with pytest.raises(NotSupportedError, match="REST"):
                await NamsOntology(t).list()

    async def test_bolt_sentinel_raises(self):
        # On bolt, client.ontology is a sentinel that raises on method call.
        sentinel = _NamsUnsupported(
            accessor="ontology",
            message="NAMS capability.",
            workaround="SchemaModel.CUSTOM",
        )
        with pytest.raises(NotSupportedError, match="ontology"):
            await sentinel.list()
