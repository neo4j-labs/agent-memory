"""Integration tests for ontology-driven runtime behaviour against Neo4j.

``tests/integration/test_ontology_store.py`` covers the store's own lifecycle;
this module covers what the *rest of the runtime* does with the ontology once
it is bound:

* a second client adopts the activated version and its validation mode,
* strict ``add_relationship`` rejects a pair the ontology forbids,
* message ingestion stores only the relations the ontology permits.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.core.exceptions import ValidationError
from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    RelationshipDef,
)


def support_document(name: str) -> OntologyDocument:
    """Customer -[REPORTED]-> Incident, Customer -[EMPLOYED_BY]-> Vendor."""
    return OntologyDocument(
        domain=DomainInfo(id=name, name=name, description="A support desk ontology."),
        entity_types=[
            EntityTypeDef(
                label="Customer",
                pole_type="PERSON",
                subtype="INDIVIDUAL",
                description="A person who reported a ticket.",
            ),
            EntityTypeDef(
                label="Vendor",
                pole_type="ORGANIZATION",
                subtype="COMPANY",
                description="A company that sells support.",
            ),
            EntityTypeDef(
                label="Incident",
                pole_type="EVENT",
                subtype="INCIDENT",
                description="A reported support incident.",
            ),
        ],
        relationships=[
            RelationshipDef(type="REPORTED", source="Customer", target="Incident"),
            RelationshipDef(type="EMPLOYED_BY", source="Customer", target="Vendor"),
        ],
    )


class StubExtractor:
    """Returns one permitted and one forbidden relation, deterministically."""

    name = "stub"

    def __init__(self, result: ExtractionResult) -> None:
        self._result = result

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        return self._result.model_copy(update={"source_text": text})


async def second_client(first: MemoryClient, *, extractor: object = None) -> MemoryClient:
    """Open a second client against the same database, sharing its settings."""
    client: MemoryClient = MemoryClient(
        first._settings,
        embedder=first._embedder,
        extractor=extractor,  # type: ignore[arg-type]
        resolver=first._resolver,
    )
    await client.connect()
    return client


@pytest.mark.integration
class TestActiveOntologyIsAdopted:
    """A client picks up whatever ontology the database has bound."""

    @pytest.mark.asyncio
    async def test_a_second_client_adopts_the_active_version(self, clean_memory_client):
        name = f"support-{uuid4().hex[:8]}"
        document = support_document(name)
        created = await clean_memory_client.ontology.create(
            name, document, validation_mode="strict"
        )
        await clean_memory_client.ontology.activate(created.id)

        client = await second_client(clean_memory_client)
        try:
            assert client.ontology_document is not None
            assert client.ontology_document.domain.name == name
            assert client.ontology_document.labels() == document.labels()
            assert client.ontology_document.patterns() == document.patterns()
            # The stored version's mode wins when no setting overrides it.
            assert client.validation_mode == "strict"
            # And the same document reaches both memory layers.
            assert client.short_term._ontology == client.ontology_document
            assert client.long_term._ontology == client.ontology_document
            assert client.long_term._validation_mode == "strict"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_nothing_bound_falls_back_to_poleo(self, clean_memory_client):
        from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY

        # clean_memory_client's own connect() already ran against an empty
        # database, so its resolved document is the built-in fallback.
        assert clean_memory_client.ontology_document is POLEO_ONTOLOGY
        assert clean_memory_client.validation_mode == "permissive"

    @pytest.mark.asyncio
    async def test_a_corrupt_active_document_does_not_break_connect(self, clean_memory_client):
        """The regression: one bad row made the database unopenable.

        ``use_active_ontology`` defaults to True, so an unparseable stored
        document failed ``connect()`` — and the ``client.ontology.delete()``
        call needed to recover was therefore unreachable. Resolution falls
        through to the built-in fallback and the recovery path stays open.
        """
        from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY

        name = f"support-{uuid4().hex[:8]}"
        created = await clean_memory_client.ontology.create(name, support_document(name))
        await clean_memory_client.ontology.activate(created.id)
        # Corrupt the stored body: a mapping pydantic rejects (no ``domain``),
        # which used to escape ``get_active()`` as a raw ValidationError.
        await clean_memory_client._client.execute_write(
            "MATCH (v:OntologyVersion {id: $id}) SET v.document = $document",
            {"id": created.id, "document": '{"entity_types": 7}'},
        )

        client = await second_client(clean_memory_client)
        try:
            assert client.ontology_document is POLEO_ONTOLOGY
            assert client.validation_mode == "permissive"
            # Recovery is reachable from the very client that fell back.
            await client.ontology.delete(created.ontology_id)
        finally:
            await client.close()

        recovered = await second_client(clean_memory_client)
        try:
            assert recovered.ontology_document is POLEO_ONTOLOGY
        finally:
            await recovered.close()

    @pytest.mark.asyncio
    async def test_a_permissive_stored_version_yields_permissive(self, clean_memory_client):
        name = f"support-{uuid4().hex[:8]}"
        created = await clean_memory_client.ontology.create(
            name, support_document(name), validation_mode="permissive"
        )
        await clean_memory_client.ontology.activate(created.id)

        client = await second_client(clean_memory_client)
        try:
            assert client.validation_mode == "permissive"
        finally:
            await client.close()


@pytest.mark.integration
class TestStrictRelationshipWrites:
    """``add_relationship`` enforces the ontology's endpoint typing."""

    @pytest.mark.asyncio
    async def test_permitted_pair_accepted_forbidden_pair_rejected(self, clean_memory_client):
        name = f"support-{uuid4().hex[:8]}"
        created = await clean_memory_client.ontology.create(
            name, support_document(name), validation_mode="strict"
        )
        await clean_memory_client.ontology.activate(created.id)

        client = await second_client(clean_memory_client)
        try:
            assert client.validation_mode == "strict"

            customer, _ = await client.long_term.add_entity(
                "Ada Lovelace", "PERSON", subtype="INDIVIDUAL", enrich=False
            )
            vendor, _ = await client.long_term.add_entity(
                "Acme Support", "ORGANIZATION", subtype="COMPANY", enrich=False
            )
            incident, _ = await client.long_term.add_entity(
                "Ticket 42", "EVENT", subtype="INCIDENT", enrich=False
            )

            # Permitted: Customer -[REPORTED]-> Incident.
            relationship = await client.long_term.add_relationship(customer, incident, "REPORTED")
            assert relationship.type == "REPORTED"

            # Forbidden pair: Vendor is not a legal source for REPORTED.
            with pytest.raises(ValidationError, match="does not permit"):
                await client.long_term.add_relationship(vendor, incident, "REPORTED")

            # Undeclared type.
            with pytest.raises(ValidationError, match="not declared"):
                await client.long_term.add_relationship(customer, vendor, "SELLS_TO")

            # Only the permitted edge reached the graph.
            rows = await client.query.cypher(
                "MATCH ()-[r:RELATED_TO]->() RETURN r.type AS type ORDER BY type"
            )
            assert [row["type"] for row in rows] == ["REPORTED"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_strict_add_entity_rejects_an_undeclared_type(self, clean_memory_client):
        name = f"support-{uuid4().hex[:8]}"
        created = await clean_memory_client.ontology.create(
            name, support_document(name), validation_mode="strict"
        )
        await clean_memory_client.ontology.activate(created.id)

        client = await second_client(clean_memory_client)
        try:
            with pytest.raises(ValidationError, match="not declared"):
                await client.long_term.add_entity("A laptop", "OBJECT", enrich=False)

            rows = await client.query.cypher("MATCH (e:Entity) RETURN count(e) AS n")
            assert rows[0]["n"] == 0
        finally:
            await client.close()


@pytest.mark.integration
class TestIngestionStoresOnlyPermittedRelations:
    """``add_message`` drops relations the ontology forbids."""

    @pytest.mark.asyncio
    async def test_only_the_permitted_relation_is_stored(self, clean_memory_client):
        name = f"support-{uuid4().hex[:8]}"
        created = await clean_memory_client.ontology.create(
            name, support_document(name), validation_mode="permissive"
        )
        await clean_memory_client.ontology.activate(created.id)

        stub = StubExtractor(
            ExtractionResult(
                entities=[
                    ExtractedEntity(
                        id="m1",
                        name="Ada Lovelace",
                        type="PERSON",
                        subtype="INDIVIDUAL",
                        confidence=0.95,
                    ),
                    ExtractedEntity(
                        id="m2",
                        name="Ticket 42",
                        type="EVENT",
                        subtype="INCIDENT",
                        confidence=0.9,
                    ),
                ],
                relations=[
                    # Permitted.
                    ExtractedRelation(
                        source="Ada Lovelace",
                        target="Ticket 42",
                        relation_type="REPORTED",
                        source_id="m1",
                        target_id="m2",
                        confidence=0.9,
                    ),
                    # Forbidden: EMPLOYED_BY does not target an Incident.
                    ExtractedRelation(
                        source="Ada Lovelace",
                        target="Ticket 42",
                        relation_type="EMPLOYED_BY",
                        source_id="m1",
                        target_id="m2",
                        confidence=0.9,
                    ),
                ],
            )
        )

        client = await second_client(clean_memory_client, extractor=stub)
        try:
            await client.short_term.add_message(
                f"session-{uuid4().hex[:8]}",
                "user",
                "Ada Lovelace reported Ticket 42.",
                extract_entities=True,
                extract_relations=True,
            )

            rows = await client.query.cypher(
                "MATCH ()-[r:RELATED_TO]->() RETURN r.type AS type ORDER BY type"
            )
            assert [row["type"] for row in rows] == ["REPORTED"]

            # Both entities were still stored (permissive mode, declared types).
            names = await client.query.cypher(
                "MATCH (e:Entity) RETURN e.name AS name ORDER BY name"
            )
            assert [row["name"] for row in names] == ["Ada Lovelace", "Ticket 42"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_strict_ingestion_drops_undeclared_entities(self, clean_memory_client):
        name = f"support-{uuid4().hex[:8]}"
        created = await clean_memory_client.ontology.create(
            name, support_document(name), validation_mode="strict"
        )
        await clean_memory_client.ontology.activate(created.id)

        stub = StubExtractor(
            ExtractionResult(
                entities=[
                    ExtractedEntity(
                        id="m1",
                        name="Ada Lovelace",
                        type="PERSON",
                        subtype="INDIVIDUAL",
                    ),
                    # OBJECT is not declared by this ontology.
                    ExtractedEntity(id="m2", name="A laptop", type="OBJECT"),
                ],
                relations=[
                    ExtractedRelation(
                        source="Ada Lovelace",
                        target="A laptop",
                        relation_type="REPORTED",
                        source_id="m1",
                        target_id="m2",
                    )
                ],
            )
        )

        client = await second_client(clean_memory_client, extractor=stub)
        try:
            await client.short_term.add_message(
                f"session-{uuid4().hex[:8]}",
                "user",
                "Ada Lovelace mentioned a laptop.",
                extract_entities=True,
                extract_relations=True,
            )

            names = await client.query.cypher(
                "MATCH (e:Entity) RETURN e.name AS name ORDER BY name"
            )
            assert [row["name"] for row in names] == ["Ada Lovelace"]

            rows = await client.query.cypher("MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS n")
            assert rows[0]["n"] == 0
        finally:
            await client.close()
