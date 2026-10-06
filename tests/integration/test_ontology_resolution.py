"""Integration tests for ontology-aware resolution on the ingestion path.

``tests/unit/test_ontology_resolver.py`` covers the scoring rules against a
fake client; this module runs the real thing against Neo4j and asserts what
ends up in the graph: how many nodes, which aliases, and which pairs land in
the review band.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from neo4j_agent_memory import MemoryClient
from neo4j_agent_memory.config.settings import ResolutionConfig
from neo4j_agent_memory.extraction.base import ExtractedEntity, ExtractionResult

# =============================================================================
# Helpers
# =============================================================================


class KeywordExtractor:
    """Deterministic extractor: emits a mention for each known surface form.

    Real extraction is not what these tests are about — which mentions reach
    the resolver is. Offsets and context windows are produced exactly as a
    real extractor would, so the resolver's context scoring is exercised.
    """

    name = "keyword"

    def __init__(self, surface_forms: dict[str, tuple[str, str | None]]) -> None:
        self._surface_forms = surface_forms

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        extract_preferences: bool = True,
    ) -> ExtractionResult:
        # Every occurrence of every known surface form, longest first, then
        # greedily dropping overlaps: a real extractor emits "Apple Bank",
        # not "Apple Bank" *and* the "Apple" inside it.
        spans: list[tuple[int, int, str]] = []
        for surface in self._surface_forms:
            start = text.find(surface)
            while start >= 0:
                spans.append((start, start + len(surface), surface))
                start = text.find(surface, start + 1)
        spans.sort(key=lambda span: (-(span[1] - span[0]), span[0]))

        taken: list[tuple[int, int]] = []
        entities: list[ExtractedEntity] = []
        for start, end, surface in spans:
            if any(start < other_end and end > other_start for other_start, other_end in taken):
                continue
            taken.append((start, end))
            entity_type, subtype = self._surface_forms[surface]
            entities.append(
                ExtractedEntity(
                    name=surface,
                    type=entity_type,
                    subtype=subtype,
                    start_pos=start,
                    end_pos=end,
                    confidence=0.9,
                    context=text[max(0, start - 90) : end + 90],
                    extractor=self.name,
                )
            )
        entities.sort(key=lambda entity: entity.start_pos or 0)
        return ExtractionResult(entities=entities, relations=[], source_text=text)


ACME_FORMS = {
    "ACME Corporation": ("ORGANIZATION", None),
    "Acme Corp": ("ORGANIZATION", None),
    "Acme": ("ORGANIZATION", None),
}


async def resolving_client(
    template: MemoryClient,
    *,
    extractor: Any,
    resolution: ResolutionConfig | None = None,
) -> MemoryClient:
    """Open a client whose COMPOSITE resolver is the ontology resolver.

    ``clean_memory_client`` injects ``MockResolver``, which deliberately does
    not resolve on ingest. These tests need the real resolver, so the resolver
    override is left off and built from settings instead.
    """
    settings = template._settings.model_copy(
        update={"resolution": resolution or ResolutionConfig()}
    )
    client: MemoryClient = MemoryClient(
        settings,
        embedder=template._embedder,
        extractor=extractor,
    )
    await client.connect()
    return client


async def _wait_for_vector_index(
    client: MemoryClient,
    vector: list[float],
    *,
    attempts: int = 40,
) -> bool:
    """Poll the entity vector index until it returns a row (it lags writes)."""
    from neo4j_agent_memory.graph import queries

    for _ in range(attempts):
        rows = await client._client.execute_read(
            queries.FIND_SIMILAR_ENTITIES_BY_EMBEDDING,
            {"embedding": vector, "limit": 5, "threshold": 0.0, "type": "PERSON"},
        )
        if rows:
            return True
        time.sleep(0.1)
    return False


async def organization_nodes(client: MemoryClient) -> list[dict[str, Any]]:
    """Every ORGANIZATION entity, with its aliases."""
    return await client.query.cypher(
        """
        MATCH (e:Entity {type: 'ORGANIZATION'})
        RETURN e.name AS name, coalesce(e.aliases, []) AS aliases
        ORDER BY name
        """
    )


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.integration
class TestVariantsCollapseOntoOneNode:
    """Three surface forms across two sessions, one entity."""

    @pytest.mark.asyncio
    async def test_aliases_accumulate_on_the_surviving_node(self, clean_memory_client):
        client = await resolving_client(clean_memory_client, extractor=KeywordExtractor(ACME_FORMS))
        try:
            from neo4j_agent_memory.resolution.ontology import OntologyResolver

            assert isinstance(client._resolver, OntologyResolver), (
                "COMPOSITE on bolt must build the ontology resolver"
            )
            assert client.short_term._resolution_config.resolve_on_ingest is True

            await client.short_term.add_message(
                "session-a", "user", "Acme Corp shipped the order yesterday"
            )
            await client.short_term.add_message("session-a", "user", "Acme delivered late again")
            await client.short_term.add_message(
                "session-b", "user", "ACME Corporation apologised for the delay"
            )

            nodes = await organization_nodes(client)
            assert len(nodes) == 1, f"expected one ORGANIZATION node, got {nodes}"
            assert nodes[0]["name"] == "Acme Corp"
            assert set(nodes[0]["aliases"]) == {"Acme", "ACME Corporation"}

            # Every message still points at that one node.
            mentions = await client.query.cypher(
                """
                MATCH (m:Message)-[:MENTIONS]->(e:Entity {type: 'ORGANIZATION'})
                RETURN count(DISTINCT m) AS messages, count(DISTINCT e) AS entities
                """
            )
            assert mentions[0]["messages"] == 3
            assert mentions[0]["entities"] == 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_variants_in_one_message_collapse(self, clean_memory_client):
        """The second pass: neither variant had a stored entity to anchor to."""
        client = await resolving_client(clean_memory_client, extractor=KeywordExtractor(ACME_FORMS))
        try:
            await client.short_term.add_message(
                "session-a", "user", "Acme Corp is late; Acme always is"
            )

            nodes = await organization_nodes(client)
            assert len(nodes) == 1
            assert nodes[0]["name"] == "Acme Corp"
            assert nodes[0]["aliases"] == ["Acme"]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_resolve_on_ingest_false_reproduces_the_pre_pr5_node_count(
        self, clean_memory_client
    ):
        client = await resolving_client(
            clean_memory_client,
            extractor=KeywordExtractor(ACME_FORMS),
            resolution=ResolutionConfig(resolve_on_ingest=False),
        )
        try:
            await client.short_term.add_message(
                "session-a", "user", "Acme Corp shipped the order yesterday"
            )
            await client.short_term.add_message("session-a", "user", "Acme delivered late again")
            await client.short_term.add_message(
                "session-b", "user", "ACME Corporation apologised for the delay"
            )

            nodes = await organization_nodes(client)
            assert [node["name"] for node in nodes] == [
                "ACME Corporation",
                "Acme",
                "Acme Corp",
            ]
            assert all(node["aliases"] == [] for node in nodes)
        finally:
            await client.close()


@pytest.mark.integration
class TestReviewBand:
    """A borderline pair is stored, not merged, and is reviewable."""

    @pytest.mark.asyncio
    async def test_bare_prefix_yields_a_pending_same_as(self, clean_memory_client):
        extractor = KeywordExtractor(
            {
                "Apple Bank": ("ORGANIZATION", None),
                "Apple": ("ORGANIZATION", None),
            }
        )
        client = await resolving_client(clean_memory_client, extractor=extractor)
        try:
            await client.short_term.add_message(
                "session-a", "user", "Apple Bank approved the mortgage application"
            )
            await client.short_term.add_message(
                "session-b", "user", "Apple announced a new phone at the keynote"
            )

            nodes = await organization_nodes(client)
            assert [node["name"] for node in nodes] == ["Apple", "Apple Bank"], (
                "a prefix alone is not an identity"
            )

            edges = await client.query.cypher(
                """
                MATCH ()-[r:SAME_AS]->()
                RETURN r.status AS status, r.match_type AS match_type,
                       r.confidence AS confidence
                """
            )
            assert len(edges) == 1
            assert edges[0]["status"] == "pending"
            assert edges[0]["match_type"] == "prefix"
            assert 0.85 <= edges[0]["confidence"] < 0.90

            # ``find_potential_duplicates`` matches the SAME_AS edge directed,
            # so one flagged pair surfaces as exactly one row, with the
            # confidence read off the stored edge (not a bare-relationship-
            # parsing bug's hardcoded 0.0).
            pending = await client.long_term.find_potential_duplicates(limit=10)
            assert len(pending) == 1
            left, right, confidence = pending[0]
            assert {left.name, right.name} == {"Apple", "Apple Bank"}
            assert confidence > 0
            assert confidence == edges[0]["confidence"]
        finally:
            await client.close()


@pytest.mark.integration
class TestUserScopedResolution:
    """``scope="user"`` blocks through the tenant's own conversations."""

    @pytest.mark.asyncio
    async def test_a_variant_does_not_cross_tenants(self, clean_memory_client):
        """One tenant's "Ada" must not resolve onto another tenant's "Ada Lovelace".

        ``:Entity`` nodes are global and MERGE on ``(name, type)``, so two
        tenants mentioning the *same* surface form always share a node. What
        user scoping changes is whether a *variant* resolves across the tenant
        boundary.
        """
        extractor = KeywordExtractor(
            {
                "Dr. Ada Lovelace": ("PERSON", None),
                "Ada Lovelace": ("PERSON", None),
            }
        )
        alice_says = "Dr. Ada Lovelace wrote the first program"
        bob_says = "Ada Lovelace joined the standup"

        scoped = await resolving_client(
            clean_memory_client,
            extractor=extractor,
            resolution=ResolutionConfig(scope="user"),
        )
        try:
            await scoped.users.upsert_user(identifier="alice")
            await scoped.users.upsert_user(identifier="bob")
            await scoped.short_term.add_message(
                "alice-1", "user", alice_says, user_identifier="alice"
            )
            await scoped.short_term.add_message("bob-1", "user", bob_says, user_identifier="bob")

            people = await scoped.query.cypher(
                "MATCH (e:Entity {type: 'PERSON'}) RETURN e.name AS name ORDER BY name"
            )
            assert [row["name"] for row in people] == ["Ada Lovelace", "Dr. Ada Lovelace"], (
                "user-scoped blocking must not reach another tenant's entities"
            )
        finally:
            await scoped.close()

        # The same two messages under global scope collapse onto one node.
        await clean_memory_client._client.execute_write("MATCH (n) DETACH DELETE n")
        globally = await resolving_client(clean_memory_client, extractor=extractor)
        try:
            await globally.short_term.add_message(
                "alice-1", "user", alice_says, user_identifier="alice"
            )
            await globally.short_term.add_message("bob-1", "user", bob_says, user_identifier="bob")

            people = await globally.query.cypher(
                """
                MATCH (e:Entity {type: 'PERSON'})
                RETURN e.name AS name, coalesce(e.aliases, []) AS aliases
                ORDER BY name
                """
            )
            assert [row["name"] for row in people] == ["Dr. Ada Lovelace"]
            assert people[0]["aliases"] == ["Ada Lovelace"]
        finally:
            await globally.close()

    @pytest.mark.asyncio
    async def test_the_vector_bucket_does_not_cross_tenants(self, clean_memory_client):
        """The entity vector index is global; only the _FOR_USER query scopes it.

        The pair here shares no normalized key, no head token and no tail
        token, so *only* the vector bucket can propose it -- and once proposed
        its token-sorted fuzzy similarity is 1.0, which merges. Tenant B's
        "Chen Alice" must not land on tenant A's "Alice Chen".
        """
        from neo4j_agent_memory.resolution.ontology import OntologyResolver

        extractor = KeywordExtractor({"Alice Chen": ("PERSON", None)})
        client = await resolving_client(clean_memory_client, extractor=extractor)
        try:
            await client.users.upsert_user(identifier="alice")
            await client.short_term.add_message(
                "alice-1", "user", "Alice Chen signed the contract", user_identifier="alice"
            )

            # Ingestion stores entity nodes without a vector, so put one on by
            # hand -- this test is about which query reads the index, not about
            # who writes the embedding.
            vector = await client._embedder.embed("Alice Chen")
            await client._client.execute_write(
                "MATCH (e:Entity {name: 'Alice Chen'}) SET e.embedding = $vector",
                {"vector": vector},
            )
            assert await _wait_for_vector_index(client, vector), (
                "the entity vector index never picked the node up"
            )

            scoped = OntologyResolver(
                client._client,
                embedder=client._embedder,
                config=ResolutionConfig(scope="user"),
            )
            scoped_result = await scoped.resolve_one("Chen Alice", "PERSON", user_identifier="bob")
            assert scoped_result.action != "merged", (
                "tenant B must not merge onto tenant A's entity"
            )

            # Same pair, global scope: the merge the unscoped query allowed.
            unscoped = OntologyResolver(
                client._client,
                embedder=client._embedder,
                config=ResolutionConfig(scope="global"),
            )
            unscoped_result = await unscoped.resolve_one("Chen Alice", "PERSON")
            assert unscoped_result.action == "merged"
            assert unscoped_result.matched_entity_name == "Alice Chen"
        finally:
            await client.close()


@pytest.mark.integration
class TestAddEntityThroughTheAdapter:
    """``add_entity`` bands through the same resolver as ingestion."""

    @pytest.mark.asyncio
    async def test_auto_merge_returns_the_existing_entity(self, clean_memory_client):
        client = await resolving_client(clean_memory_client, extractor=KeywordExtractor(ACME_FORMS))
        try:
            first, first_dedup = await client.long_term.add_entity(
                "Acme Corp", "ORGANIZATION", enrich=False
            )
            assert first_dedup.action == "none"

            second, second_dedup = await client.long_term.add_entity(
                "ACME Corporation", "ORGANIZATION", enrich=False
            )

            assert second_dedup.action == "merged"
            assert second_dedup.matched_entity_id == first.id
            assert second.id == first.id
            assert "ACME Corporation" in second.aliases

            nodes = await organization_nodes(client)
            assert len(nodes) == 1
        finally:
            await client.close()


@pytest.mark.integration
class TestStoredLookupKeys:
    """Blocking reads the indexed ``surface_keys`` / ``name_key`` properties."""

    @pytest.mark.asyncio
    async def test_every_write_path_maintains_the_keys(self, clean_memory_client):
        client = await resolving_client(clean_memory_client, extractor=KeywordExtractor(ACME_FORMS))
        try:
            await client.short_term.add_message("s", "user", "Acme Corp shipped; Acme is late")
            rows = await client.query.cypher(
                "MATCH (e:Entity {type: 'ORGANIZATION'}) "
                "RETURN e.name_key AS name_key, e.surface_keys AS surface_keys"
            )
            assert rows == [{"name_key": "acme corp", "surface_keys": "|acme corp|acme corp|acme|"}]
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_the_blocking_queries_use_the_indexes(self, clean_memory_client):
        """No hints (one errors while its index populates): the planner picks the
        indexes itself once it has statistics for a populated graph.
        """
        import asyncio

        from neo4j_agent_memory.graph import queries

        graph = clean_memory_client._client
        await graph.execute_write(
            "UNWIND range(1, 200) AS i "
            "CREATE (:Entity {id: 'seed-' + i, name: 'Seed ' + i, name_key: 'seed ' + i, "
            "surface_keys: '|seed ' + i + '|', "
            "type: ['PERSON', 'ORGANIZATION', 'LOCATION', 'EVENT', 'OBJECT'][i % 5]})"
        )
        params = {
            "keys": ["acme"],
            "type": "ORGANIZATION",
            "limit": 5,
            "head": "acme",
            "tail": "corp",
        }

        async def plan(query: str) -> str:
            async with graph.session() as session:
                result = await session.run("EXPLAIN " + query, params)
                return str((await result.consume()).plan)

        for query, index in (
            (queries.FIND_ENTITIES_BY_NORMALIZED_KEYS, "surface_keys"),
            (queries.FIND_ENTITIES_BY_TOKEN_PREFIX, "name_key"),
        ):
            # Index samples refresh in the background; nudge them, then replan.
            for _ in range(50):
                await graph.execute_write("CALL db.resampleOutdatedIndexes()")
                await graph.execute_write("CALL db.clearQueryCaches()")
                text = await plan(query)
                if f"e:Entity({index})" in text:
                    break
                await asyncio.sleep(0.2)
            assert f"e:Entity({index})" in text, text
            assert "NodeByLabelScan" not in text, text

    @pytest.mark.asyncio
    async def test_the_backfill_makes_legacy_entities_resolvable(self, clean_memory_client):
        """An entity written without keys (pre-0.7, or raw Cypher) is invisible until backfilled."""
        await clean_memory_client._client.execute_write(
            "CREATE (:Entity:Organization {id: 'legacy-acme', name: 'Acme Corp', "
            "type: 'ORGANIZATION', aliases: ['ACME Inc']})"
        )
        await clean_memory_client._schema_manager._backfill_entity_keys(force=True)

        client = await resolving_client(
            clean_memory_client, extractor=KeywordExtractor({"ACME Inc": ("ORGANIZATION", None)})
        )
        try:
            await client.short_term.add_message("s", "user", "ACME Inc called again")
            nodes = await organization_nodes(client)
            assert [node["name"] for node in nodes] == ["Acme Corp"], nodes
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_add_entity_dedups_without_an_embedding(self, clean_memory_client):
        client = await resolving_client(clean_memory_client, extractor=KeywordExtractor(ACME_FORMS))
        try:
            first, _ = await client.long_term.add_entity(
                "Acme Corp", "ORGANIZATION", enrich=False, generate_embedding=False
            )
            second, dedup = await client.long_term.add_entity(
                "Acme Corp.", "ORGANIZATION", enrich=False, generate_embedding=False
            )
            assert dedup.action == "merged"
            assert second.id == first.id
            assert len(await organization_nodes(client)) == 1
        finally:
            await client.close()


@pytest.mark.integration
class TestLookupKeysEdgeCases:
    @pytest.mark.asyncio
    async def test_a_non_string_name_does_not_break_adoption(self, clean_memory_client):
        """``toLower`` raises on a number; adopted names can be numbers."""
        await clean_memory_client._client.execute_write("CREATE (:Account {number: 4210})")

        report = await clean_memory_client.schema.adopt_existing_graph(
            label_to_type={"Account": "ACCOUNT"},
            name_property_per_label={"Account": "number"},
        )

        assert [label.migrated_count for label in report.by_label] == [1]
        rows = await clean_memory_client.query.cypher(
            "MATCH (e:Entity:Account) RETURN e.name_key AS name_key, e.surface_keys AS keys"
        )
        assert rows == [{"name_key": "4210", "keys": "|4210|"}]

    @pytest.mark.asyncio
    async def test_every_entity_ref_path_lands_on_one_node(self, clean_memory_client, session_id):
        """Explicit mentions and TOUCHED edges store the type as given, so they agree."""
        from neo4j_agent_memory.schema.models import EntityRef

        client = clean_memory_client
        ref = EntityRef(name="F-150", type="Vehicle")
        await client.short_term.add_message(
            session_id,
            "user",
            "The F-150 is in the shop.",
            extraction_mode="explicit",
            explicit_mentions=[ref],
        )
        trace = await client.reasoning.start_trace(session_id, "Check the truck")
        step = await client.reasoning.add_step(trace.id, thought="look it up")
        await client.reasoning.record_tool_call(
            step.id, "lookup", {"q": "F-150"}, touched_entities=[ref]
        )

        rows = await client.query.cypher(
            "MATCH (e:Entity {name: 'F-150'}) RETURN e.type AS type, e.subtype AS subtype"
        )
        assert rows == [{"type": "Vehicle", "subtype": None}]


@pytest.mark.integration
class TestMergeIsIdempotent:
    @pytest.mark.asyncio
    async def test_a_repeated_merge_does_not_recount_support(self, clean_memory_client):
        """The regression: each re-run added the source edge's support again."""
        long_term = clean_memory_client.long_term
        source, _ = await long_term.add_entity("Acme Corp.", "ORGANIZATION", deduplicate=False)
        target, _ = await long_term.add_entity(
            "Acme Corporation", "ORGANIZATION", deduplicate=False
        )
        other, _ = await long_term.add_entity("Ada", "PERSON", deduplicate=False)
        await long_term.add_relationship(other, source, "EMPLOYED_BY")
        await long_term.add_relationship(other, target, "EMPLOYED_BY")

        for _ in range(3):
            await long_term.merge_duplicate_entities(source.id, target.id)

        rows = await clean_memory_client.query.cypher(
            "MATCH (:Entity {id: $other})-[r:RELATED_TO {type: 'EMPLOYED_BY'}]->"
            "(:Entity {id: $target}) RETURN r.support AS support",
            {"other": str(other.id), "target": str(target.id)},
        )
        assert [row["support"] for row in rows] == [2]


@pytest.mark.integration
class TestResolutionOverhead:
    """Measure, don't guess: how much does resolution add per message?"""

    @pytest.mark.asyncio
    async def test_per_message_overhead_is_reported(self, clean_memory_client):
        messages = [f"Acme Corp shipped order {index} and Acme confirmed it" for index in range(10)]

        async def timed(resolution: ResolutionConfig) -> float:
            await clean_memory_client._client.execute_write("MATCH (n) DETACH DELETE n")
            client = await resolving_client(
                clean_memory_client,
                extractor=KeywordExtractor(ACME_FORMS),
                resolution=resolution,
            )
            try:
                started = time.perf_counter()
                for index, content in enumerate(messages):
                    await client.short_term.add_message(f"perf-{index}", "user", content)
                return (time.perf_counter() - started) / len(messages) * 1000
            finally:
                await client.close()

        with_resolution = await timed(ResolutionConfig())
        without_resolution = await timed(ResolutionConfig(resolve_on_ingest=False))

        print(
            f"\nper-message: resolution on {with_resolution:.1f} ms, "
            f"off {without_resolution:.1f} ms, "
            f"overhead {with_resolution - without_resolution:+.1f} ms"
        )
        # No latency assertion — machine-dependent. The contract asserted here
        # is only that resolution does not multiply the round-trip count.
        assert with_resolution < without_resolution * 6
