"""Tests for nams/long_term.py — NamsLongTermMemory.

Endpoint shapes verified against the live NAMS OpenAPI spec.

NAMS provides entity and relationship endpoints (``POST
/v1/relationships`` and ``GET /v1/entities/by-name`` per ADR-0016).
Preferences and facts raise :class:`NotSupportedError`.
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
    """``GET /v1/entities/by-name`` — resolver-normalized lookup, ordered
    list with the best match first; the SDK returns first-or-None."""

    @respx.mock
    async def test_returns_first_match(self, long_term):
        route = respx.get("https://memory.test/v1/entities/by-name").respond(
            200,
            json={
                "entities": [
                    {**SAMPLE_ENTITY, "matchKind": "name"},
                    {
                        **SAMPLE_ENTITY,
                        "id": "00000000-0000-0000-0000-000000000002",
                        "matchKind": "alias",
                        "resolvedFrom": "Ali",
                    },
                ]
            },
        )
        result = await long_term.get_entity_by_name("alice")
        assert result is not None
        assert result.name == "Alice"
        assert str(result.id) == "00000000-0000-0000-0000-000000000001"
        assert result.type == "PERSON"  # uppercased for package consumers
        assert route.calls[0].request.url.params["name"] == "alice"

    @respx.mock
    async def test_returns_none_when_no_match(self, long_term):
        # 200 with an empty list — the endpoint never 404s on no-match.
        respx.get("https://memory.test/v1/entities/by-name").respond(200, json={"entities": []})
        result = await long_term.get_entity_by_name("Missing")
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
    """Preferences, facts, and get_facts_about have no NAMS endpoints —
    all raise NotSupportedError."""

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


class TestAddRelationship:
    """``POST /v1/relationships`` — ADR-0016 pipeline-mirroring semantics."""

    @respx.mock
    async def test_happy_path_sends_camel_body_and_returns_relationship(self, long_term):
        route = respx.post("https://memory.test/v1/relationships").respond(
            201,
            json={
                "id": "00000000-0000-0000-0000-00000000ab01",
                "sourceId": "00000000-0000-0000-0000-0000000000e1",
                "targetId": "00000000-0000-0000-0000-0000000000e2",
                "relationshipType": "WORKS_AT",
                "confidence": 0.9,
                "created": True,
            },
        )
        rel = await long_term.add_relationship(
            "00000000-0000-0000-0000-0000000000e1",
            "WORKS_AT",
            "00000000-0000-0000-0000-0000000000e2",
            confidence=0.9,
            properties={"since": "2023"},
        )
        body = json.loads(route.calls[0].request.content)
        assert body == {
            "sourceId": "00000000-0000-0000-0000-0000000000e1",
            "targetId": "00000000-0000-0000-0000-0000000000e2",
            "relationshipType": "WORKS_AT",
            "confidence": 0.9,
            "properties": {"since": "2023"},
        }
        assert isinstance(rel, Relationship)
        assert str(rel.id) == "00000000-0000-0000-0000-00000000ab01"
        assert str(rel.source_id) == "00000000-0000-0000-0000-0000000000e1"
        assert str(rel.target_id) == "00000000-0000-0000-0000-0000000000e2"
        assert rel.type == "WORKS_AT"
        assert rel.confidence == 0.9
        assert rel.attributes == {"since": "2023"}

    @respx.mock
    async def test_collapsed_type_passes_through(self, long_term):
        """An unknown type collapses to RELATED_TO server-side; the SDK
        reports the WRITTEN type, not the requested one."""
        respx.post("https://memory.test/v1/relationships").respond(
            200,
            json={
                "id": "00000000-0000-0000-0000-00000000ab02",
                "sourceId": "00000000-0000-0000-0000-0000000000e1",
                "targetId": "00000000-0000-0000-0000-0000000000e2",
                "relationshipType": "RELATED_TO",
                "predicate": "COLLABORATES_WITH",
                "confidence": 1.0,
                "created": False,
            },
        )
        rel = await long_term.add_relationship(
            "00000000-0000-0000-0000-0000000000e1",
            "COLLABORATES_WITH",
            "00000000-0000-0000-0000-0000000000e2",
        )
        assert rel.type == "RELATED_TO"
        assert rel.attributes == {}


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
    async def test_maps_inline_relationships_to_entities(self, long_term):
        respx.get("https://memory.test/v1/entities/00000000-0000-0000-0000-0000000000e1").respond(
            200,
            json={
                **SAMPLE_ENTITY,
                "relationships": [
                    {
                        "relType": "KNOWS",
                        "targetId": "00000000-0000-0000-0000-0000000000e2",
                        "targetName": "Bob",
                        "targetType": "person",
                    },
                    {
                        "relType": "WORKS_AT",
                        "targetId": "00000000-0000-0000-0000-0000000000e3",
                        "targetName": "Acme",
                        "targetType": "organization",
                    },
                ],
            },
        )
        related = await long_term.get_related_entities("00000000-0000-0000-0000-0000000000e1")
        assert isinstance(related, list)
        assert len(related) == 2
        assert all(isinstance(e, Entity) for e in related)
        assert related[0].name == "Bob"
        assert related[0].type == "PERSON"
        assert str(related[0].id) == "00000000-0000-0000-0000-0000000000e2"
        assert related[1].name == "Acme"

    @respx.mock
    async def test_filters_by_relationship_type(self, long_term):
        respx.get("https://memory.test/v1/entities/00000000-0000-0000-0000-0000000000e1").respond(
            200,
            json={
                **SAMPLE_ENTITY,
                "relationships": [
                    {
                        "relType": "KNOWS",
                        "targetId": "00000000-0000-0000-0000-0000000000e2",
                        "targetName": "Bob",
                        "targetType": "person",
                    },
                    {
                        "relType": "WORKS_AT",
                        "targetId": "00000000-0000-0000-0000-0000000000e3",
                        "targetName": "Acme",
                        "targetType": "organization",
                    },
                ],
            },
        )
        related = await long_term.get_related_entities(
            "00000000-0000-0000-0000-0000000000e1", relationship_type="WORKS_AT"
        )
        assert len(related) == 1
        assert related[0].name == "Acme"

    async def test_depth_beyond_one_raises(self, long_term):
        # No respx mock — the guard must trip before any HTTP call.
        with pytest.raises(NotSupportedError):
            await long_term.get_related_entities("00000000-0000-0000-0000-0000000000e1", depth=2)

    @respx.mock
    async def test_depth_one_is_fine(self, long_term):
        respx.get("https://memory.test/v1/entities/00000000-0000-0000-0000-0000000000e1").respond(
            200, json=SAMPLE_ENTITY
        )
        related = await long_term.get_related_entities(
            "00000000-0000-0000-0000-0000000000e1", depth=1
        )
        assert related == []


class TestMergeDuplicateEntities:
    """Two/three-call composition: merge → optional rename → fetch."""

    SRC = "00000000-0000-0000-0000-0000000000a1"
    TGT = "00000000-0000-0000-0000-0000000000a2"
    CANON = "00000000-0000-0000-0000-0000000000c1"

    @respx.mock
    async def test_merge_then_fetch_uses_canonical_target(self, long_term):
        merge = respx.post(f"https://memory.test/v1/entities/{self.SRC}/merge").respond(
            200, json={"status": "merged", "sourceId": self.SRC, "targetId": self.CANON}
        )
        # The follow-up GET must hit the canonical-resolved target from the
        # merge response, NOT the requested target id.
        get = respx.get(f"https://memory.test/v1/entities/{self.CANON}").respond(
            200, json={**SAMPLE_ENTITY, "id": self.CANON}
        )
        entity = await long_term.merge_duplicate_entities(self.SRC, self.TGT)
        body = json.loads(merge.calls[0].request.content)
        assert body == {"targetId": self.TGT}
        assert get.called
        assert isinstance(entity, Entity)
        assert str(entity.id) == self.CANON

    @respx.mock
    async def test_canonical_name_triggers_rename(self, long_term):
        respx.post(f"https://memory.test/v1/entities/{self.SRC}/merge").respond(
            200, json={"status": "merged", "sourceId": self.SRC, "targetId": self.CANON}
        )
        put = respx.put(f"https://memory.test/v1/entities/{self.CANON}").respond(
            200, json={"status": "updated"}
        )
        respx.get(f"https://memory.test/v1/entities/{self.CANON}").respond(
            200, json={**SAMPLE_ENTITY, "id": self.CANON, "name": "Alice Smith"}
        )
        entity = await long_term.merge_duplicate_entities(
            self.SRC, self.TGT, canonical_name="Alice Smith"
        )
        assert put.called
        assert json.loads(put.calls[0].request.content) == {"name": "Alice Smith"}
        assert entity.name == "Alice Smith"

    @respx.mock
    async def test_no_rename_without_canonical_name(self, long_term):
        respx.post(f"https://memory.test/v1/entities/{self.SRC}/merge").respond(
            200, json={"status": "merged", "sourceId": self.SRC, "targetId": self.CANON}
        )
        put = respx.put(f"https://memory.test/v1/entities/{self.CANON}")
        respx.get(f"https://memory.test/v1/entities/{self.CANON}").respond(
            200, json={**SAMPLE_ENTITY, "id": self.CANON}
        )
        await long_term.merge_duplicate_entities(self.SRC, self.TGT)
        assert not put.called


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
