"""Tests for nams/long_term.py — NamsLongTermMemory.

Endpoint shapes verified against the live NAMS OpenAPI spec.

NAMS provides entity endpoints only. Preferences, facts, and
relationship writes raise :class:`NotSupportedError`.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.core.protocols import LongTermProtocol
from neo4j_agent_memory.memory.long_term import Entity, Relationship
from neo4j_agent_memory.nams import HttpTransport, NamsLongTermMemory, StaticApiKeyAuth


@pytest.fixture
async def transport(nams_config):
    auth = StaticApiKeyAuth.from_config(nams_config)
    t = HttpTransport.from_config(nams_config, auth=auth)
    async with t:
        yield t


@pytest.fixture
def long_term(transport) -> NamsLongTermMemory:
    return NamsLongTermMemory(transport)


SAMPLE_ENTITY = {
    "id": "00000000-0000-0000-0000-000000000001",
    "name": "Alice",
    # NAMS returns lowercase type values from its restricted set
    # (person/organization/location/concept/tool/custom). NamsLongTermMemory
    # uppercases on the way back so package consumers see POLE+O-style types.
    "type": "person",
    "description": "Test entity",
    "confidence": 0.95,
    "sourceStage": "extraction",
    "createdAt": "2026-05-17T12:00:00Z",
    "updatedAt": "2026-05-17T12:00:00Z",
}


class TestWaitForExtraction:
    """A.4 — extraction-readiness helper over async entity search."""

    @respx.mock
    async def test_returns_true_when_expected_name_appears(self, long_term):
        respx.post("https://memory.test/v1/entities/search").respond(
            200, json={"entities": [SAMPLE_ENTITY], "searchType": "vector"}
        )
        ok = await long_term.wait_for_extraction(
            query="Alice", expected_names=["Alice"], timeout=2, interval=0.01
        )
        assert ok is True

    @respx.mock
    async def test_session_id_uses_extraction_status_endpoint(self, long_term):
        # Authoritative path: a conversation with no pending messages is done,
        # and no entity search is performed when only session_id is given.
        status = respx.get("https://memory.test/v1/conversations/conv-1/extraction-status").respond(
            200, json={"messages": [], "summary": {"completed": 3}}
        )
        search = respx.post("https://memory.test/v1/entities/search")
        ok = await long_term.wait_for_extraction(session_id="conv-1", timeout=2, interval=0.01)
        assert ok is True
        assert status.called
        assert not search.called  # no search needed — status is authoritative

    @respx.mock
    async def test_session_id_polls_until_no_pending(self, long_term):
        route = respx.get("https://memory.test/v1/conversations/conv-1/extraction-status")
        route.side_effect = [
            httpx.Response(200, json={"summary": {"pending": 2, "completed": 1}}),
            httpx.Response(200, json={"summary": {"completed": 3}}),
        ]
        ok = await long_term.wait_for_extraction(session_id="conv-1", timeout=2, interval=0.01)
        assert ok is True
        assert route.call_count == 2

    @respx.mock
    async def test_session_id_times_out_while_pending(self, long_term):
        respx.get("https://memory.test/v1/conversations/conv-1/extraction-status").respond(
            200, json={"summary": {"pending": 1}}
        )
        ok = await long_term.wait_for_extraction(session_id="conv-1", timeout=0.05, interval=0.01)
        assert ok is False

    @respx.mock
    async def test_session_id_then_entity_confirmation(self, long_term):
        # status completes, then expected_names is confirmed via search.
        respx.get("https://memory.test/v1/conversations/conv-1/extraction-status").respond(
            200, json={"summary": {"completed": 1}}
        )
        respx.post("https://memory.test/v1/entities/search").respond(
            200, json={"entities": [SAMPLE_ENTITY], "searchType": "vector"}
        )
        ok = await long_term.wait_for_extraction(
            session_id="conv-1", expected_names=["Alice"], timeout=2, interval=0.01
        )
        assert ok is True

    @respx.mock
    async def test_returns_false_on_timeout(self, long_term):
        respx.post("https://memory.test/v1/entities/search").respond(
            200, json={"entities": [SAMPLE_ENTITY], "searchType": "vector"}
        )
        ok = await long_term.wait_for_extraction(
            query="x", expected_names=["NeverAppears"], timeout=0.05, interval=0.01
        )
        assert ok is False

    @respx.mock
    async def test_predicate_path(self, long_term):
        respx.post("https://memory.test/v1/entities/search").respond(
            200, json={"entities": [SAMPLE_ENTITY], "searchType": "vector"}
        )
        ok = await long_term.wait_for_extraction(
            query="Alice",
            predicate=lambda ents: any(e.name == "Alice" for e in ents),
            timeout=2,
            interval=0.01,
        )
        assert ok is True

    async def test_requires_a_signal(self, long_term):
        with pytest.raises(ValueError, match="query.*expected_names.*predicate|requires"):
            await long_term.wait_for_extraction(timeout=0.01)


class TestProtocolConformance:
    def test_satisfies_long_term_protocol(self, long_term):
        assert isinstance(long_term, LongTermProtocol)


class TestAddEntity:
    @respx.mock
    async def test_basic_returns_entity_only(self, long_term):
        route = respx.post("https://memory.test/v1/entities").respond(201, json=SAMPLE_ENTITY)
        entity = await long_term.add_entity("Alice", "PERSON", description="Test entity")
        assert isinstance(entity, Entity)
        assert entity.name == "Alice"
        # Round-trip: package sends POLE+O "PERSON"; NAMS returns lowercase
        # "person"; NamsLongTermMemory uppercases it before parsing.
        assert entity.type == "PERSON"
        body = json.loads(route.calls[0].request.content)
        # Outbound type is mapped to NAMS' lowercase enum.
        assert body == {"name": "Alice", "type": "person", "description": "Test entity"}

    @respx.mock
    async def test_merged_resolution_fetches_canonical_entity(self, long_term):
        # NAMS resolves-before-create: a near-duplicate name merges onto the
        # existing entity and the response carries no name/type — the client
        # must follow up with GET /entities/{id} for the canonical record.
        respx.post("https://memory.test/v1/entities").respond(
            200,
            json={
                "id": SAMPLE_ENTITY["id"],
                "resolution": "merged",
                "merged_into": SAMPLE_ENTITY["id"],
                "confidence": 0.93,
            },
        )
        get_route = respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            200, json={**SAMPLE_ENTITY, "relationships": []}
        )
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        assert get_route.called
        assert isinstance(entity, Entity)
        assert str(entity.id) == SAMPLE_ENTITY["id"]
        # The canonical (merged-into) record wins over the requested name.
        assert entity.name == "Alice"
        assert entity.type == "PERSON"

    @respx.mock
    async def test_merged_resolution_falls_back_to_request_fields(self, long_term):
        # If the follow-up read fails, synthesize a parseable Entity from the
        # request instead of raising a ValidationError.
        respx.post("https://memory.test/v1/entities").respond(
            200,
            json={
                "id": SAMPLE_ENTITY["id"],
                "resolution": "merged",
                "merged_into": SAMPLE_ENTITY["id"],
                "confidence": 0.93,
            },
        )
        respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            404, json={"error": "entity not found"}
        )
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        assert isinstance(entity, Entity)
        assert str(entity.id) == SAMPLE_ENTITY["id"]
        assert entity.name == "Alice Smith"
        assert entity.type == "PERSON"
        assert entity.confidence == 0.93

    @respx.mock
    async def test_review_pending_resolution_parses(self, long_term):
        # review_pending responses include full entity fields plus the
        # resolution/duplicate_of extras — those must parse cleanly.
        respx.post("https://memory.test/v1/entities").respond(
            201,
            json={
                **SAMPLE_ENTITY,
                "resolution": "review_pending",
                "duplicate_of": "00000000-0000-0000-0000-000000000002",
            },
        )
        entity = await long_term.add_entity("Alice", "PERSON")
        assert isinstance(entity, Entity)
        assert entity.name == "Alice"

    @respx.mock
    async def test_bolt_only_kwargs_dropped(self, long_term):
        route = respx.post("https://memory.test/v1/entities").respond(201, json=SAMPLE_ENTITY)
        await long_term.add_entity(
            "Alice",
            "PERSON",
            subtype="INDIVIDUAL",
            aliases=["Al"],
            attributes={"role": "lead"},
            confidence=0.8,
            deduplicate=True,
            geocode=True,
        )
        body = json.loads(route.calls[0].request.content)
        # NAMS accepts only name/type/description.
        for k in ("subtype", "aliases", "attributes", "confidence", "deduplicate", "geocode"):
            assert k not in body


class TestSearchEntities:
    @respx.mock
    async def test_with_envelope(self, long_term):
        route = respx.post("https://memory.test/v1/entities/search").respond(
            200, json={"entities": [SAMPLE_ENTITY], "searchType": "vector"}
        )
        results = await long_term.search_entities("Alice", entity_type="PERSON", limit=5)
        assert len(results) == 1
        assert isinstance(results[0], Entity)
        body = json.loads(route.calls[0].request.content)
        # Filter type is mapped to NAMS' lowercase enum.
        assert body == {"query": "Alice", "type": "person", "limit": 5}


class TestGetEntityByName:
    @respx.mock
    async def test_uses_search_internally(self, long_term):
        respx.post("https://memory.test/v1/entities/search").respond(
            200, json={"entities": [SAMPLE_ENTITY], "searchType": "vector"}
        )
        result = await long_term.get_entity_by_name("Alice")
        assert result is not None
        assert result.name == "Alice"

    @respx.mock
    async def test_returns_none_when_no_match(self, long_term):
        respx.post("https://memory.test/v1/entities/search").respond(
            200, json={"entities": [], "searchType": "vector"}
        )
        result = await long_term.get_entity_by_name("Missing")
        assert result is None

    @respx.mock
    async def test_returns_none_when_only_inexact_matches(self, long_term):
        """Search returns hits but none with exact name match."""
        respx.post("https://memory.test/v1/entities/search").respond(
            200,
            json={
                "entities": [{**SAMPLE_ENTITY, "name": "Alice Different"}],
                "searchType": "vector",
            },
        )
        result = await long_term.get_entity_by_name("Alice")
        assert result is None


class TestSetEntityFeedback:
    @respx.mock
    async def test_positive_maps_to_user_score_and_confirmed(self, long_term):
        route = respx.put("https://memory.test/v1/entities/eid/feedback").respond(
            200, json={"id": "eid", "updated": True}
        )
        await long_term.set_entity_feedback("eid", "positive")
        body = json.loads(route.calls[0].request.content)
        assert body == {"userScore": 1.0, "confirmed": True}

    @respx.mock
    async def test_negative_maps_to_zero_and_false(self, long_term):
        route = respx.put("https://memory.test/v1/entities/eid/feedback").respond(
            200, json={"id": "eid", "updated": True}
        )
        await long_term.set_entity_feedback("eid", "negative")
        body = json.loads(route.calls[0].request.content)
        assert body == {"userScore": 0.0, "confirmed": False}

    @respx.mock
    async def test_explicit_user_score_kwarg(self, long_term):
        route = respx.put("https://memory.test/v1/entities/eid/feedback").respond(
            200, json={"id": "eid", "updated": True}
        )
        await long_term.set_entity_feedback("eid", "", user_score=0.75, confirmed=True)
        body = json.loads(route.calls[0].request.content)
        assert body == {"userScore": 0.75, "confirmed": True}


class TestGetEntityHistory:
    @respx.mock
    async def test_returns_mentions(self, long_term):
        respx.get("https://memory.test/v1/entities/eid/history").respond(
            200,
            json={
                "entityId": "eid",
                "mentions": [{"conversationId": "c1", "mentionCount": 3}],
            },
        )
        history = await long_term.get_entity_history("eid")
        assert len(history) == 1


class TestGetEntityProvenance:
    """Entity provenance lives under /v1/reasoning/provenance/{entityId}."""

    @respx.mock
    async def test_basic(self, long_term):
        respx.get("https://memory.test/v1/reasoning/provenance/eid").respond(
            200,
            json={"entityId": "eid", "steps": [{"id": "s1", "reasoning": "..."}]},
        )
        prov = await long_term.get_entity_provenance("eid")
        assert "steps" in prov
        assert len(prov["steps"]) == 1


class TestNotSupportedMethods:
    """Preferences, facts, relationships, related-entities, get_facts_about
    have no NAMS endpoints — all raise NotSupportedError."""

    async def test_add_preference(self, long_term):
        with pytest.raises(NotSupportedError):
            await long_term.add_preference("food", "italian")

    async def test_search_preferences(self, long_term):
        with pytest.raises(NotSupportedError):
            await long_term.search_preferences("food")

    async def test_get_preferences_for(self, long_term):
        with pytest.raises(NotSupportedError):
            await long_term.get_preferences_for(category="food")

    async def test_supersede_preference(self, long_term):
        with pytest.raises(NotSupportedError):
            await long_term.supersede_preference("pref-id")

    async def test_add_fact(self, long_term):
        with pytest.raises(NotSupportedError):
            await long_term.add_fact("Alice", "works_at", "Acme")

    async def test_search_facts(self, long_term):
        with pytest.raises(NotSupportedError):
            await long_term.search_facts("Acme")

    async def test_get_facts_about(self, long_term):
        with pytest.raises(NotSupportedError):
            await long_term.get_facts_about("Alice")

    async def test_add_relationship(self, long_term):
        with pytest.raises(NotSupportedError):
            await long_term.add_relationship(
                "00000000-0000-0000-0000-0000000000e1",
                "WORKS_AT",
                "00000000-0000-0000-0000-0000000000e2",
            )


class TestGetEntityRelationships:
    @respx.mock
    async def test_returns_inline_relationships(self, long_term):
        """NAMS GET /v1/entities/{id} returns relationships inline."""
        respx.get("https://memory.test/v1/entities/00000000-0000-0000-0000-0000000000e1").respond(
            200,
            json={
                **SAMPLE_ENTITY,
                "relationships": [
                    {
                        "relType": "WORKS_AT",
                        "targetId": "00000000-0000-0000-0000-0000000000e2",
                        "targetName": "Acme",
                        "targetType": "ORGANIZATION",
                    }
                ],
            },
        )
        rels = await long_term.get_entity_relationships("00000000-0000-0000-0000-0000000000e1")
        assert len(rels) == 1
        assert isinstance(rels[0], Relationship)
        assert rels[0].type == "WORKS_AT"
        assert str(rels[0].target_id) == "00000000-0000-0000-0000-0000000000e2"

    @respx.mock
    async def test_empty_when_no_relationships(self, long_term):
        respx.get("https://memory.test/v1/entities/00000000-0000-0000-0000-0000000000e1").respond(
            200, json=SAMPLE_ENTITY
        )
        rels = await long_term.get_entity_relationships("00000000-0000-0000-0000-0000000000e1")
        assert rels == []


class TestGetRelatedEntities:
    @respx.mock
    async def test_returns_relationships_as_dicts(self, long_term):
        respx.get("https://memory.test/v1/entities/00000000-0000-0000-0000-0000000000e1").respond(
            200,
            json={
                **SAMPLE_ENTITY,
                "relationships": [
                    {
                        "relType": "KNOWS",
                        "targetId": "00000000-0000-0000-0000-0000000000e2",
                        "targetName": "Bob",
                        "targetType": "PERSON",
                    },
                ],
            },
        )
        related = await long_term.get_related_entities("00000000-0000-0000-0000-0000000000e1")
        assert isinstance(related, list)
        assert len(related) == 1


class TestGetContext:
    async def test_returns_empty_string(self, long_term):
        # NAMS doesn't expose long-term context. Returns "".
        result = await long_term.get_context("anything")
        assert result == ""


class TestTypeMapping:
    """POLE+O uppercase types → NAMS' lowercase enum.

    NAMS accepts only: person, organization, location, concept, tool, custom.
    POLE+O OBJECT/EVENT have no first-class NAMS analog → fall back to custom.
    """

    @pytest.mark.parametrize(
        ("package_type", "nams_type"),
        [
            ("PERSON", "person"),
            ("ORGANIZATION", "organization"),
            ("LOCATION", "location"),
            ("OBJECT", "custom"),  # no NAMS analog
            ("EVENT", "custom"),  # no NAMS analog
            ("CONCEPT", "concept"),
            ("TOOL", "tool"),
            ("CUSTOM", "custom"),
            ("Person", "person"),  # case-insensitive
            ("PERSON:INDIVIDUAL", "person"),  # subtype stripped
            ("Whatever", "custom"),  # unknown → custom
        ],
    )
    @respx.mock
    async def test_add_entity_maps_type(self, long_term, package_type, nams_type):
        route = respx.post("https://memory.test/v1/entities").respond(201, json=SAMPLE_ENTITY)
        await long_term.add_entity("X", package_type)
        body = json.loads(route.calls[0].request.content)
        assert body["type"] == nams_type


class TestExpandGraph:
    @respx.mock
    async def test_expand_graph_sends_camel_body_and_returns_fragment(self, long_term):
        route = respx.post("https://memory.test/v1/graph/expand").respond(
            200,
            json={
                "nodes": [{"id": "n1", "labels": ["Entity"], "properties": {"name": "Alice"}}],
                "edges": [{"id": "e1", "source": "n1", "target": "n2", "type": "KNOWS"}],
            },
        )
        result = await long_term.expand_graph("n1", loaded_ids=["n0"])
        body = json.loads(route.calls.last.request.content)
        assert body == {"nodeId": "n1", "loadedIds": ["n0"]}
        assert result["nodes"][0]["id"] == "n1"
        assert result["edges"][0]["type"] == "KNOWS"

    @respx.mock
    async def test_expand_graph_defaults_loaded_ids(self, long_term):
        route = respx.post("https://memory.test/v1/graph/expand").respond(
            200, json={"nodes": [], "edges": []}
        )
        await long_term.expand_graph("n1")
        body = json.loads(route.calls.last.request.content)
        assert body == {"nodeId": "n1", "loadedIds": []}
