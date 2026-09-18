"""Integration tests for the bolt ontology store (``client.ontology``).

Exercises the full lifecycle against a real Neo4j: create → update →
activate → get_active, the single-active-version invariant, template
listing/cloning, revision diffing, cascade delete, and the synchronous
``migrate`` relabel (dry run and for real).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from neo4j_agent_memory.core.exceptions import NotFoundError, NotSupportedError
from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    PropertyDef,
    RelationshipDef,
)
from neo4j_agent_memory.ontology.store import TEMPLATE_ID_PREFIX


def rich_document(name: str) -> OntologyDocument:
    """A document that uses every v0.7 extension field worth round-tripping."""
    return OntologyDocument(
        domain=DomainInfo(
            id=name,
            name=name,
            description="A support desk ontology.",
            tagline="Tickets and the people in them",
            emoji="🎧",
        ),
        entity_types=[
            EntityTypeDef(
                label="Customer",
                pole_type="PERSON",
                subtype="INDIVIDUAL",
                description=(
                    "A person who reported a ticket. Not a support engineer, "
                    "and not the organisation they work for."
                ),
                color="#4CAF50",
                aliases={"Acme Corp": ["Acme", "ACME Corporation"]},
                threshold=0.35,
                resolution_threshold=0.92,
                review_threshold=0.8,
                properties=[
                    PropertyDef(
                        name="tier",
                        type="string",
                        required=True,
                        enum=["free", "pro", "enterprise"],
                        description="Support tier.",
                    )
                ],
            ),
            EntityTypeDef(
                label="Organization",
                pole_type="ORGANIZATION",
                description="A company that buys support.",
            ),
        ],
        relationships=[
            RelationshipDef(
                type="EMPLOYED_BY",
                source="Customer",
                target="Organization",
                description="A customer is paid to work for an organization.",
                threshold=0.35,
                unique_source=True,
                inverse="EMPLOYS",
            ),
            RelationshipDef(
                type="EMPLOYS",
                source="Organization",
                target="Customer",
                description="An organization employs a customer.",
                inverse="EMPLOYED_BY",
                acyclic=True,
            ),
        ],
        no_self_loops=False,
    )


def extended_document(name: str) -> OntologyDocument:
    """``rich_document`` plus one entity type and one relationship."""
    document = rich_document(name)
    return document.model_copy(
        update={
            "entity_types": [
                *document.entity_types,
                EntityTypeDef(
                    label="Incident",
                    pole_type="EVENT",
                    subtype="INCIDENT",
                    description="A reported support incident.",
                ),
            ],
            "relationships": [
                *document.relationships,
                RelationshipDef(
                    type="REPORTED",
                    source="Customer",
                    target="Incident",
                    description="A customer reported an incident.",
                ),
            ],
        }
    )


@pytest.mark.integration
class TestOntologyLifecycle:
    """create → update → activate → get_active."""

    @pytest.mark.asyncio
    async def test_create_update_activate_round_trip(self, clean_memory_client):
        ontology = clean_memory_client.ontology
        name = f"support-{uuid4().hex[:8]}"
        document = rich_document(name)

        v1 = await ontology.create(name, document, validation_mode="strict")
        assert v1.revision == 1
        assert v1.validation_mode == "strict"
        assert v1.schema_hash and len(v1.schema_hash) == 64

        v2 = await ontology.update(v1.ontology_id, extended_document(name))
        assert v2.revision == 2
        # The mode is inherited when not overridden.
        assert v2.validation_mode == "strict"
        assert v2.id != v1.id

        activated = await ontology.activate(v2.id)
        assert activated.id == v2.id

        active = await ontology.get_active()
        assert active.version_id == v2.id
        assert active.ontology_id == v1.ontology_id
        assert active.revision == 2
        assert active.validation_mode == "strict"

        # Every extension field survives the JSON round trip through Neo4j.
        assert active.document == extended_document(name)
        customer = active.document.entity_type("Customer")
        assert customer is not None
        assert customer.description is not None and "Not a support engineer" in (
            customer.description
        )
        assert customer.aliases == {"Acme Corp": ["Acme", "ACME Corporation"]}
        assert customer.resolution_threshold == 0.92
        assert customer.properties[0].enum == ["free", "pro", "enterprise"]
        employed_by = next(
            rel for rel in active.document.relationships if rel.type == "EMPLOYED_BY"
        )
        assert employed_by.unique_source is True
        assert employed_by.inverse == "EMPLOYS"
        assert employed_by.threshold == 0.35
        assert active.document.no_self_loops is False
        assert active.document.permits("Customer", "REPORTED", "Incident")

    @pytest.mark.asyncio
    async def test_get_returns_the_full_revision_history(self, clean_memory_client):
        ontology = clean_memory_client.ontology
        name = f"support-{uuid4().hex[:8]}"

        v1 = await ontology.create(name, rich_document(name))
        await ontology.update(v1.ontology_id, extended_document(name))

        stored = await ontology.get(v1.ontology_id)
        assert stored.record.name == name
        assert stored.record.is_system is False
        assert [v.revision for v in stored.versions] == [1, 2]
        assert stored.versions[0].document == rich_document(name)

    @pytest.mark.asyncio
    async def test_no_active_ontology_raises(self, clean_memory_client):
        with pytest.raises(NotSupportedError, match="No active ontology"):
            await clean_memory_client.ontology.get_active()

    @pytest.mark.asyncio
    async def test_only_one_version_is_ever_active(self, clean_memory_client):
        ontology = clean_memory_client.ontology
        name = f"support-{uuid4().hex[:8]}"

        v1 = await ontology.create(name, rich_document(name))
        v2 = await ontology.update(v1.ontology_id, extended_document(name))

        await ontology.activate(v1.id)
        await ontology.activate(v2.id)

        rows = await clean_memory_client._client.execute_read(
            "MATCH (v:OntologyVersion {is_active: true}) RETURN collect(v.id) AS ids"
        )
        assert rows[0]["ids"] == [v2.id]

        active = await ontology.get_active()
        assert active.revision == 2

    @pytest.mark.asyncio
    async def test_a_second_ontology_takes_the_active_flag_from_the_first(
        self, clean_memory_client
    ):
        ontology = clean_memory_client.ontology
        first_name = f"first-{uuid4().hex[:8]}"
        second_name = f"second-{uuid4().hex[:8]}"

        first = await ontology.create(first_name, rich_document(first_name))
        second = await ontology.create(second_name, rich_document(second_name))

        await ontology.activate(first.id)
        await ontology.activate(second.id)

        rows = await clean_memory_client._client.execute_read(
            "MATCH (v:OntologyVersion {is_active: true}) RETURN count(v) AS n"
        )
        assert rows[0]["n"] == 1
        assert (await ontology.get_active()).ontology_id == second.ontology_id

    @pytest.mark.asyncio
    async def test_invalid_document_is_rejected(self, clean_memory_client):
        name = f"broken-{uuid4().hex[:8]}"
        broken = OntologyDocument(
            domain=DomainInfo(id=name, name=name),
            entity_types=[EntityTypeDef(label="Customer", pole_type="PERSON")],
            relationships=[RelationshipDef(type="EMPLOYED_BY", source="Customer", target="Ghost")],
        )

        with pytest.raises(ValueError, match="structurally invalid"):
            await clean_memory_client.ontology.create(name, broken)

        rows = await clean_memory_client._client.execute_read(
            "MATCH (o:Ontology) RETURN count(o) AS n"
        )
        assert rows[0]["n"] == 0

    @pytest.mark.asyncio
    async def test_unknown_ids_raise_not_found(self, clean_memory_client):
        ontology = clean_memory_client.ontology
        with pytest.raises(NotFoundError):
            await ontology.get("no-such-ontology")
        with pytest.raises(NotFoundError):
            await ontology.activate("no-such-version")
        with pytest.raises(NotFoundError):
            await ontology.delete("no-such-ontology")
        with pytest.raises(NotFoundError):
            await ontology.get_migration("no-such-job")


@pytest.mark.integration
class TestOntologyListingAndTemplates:
    @pytest.mark.asyncio
    async def test_templates_precede_stored_ontologies(self, clean_memory_client):
        ontology = clean_memory_client.ontology
        name = f"support-{uuid4().hex[:8]}"
        created = await ontology.create(name, rich_document(name))
        await ontology.activate(created.id)

        summaries = await ontology.list()
        system = [s for s in summaries if s.is_system]
        stored = [s for s in summaries if not s.is_system]

        assert system, "built-in templates should be listed"
        assert stored, "stored ontologies should be listed"
        # All system rows come first.
        assert summaries[: len(system)] == system
        assert {s.name for s in system} >= {"poleo", "podcast", "news"}
        assert all(s.id.startswith(TEMPLATE_ID_PREFIX) for s in system)
        assert all(s.current_revision is None for s in system)

        row = next(s for s in stored if s.name == name)
        assert row.id == created.ontology_id
        assert row.current_revision == 1
        assert row.is_active is True

    @pytest.mark.asyncio
    async def test_a_template_can_be_read_without_being_stored(self, clean_memory_client):
        template = await clean_memory_client.ontology.get(f"{TEMPLATE_ID_PREFIX}podcast")
        assert template.record.is_system is True
        assert len(template.versions) == 1
        assert template.versions[0].document is not None

        rows = await clean_memory_client._client.execute_read(
            "MATCH (o:Ontology) RETURN count(o) AS n"
        )
        assert rows[0]["n"] == 0

    @pytest.mark.asyncio
    async def test_clone_podcast_creates_an_editable_revision_one(self, clean_memory_client):
        ontology = clean_memory_client.ontology

        clone = await ontology.clone("podcast")

        assert clone.revision == 1
        assert clone.document is not None
        assert clone.document.domain.name == "podcast"
        assert clone.document.labels()
        assert clone.document.relationship_types()

        stored = await ontology.get(clone.ontology_id)
        assert stored.record.name == "podcast"
        assert stored.record.is_system is False

        # Not activated by cloning.
        with pytest.raises(NotSupportedError):
            await ontology.get_active()

        # A second clone disambiguates the name.
        again = await ontology.clone("podcast")
        second = await ontology.get(again.ontology_id)
        assert second.record.name == "podcast-copy-1"

    @pytest.mark.asyncio
    async def test_templates_are_read_only(self, clean_memory_client):
        ontology = clean_memory_client.ontology
        template_id = f"{TEMPLATE_ID_PREFIX}podcast"

        with pytest.raises(ValueError, match="read-only"):
            await ontology.delete(template_id)
        with pytest.raises(ValueError, match="read-only"):
            await ontology.update(template_id, rich_document("x"))


@pytest.mark.integration
class TestOntologyDiffAndDelete:
    @pytest.mark.asyncio
    async def test_diff_between_two_revisions(self, clean_memory_client):
        ontology = clean_memory_client.ontology
        name = f"support-{uuid4().hex[:8]}"

        v1 = await ontology.create(name, rich_document(name))
        await ontology.update(v1.ontology_id, extended_document(name))

        diff = await ontology.diff(v1.ontology_id, 1, 2)

        assert diff.from_revision == 1
        assert diff.to_revision == 2
        assert [et["label"] for et in diff.entity_types["added"]] == ["Incident"]
        assert diff.entity_types["removed"] == []
        assert [rel["type"] for rel in diff.relationships["added"]] == ["REPORTED"]

        # The reverse diff reports the removal.
        reverse = await ontology.diff(v1.ontology_id, 2, 1)
        assert [et["label"] for et in reverse.entity_types["removed"]] == ["Incident"]

    @pytest.mark.asyncio
    async def test_delete_cascades_to_versions_and_migrations(self, clean_memory_client):
        raw = clean_memory_client._client
        ontology = clean_memory_client.ontology
        name = f"support-{uuid4().hex[:8]}"

        v1 = await ontology.create(name, rich_document(name))
        v2 = await ontology.update(v1.ontology_id, extended_document(name))
        await ontology.activate(v2.id)
        job = await ontology.migrate(
            v1.ontology_id,
            from_version_id=v1.id,
            to_version_id=v2.id,
            type_mappings=[("Customer", "Incident")],
            dry_run=True,
        )

        before = await raw.execute_read(
            """
            MATCH (o:Ontology) WITH count(o) AS ontologies
            MATCH (v:OntologyVersion) WITH ontologies, count(v) AS versions
            MATCH (m:OntologyMigration) RETURN ontologies, versions, count(m) AS migrations
            """
        )
        assert before[0] == {"ontologies": 1, "versions": 2, "migrations": 1}

        await ontology.delete(v1.ontology_id)

        after = await raw.execute_read(
            """
            OPTIONAL MATCH (o:Ontology) WITH count(o) AS ontologies
            OPTIONAL MATCH (v:OntologyVersion) WITH ontologies, count(v) AS versions
            OPTIONAL MATCH (m:OntologyMigration)
            RETURN ontologies, versions, count(m) AS migrations
            """
        )
        assert after[0] == {"ontologies": 0, "versions": 0, "migrations": 0}

        with pytest.raises(NotFoundError):
            await ontology.get_migration(job.id)


@pytest.mark.integration
class TestOntologyMigrate:
    """The synchronous relabel path."""

    async def _seed(self, client):
        """Create two revisions that rename ``Client`` to ``Customer``."""
        name = f"migrate-{uuid4().hex[:8]}"
        old = OntologyDocument(
            domain=DomainInfo(id=name, name=name),
            entity_types=[EntityTypeDef(label="Client", pole_type="PERSON", subtype="INDIVIDUAL")],
        )
        new = OntologyDocument(
            domain=DomainInfo(id=name, name=name),
            entity_types=[
                EntityTypeDef(label="Customer", pole_type="ORGANIZATION", subtype="COMPANY")
            ],
        )
        v1 = await client.ontology.create(name, old)
        v2 = await client.ontology.update(v1.ontology_id, new)
        return v1, v2

    async def _seed_entity(self, client) -> str:
        entity_id = f"test-{uuid4()}"
        await client._client.execute_write(
            """
            CREATE (e:Entity:Client {
                id: $id, name: 'Acme', type: 'PERSON', subtype: 'INDIVIDUAL'
            })
            """,
            {"id": entity_id},
        )
        return entity_id

    async def _labels_and_type(self, client, entity_id: str):
        rows = await client._client.execute_read(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels, e.type AS type, "
            "e.subtype AS subtype",
            {"id": entity_id},
        )
        return rows[0]

    @pytest.mark.asyncio
    async def test_dry_run_counts_without_changing_anything(self, clean_memory_client):
        v1, v2 = await self._seed(clean_memory_client)
        entity_id = await self._seed_entity(clean_memory_client)

        job = await clean_memory_client.ontology.migrate(
            v1.ontology_id,
            from_version_id=v1.id,
            to_version_id=v2.id,
            type_mappings=[("Client", "Customer")],
            dry_run=True,
        )

        assert job.status == "completed"
        assert job.total == 1
        assert job.processed == 0
        assert job.errored == 0

        row = await self._labels_and_type(clean_memory_client, entity_id)
        assert "Client" in row["labels"]
        assert "Customer" not in row["labels"]
        assert row["type"] == "PERSON"
        assert row["subtype"] == "INDIVIDUAL"

    @pytest.mark.asyncio
    async def test_relabels_entities_and_rewrites_type_and_subtype(self, clean_memory_client):
        v1, v2 = await self._seed(clean_memory_client)
        entity_id = await self._seed_entity(clean_memory_client)

        job = await clean_memory_client.ontology.migrate(
            v1.ontology_id,
            from_version_id=v1.id,
            to_version_id=v2.id,
            type_mappings=[("Client", "Customer")],
        )

        assert job.status == "completed"
        assert job.total == 1
        assert job.processed == 1
        assert job.errored == 0
        assert job.spec is not None
        assert job.spec["type_mappings"] == [{"from": "Client", "to": "Customer"}]

        row = await self._labels_and_type(clean_memory_client, entity_id)
        assert "Customer" in row["labels"]
        assert "Client" not in row["labels"]
        assert "Entity" in row["labels"]
        assert row["type"] == "ORGANIZATION"
        assert row["subtype"] == "COMPANY"

        # A second run is a no-op (nothing carries the old label any more).
        repeat = await clean_memory_client.ontology.migrate(
            v1.ontology_id,
            from_version_id=v1.id,
            to_version_id=v2.id,
            type_mappings=[("Client", "Customer")],
        )
        assert repeat.processed == 0

    @pytest.mark.asyncio
    async def test_a_subtypeless_target_strips_the_old_subtype_label(self, clean_memory_client):
        """The regression: ``subtype = null`` left the subtype *label* behind.

        ``:Entity:Person:Individual`` kept ``:Individual`` and went on
        matching every subtype-scoped query while reporting no subtype.
        """
        client = clean_memory_client
        name = f"migrate-{uuid4().hex[:8]}"
        old = OntologyDocument(
            domain=DomainInfo(id=name, name=name),
            entity_types=[
                EntityTypeDef(label="Person", pole_type="PERSON"),
                EntityTypeDef(label="Individual", pole_type="PERSON", subtype="INDIVIDUAL"),
            ],
        )
        new = OntologyDocument(
            domain=DomainInfo(id=name, name=name),
            entity_types=[EntityTypeDef(label="Party", pole_type="PERSON")],
        )
        v1 = await client.ontology.create(name, old)
        v2 = await client.ontology.update(v1.ontology_id, new)

        entity_id = f"test-{uuid4()}"
        await client._client.execute_write(
            """
            CREATE (e:Entity:Person:Individual {
                id: $id, name: 'Ada', type: 'PERSON', subtype: 'INDIVIDUAL'
            })
            """,
            {"id": entity_id},
        )

        job = await client.ontology.migrate(
            v1.ontology_id,
            from_version_id=v1.id,
            to_version_id=v2.id,
            type_mappings=[("Person", "Party")],
        )
        assert job.status == "completed"
        assert job.processed == 1

        row = await self._labels_and_type(client, entity_id)
        assert row["subtype"] is None
        assert "Party" in row["labels"]
        assert "Person" not in row["labels"]
        # The whole point: the stale subtype label is gone too.
        assert "Individual" not in row["labels"]

    @pytest.mark.asyncio
    async def test_the_job_is_persisted_and_readable(self, clean_memory_client):
        v1, v2 = await self._seed(clean_memory_client)
        await self._seed_entity(clean_memory_client)

        job = await clean_memory_client.ontology.migrate(
            v1.ontology_id,
            from_version_id=v1.id,
            to_version_id=v2.id,
            type_mappings=[{"from": "Client", "to": "Customer"}],
            batch_size=100,
        )

        reread = await clean_memory_client.ontology.get_migration(job.id)
        assert reread.id == job.id
        assert reread.ontology_id == v1.ontology_id
        assert reread.status == "completed"
        assert reread.processed == 1
        assert reread.created_at is not None
        assert reread.completed_at is not None
        assert reread.spec is not None
        assert reread.spec["batch_size"] == 100
        assert reread.spec["from_version_id"] == v1.id
