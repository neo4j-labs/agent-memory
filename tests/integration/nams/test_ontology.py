"""Integration tests for the NAMS ontology surface (P2).

Exercises the live ontology lifecycle against the staging deployment:
list → clone → update (new revision) → activate → get_active → delete,
with exact active-version and mode readback. No entity validation probe runs.

The read-only checks (``list`` and ``get_active`` metadata) run whenever the
NAMS suite runs. The lifecycle tests **mutate workspace-global
active-ontology state**, so they run only with
``RUN_ISOLATED_ONTOLOGY_TESTS=1`` against a workspace the run owns
exclusively. Their ``restore_active_ontology`` fixture snapshots the active
version before each test and rebinds it afterward. Created ontologies are
tracked and deleted on teardown.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.core.exceptions import NotFoundError
from neo4j_agent_memory.nams import OntologyDocument

pytestmark = pytest.mark.integration

# Applied to every test that changes the workspace's active ontology.
mutates_active_ontology = pytest.mark.skipif(
    os.environ.get("RUN_ISOLATED_ONTOLOGY_TESTS") != "1",
    reason="Set RUN_ISOLATED_ONTOLOGY_TESTS=1 only for an exclusively owned test workspace",
)

# A template unlikely to clash with other suites; its clone is "<name>-clone".
TEMPLATE = "conservation"
CLONE_NAME = f"{TEMPLATE}-clone"


@pytest.fixture
async def restore_active_ontology(nams_client: MemoryClient) -> AsyncIterator[dict]:
    """Restore and verify before deleting only IDs returned to this test."""
    onto = nams_client.ontology
    before = await onto.get_active()
    assert before.version_id and before.ontology_id and before.revision
    assert before.validation_mode in {"permissive", "strict"}
    owned = {"ontologies": set(), "versions": {}, "previous": before}
    try:
        yield owned
    finally:
        active = await onto.get_active()
        is_prior = (
            active.version_id == before.version_id
            and active.validation_mode == before.validation_mode
        )
        is_owned = (
            active.version_id in owned["versions"]
            and owned["versions"][active.version_id] == active.validation_mode
        )
        if not is_prior and not is_owned:
            raise AssertionError(
                f"Unexpected active binding; retained ontology IDs {owned['ontologies']}"
            )
        if not is_prior:
            await onto.activate(before.version_id)
        restored = await onto.get_active()
        assert restored == before, (
            f"Restoration differs; retained ontology IDs {owned['ontologies']}"
        )
        for ontology_id in owned["ontologies"]:
            assert await onto.get_active() == before
            await onto.delete(ontology_id)
            with pytest.raises(NotFoundError):
                await onto.get(ontology_id)


@pytest.fixture
async def fresh_clone(nams_client: MemoryClient, restore_active_ontology: dict):
    onto = nams_client.ontology
    existing = {item.id for item in await onto.list()}
    clone = await onto.clone(TEMPLATE)
    assert clone.ontology_id not in existing, "Returned ontology already existed; it is not owned"
    restore_active_ontology["ontologies"].add(clone.ontology_id)
    restore_active_ontology["versions"][clone.id] = clone.validation_mode
    return clone


@pytest.mark.asyncio
async def test_list_includes_system_templates(nams_client: MemoryClient) -> None:
    catalog = await nams_client.ontology.list()
    names = {o.name for o in catalog}
    assert TEMPLATE in names
    assert "nams-default" in names
    # System templates are flagged.
    assert any(o.is_system for o in catalog)


@pytest.mark.asyncio
async def test_get_active_reports_version_metadata(nams_client: MemoryClient) -> None:
    """Read-only: the active response carries the binding's version metadata.

    ``get_active()`` reads version_id, ontology_id, revision and
    validation_mode only from the ``version`` object in the
    ``GET /ontologies/active`` response. If the service stopped sending it,
    every caller would silently see None for all four.
    """
    onto = nams_client.ontology
    active = await onto.get_active()
    assert active.document is not None
    assert active.version_id, "GET /ontologies/active returned no version metadata"
    assert active.ontology_id
    assert isinstance(active.revision, int) and active.revision >= 1
    assert active.validation_mode in {"permissive", "strict"}

    # Cross-check against the catalog, the lookup get_active() used before it
    # read the response's version object.
    flagged = [summary.id for summary in await onto.list() if summary.is_active]
    assert flagged == [active.ontology_id]
    versions = {version.id: version for version in (await onto.get(active.ontology_id)).versions}
    assert active.version_id in versions
    assert versions[active.version_id].revision == active.revision
    assert versions[active.version_id].validation_mode == active.validation_mode


@mutates_active_ontology
@pytest.mark.asyncio
async def test_clone_returns_revision_1_version(fresh_clone) -> None:
    assert fresh_clone.revision == 1
    assert fresh_clone.ontology_id.startswith("ont_")
    assert fresh_clone.id.startswith("ov_")
    assert fresh_clone.document is not None
    assert len(fresh_clone.document.entity_types) > 0


@mutates_active_ontology
@pytest.mark.asyncio
async def test_update_creates_new_revision_preserving_schema(
    nams_client: MemoryClient, fresh_clone
) -> None:
    onto = nams_client.ontology
    original_types = len(fresh_clone.document.entity_types)
    v2 = await onto.update(fresh_clone.ontology_id, fresh_clone.document)
    assert v2.revision == 2
    # The schema is preserved across the revision (regression: the body must
    # be wrapped under "ontology", else the service stores an empty schema).
    assert len(v2.document.entity_types) == original_types


@mutates_active_ontology
@pytest.mark.asyncio
async def test_activate_and_get_active_surface_validation_mode(
    nams_client: MemoryClient, fresh_clone, restore_active_ontology: dict
) -> None:
    onto = nams_client.ontology
    strict = await onto.update(
        fresh_clone.ontology_id, fresh_clone.document, validation_mode="strict"
    )
    restore_active_ontology["versions"][strict.id] = strict.validation_mode
    assert await onto.get_active() == restore_active_ontology["previous"]
    await onto.activate(strict.id)

    active = await onto.get_active()
    assert active.document.domain.id == CLONE_NAME
    assert active.validation_mode == "strict"
    assert active.revision == strict.revision
    assert active.version_id == strict.id


@mutates_active_ontology
@pytest.mark.asyncio
async def test_create_from_document(
    nams_client: MemoryClient, restore_active_ontology: dict
) -> None:
    onto = nams_client.ontology
    name = f"itest-create-ontology-{uuid4().hex}"
    doc = OntologyDocument.model_validate(
        {
            "domain": {"id": name, "name": "Integration Create", "description": "synthetic"},
            "entity_types": [
                {
                    "label": "Widget",
                    "pole_type": "OBJECT",
                    "properties": [{"name": "sku", "type": "string", "unique": True}],
                }
            ],
            "relationships": [],
        }
    )
    v = await onto.create(name, doc)
    restore_active_ontology["ontologies"].add(v.ontology_id)
    restore_active_ontology["versions"][v.id] = v.validation_mode
    assert v.revision == 1
    assert v.document.entity_types[0].label == "Widget"
