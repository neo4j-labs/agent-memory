"""Tests for nams/long_term.py — NamsLongTermMemory.

Endpoint shapes verified against the live NAMS OpenAPI spec.

NAMS provides entity endpoints only. Preferences, facts, and
relationship writes raise :class:`NotSupportedError`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx
from pydantic import ValidationError as PydanticValidationError

from neo4j_agent_memory.core.exceptions import (
    AuthenticationError,
    NotSupportedError,
    RateLimitError,
    TransportError,
    ValidationError,
)
from neo4j_agent_memory.core.protocols import LongTermProtocol
from neo4j_agent_memory.memory.long_term import Entity, Relationship
from neo4j_agent_memory.nams import HttpTransport, NamsLongTermMemory, StaticApiKeyAuth
from neo4j_agent_memory.nams.long_term import _normalize_entity


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
    async def test_merged_resolution_tolerates_null_fields_on_canonical_entity(self, long_term):
        # NAMS projects unset node properties as JSON null — a manually
        # created entity has no confidence/sourceStage/updatedAt. Those
        # nulls must fall back to model defaults, not fail float parsing.
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
            200,
            json={
                "id": SAMPLE_ENTITY["id"],
                "name": "Alice",
                "type": "person",
                "description": None,
                "confidence": None,
                "sourceStage": None,
                "createdAt": "2026-05-17T12:00:00Z",
                "updatedAt": None,
                "relationships": [],
            },
        )
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        assert isinstance(entity, Entity)
        assert entity.name == "Alice"
        assert entity.confidence == 1.0  # model default
        assert entity.description is None

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
        assert entity.confidence == 1.0
        assert entity.metadata["nams_resolution"] == {
            "resolution": "merged",
            "merged_into": SAMPLE_ENTITY["id"],
            "merge_confidence": 0.93,
            "fallback": True,
        }

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


def _mock_merged_create(**overrides):
    return respx.post("https://memory.test/v1/entities").respond(
        200,
        json={
            "id": SAMPLE_ENTITY["id"],
            "resolution": "merged",
            "merged_into": SAMPLE_ENTITY["id"],
            "confidence": 0.93,
            **overrides,
        },
    )


class TestMergedEntityResolution:
    @pytest.mark.parametrize(
        ("status", "error_type"),
        [
            (400, ValidationError),
            (401, AuthenticationError),
            (403, AuthenticationError),
            (405, NotSupportedError),
            (429, RateLimitError),
            (500, TransportError),
            (501, NotSupportedError),
        ],
    )
    @respx.mock
    async def test_canonical_read_errors_propagate(self, long_term, status, error_type):
        _mock_merged_create()
        route = respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            status, json={"error": "canonical read failed"}, headers={"Retry-After": "0"}
        )
        with pytest.raises(error_type):
            await long_term.add_entity("Alice Smith", "PERSON")
        assert route.call_count == (3 if status in (429, 500) else 1)

    @pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
    @respx.mock
    async def test_canonical_network_errors_propagate(self, long_term, error_type):
        _mock_merged_create()
        route = respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").mock(
            side_effect=error_type("canonical read failed")
        )
        with pytest.raises(TransportError) as exc_info:
            await long_term.add_entity("Alice Smith", "PERSON")
        assert isinstance(exc_info.value.__cause__, error_type)
        assert route.call_count == 3

    @pytest.mark.parametrize("score", [0.93, 1.00001, -0.1, "unscaled", None])
    @pytest.mark.parametrize("fallback", [False, True])
    @respx.mock
    async def test_score_and_metadata_are_separate_from_entity_confidence(
        self, long_term, score, fallback
    ):
        _mock_merged_create(confidence=score)
        route = respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}")
        if fallback:
            route.respond(404)
        else:
            route.respond(200, json={**SAMPLE_ENTITY, "metadata": {"source": "canonical"}})
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        assert entity.confidence == (1.0 if fallback else SAMPLE_ENTITY["confidence"])
        expected_resolution = {
            "resolution": "merged",
            "merged_into": SAMPLE_ENTITY["id"],
            "fallback": fallback,
        }
        if score is not None:
            expected_resolution["merge_confidence"] = score
        assert entity.metadata["nams_resolution"] == expected_resolution
        if not fallback:
            assert entity.metadata["source"] == "canonical"

    @respx.mock
    async def test_absent_merge_score_stays_absent(self, long_term):
        respx.post("https://memory.test/v1/entities").respond(
            200, json={"id": SAMPLE_ENTITY["id"], "resolution": "merged"}
        )
        respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            200, json=SAMPLE_ENTITY
        )
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        assert "merge_confidence" not in entity.metadata["nams_resolution"]

    @pytest.mark.parametrize("target_key", ["merged_into", "mergedInto", "id"])
    @respx.mock
    async def test_merge_target_aliases(self, long_term, target_key):
        respx.post("https://memory.test/v1/entities").respond(
            200, json={target_key: SAMPLE_ENTITY["id"], "resolution": "merged"}
        )
        route = respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            200, json=SAMPLE_ENTITY
        )
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        assert route.call_count == 1
        assert str(entity.id) == SAMPLE_ENTITY["id"]

    @pytest.mark.parametrize("missing_target", [None, "", " \t "])
    @respx.mock
    async def test_empty_merge_target_uses_valid_id(self, long_term, missing_target):
        _mock_merged_create(merged_into=missing_target)
        route = respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            200, json=SAMPLE_ENTITY
        )
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        assert route.call_count == 1
        assert str(entity.id) == SAMPLE_ENTITY["id"]

    @pytest.mark.parametrize("fallback", [False, True])
    @respx.mock
    async def test_envelope_and_canonical_metadata_are_preserved(self, long_term, fallback):
        _mock_merged_create(metadata={"request": "create", "source": "envelope"})
        detail = {"metadata": {"source": "canonical"}}
        if not fallback:
            detail.update(SAMPLE_ENTITY)
        respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            200, json=detail
        )
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        assert entity.metadata["request"] == "create"
        assert entity.metadata["source"] == "canonical"
        assert entity.metadata["nams_resolution"]["fallback"] is fallback

    @pytest.mark.parametrize(
        "identity",
        [
            {},
            {"id": None},
            {"id": ""},
            {"id": "bad-id"},
            {"merged_into": []},
            {"merged_into": [], "id": SAMPLE_ENTITY["id"]},
            {"merged_into": False, "id": SAMPLE_ENTITY["id"]},
            {"merged_into": 0, "id": SAMPLE_ENTITY["id"]},
        ],
    )
    @respx.mock
    async def test_invalid_merge_identity_fails_before_read(self, long_term, identity):
        respx.post("https://memory.test/v1/entities").respond(
            200, json={"resolution": "merged", **identity}
        )
        with pytest.raises(PydanticValidationError):
            await long_term.add_entity("Alice Smith", "PERSON")
        assert len(respx.calls) == 1

    @pytest.mark.parametrize(
        "detail",
        [
            None,
            {},
            {"name": None},
            {"name": ""},
            {"name": " \t "},
            {"metadata": {"source": "partial"}},
        ],
    )
    @respx.mock
    async def test_incomplete_canonical_response_uses_marked_fallback(self, long_term, detail):
        _mock_merged_create()
        respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            200, json=detail
        )
        before = datetime.now(timezone.utc)
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        after = datetime.now(timezone.utc)
        assert str(entity.id) == SAMPLE_ENTITY["id"]
        assert entity.name == "Alice Smith"
        assert entity.type == "PERSON"
        assert entity.confidence == 1.0
        assert before <= entity.created_at <= after
        assert entity.created_at.utcoffset().total_seconds() == 0
        assert entity.metadata["nams_resolution"]["fallback"] is True
        if detail and detail.get("metadata"):
            assert entity.metadata["source"] == "partial"

    @pytest.mark.parametrize("status", [200, 204])
    @respx.mock
    async def test_empty_canonical_body_uses_marked_fallback(self, long_term, status):
        _mock_merged_create()
        respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(status)
        entity = await long_term.add_entity("Alice Smith", "PERSON")
        assert entity.name == "Alice Smith"
        assert str(entity.id) == SAMPLE_ENTITY["id"]
        assert entity.metadata["nams_resolution"]["fallback"] is True

    @pytest.mark.parametrize("entity_type", ["", " \t "])
    @respx.mock
    async def test_blank_canonical_type_is_invalid(self, long_term, entity_type):
        _mock_merged_create()
        respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            200, json={"type": entity_type}
        )
        with pytest.raises(ValidationError, match="type must not be blank"):
            await long_term.add_entity("Alice Smith", "PERSON")

    @pytest.mark.parametrize(
        "detail",
        [
            [],
            "not an entity",
            {"name": 17},
            {"id": None},
            {"id": "bad-id"},
            {"name": "Alice"},
            {"createdAt": None},
            {"createdAt": "bad-date"},
            {"confidence": 1.1},
            {"type": None},
            {"aliases": "Alice"},
            {"metadata": []},
        ],
    )
    @respx.mock
    async def test_malformed_canonical_fields_do_not_fall_back(self, long_term, detail):
        _mock_merged_create()
        respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            200, json=detail
        )
        with pytest.raises(PydanticValidationError):
            await long_term.add_entity("Alice Smith", "PERSON")

    @pytest.mark.parametrize("name", ["Alice", None])
    @respx.mock
    async def test_canonical_identity_must_match_merge_target(self, long_term, name):
        _mock_merged_create()
        respx.get(f"https://memory.test/v1/entities/{SAMPLE_ENTITY['id']}").respond(
            200, json={**SAMPLE_ENTITY, "name": name, "id": "00000000-0000-0000-0000-000000000002"}
        )
        with pytest.raises(ValidationError, match="does not match the merge target"):
            await long_term.add_entity("Alice Smith", "PERSON")


class TestNormalizeEntity:
    def test_known_null_fields_use_defaults(self):
        entity = Entity.model_validate(
            _normalize_entity(
                {
                    **SAMPLE_ENTITY,
                    "confidence": None,
                    "aliases": None,
                    "attributes": None,
                    "metadata": None,
                    "description": None,
                    "updatedAt": None,
                }
            )
        )
        assert entity.confidence == 1.0
        assert entity.aliases == []
        assert entity.attributes == {}
        assert entity.metadata == {}
        assert entity.description is None
        assert entity.updated_at is None
        assert entity.created_at == datetime(2026, 5, 17, 12, tzinfo=timezone.utc)

    @pytest.mark.parametrize(
        "changes", [{"id": None}, {"id": "bad-id"}, {"createdAt": None}, {"createdAt": "bad-date"}]
    )
    def test_invalid_identity_and_timestamps_are_not_defaulted(self, changes):
        with pytest.raises(PydanticValidationError):
            Entity.model_validate(_normalize_entity({**SAMPLE_ENTITY, **changes}))

    def test_missing_id_never_generates_a_uuid(self):
        payload = {key: value for key, value in SAMPLE_ENTITY.items() if key != "id"}
        with pytest.raises(PydanticValidationError):
            Entity.model_validate(_normalize_entity(payload))

    def test_missing_timestamp_keeps_existing_default_behavior(self):
        payload = {key: value for key, value in SAMPLE_ENTITY.items() if key != "createdAt"}
        before = datetime.now(timezone.utc)
        entity = Entity.model_validate(_normalize_entity(payload))
        assert before <= entity.created_at <= datetime.now(timezone.utc)


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
