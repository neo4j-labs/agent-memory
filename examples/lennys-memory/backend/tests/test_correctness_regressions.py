"""Regression tests for the bugs found in the 2026-09 examples review.

Every test here covers a path that used to fail on every call and was hidden by
a blanket ``except Exception`` or by a bare ``MagicMock`` in the old fixtures:

* ``get_user_preferences`` read a nonexistent ``Preference.source`` field.
* ``find_locations_near`` called ``client.search_locations_near`` (the method
  lives on ``client.long_term``).
* ``find_duplicate_entities`` passed ``entity_type=`` to a keyword-only method.
* ``get_conversation_context`` iterated a ``Conversation`` model.
* ``get_conversation_history`` asked Cypher for the OLDEST 20 messages.
* ``/api/locations/nearby`` read coordinates from the metadata blob.
* ``DELETE /api/preferences/{id}`` returned success without deleting anything.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.agent.tools import (
    PREFERENCE_CATEGORIES,
    find_duplicate_entities,
    find_locations_near,
    get_conversation_context,
    get_user_preferences,
)
from src.api.routes import memory as memory_routes
from src.api.routes import threads as thread_routes
from src.api.routes.chat import extract_touched_entities, get_conversation_history

from neo4j_agent_memory.memory.long_term import Entity, Preference
from neo4j_agent_memory.memory.short_term import Conversation, Message, MessageRole


def _preference(category: str = "format") -> Preference:
    return Preference(
        category=category,
        preference="prefers concise bullet summaries",
        context="I prefer concise bullet summaries",
        confidence=0.8,
        metadata={"source": "chat"},
    )


class TestGetUserPreferences:
    async def test_returns_stored_preferences(self, mock_agent_context):
        client = mock_agent_context.deps.client
        client.long_term.get_preferences_by_category.side_effect = (
            lambda category, **_: [_preference(category)] if category == "format" else []
        )

        results = await get_user_preferences(mock_agent_context)

        assert results, "expected at least one preference"
        assert all("error" not in row for row in results)
        assert results[0]["category"] == "format"
        assert results[0]["source"] == "chat"

    async def test_reads_every_category_the_app_writes(self, mock_agent_context):
        client = mock_agent_context.deps.client
        client.long_term.get_preferences_by_category.return_value = []

        await get_user_preferences(mock_agent_context)

        asked = {
            call.args[0] for call in client.long_term.get_preferences_by_category.call_args_list
        }
        assert asked == set(PREFERENCE_CATEGORIES)


class TestFindLocationsNear:
    async def test_returns_nearby_locations(self, mock_agent_context):
        client = mock_agent_context.deps.client
        client.get_locations.return_value = [
            {"name": "San Francisco", "latitude": 37.77, "longitude": -122.42}
        ]
        oakland = Entity(name="Oakland", type="LOCATION", subtype="CITY")
        oakland.metadata["distance_km"] = 13.4
        client.long_term.search_locations_near.return_value = [oakland]
        client.long_term.get_location_coordinates.return_value = (37.80, -122.27)

        results = await find_locations_near(mock_agent_context, "San Francisco")

        assert results == [
            {
                "name": "Oakland",
                "type": "LOCATION:CITY",
                "latitude": 37.80,
                "longitude": -122.27,
                "distance_km": 13.4,
            }
        ]


class TestFindDuplicateEntities:
    async def test_accepts_library_tuple_shape(self, mock_agent_context):
        client = mock_agent_context.deps.client
        left = Entity(name="Brian Chesky", type="PERSON")
        right = Entity(name="Chesky", type="PERSON")
        client.long_term.find_potential_duplicates.return_value = [(left, right, 0.88)]

        results = await find_duplicate_entities(mock_agent_context, limit=5)

        assert all("error" not in row for row in results)
        assert results[0]["entity1"]["name"] == "Brian Chesky"
        assert results[0]["similarity"] == 0.88
        # Keyword-only signature: a positional/unexpected kwarg would raise.
        client.long_term.find_potential_duplicates.assert_awaited_once_with(limit=5)


class TestGetConversationContext:
    async def test_returns_the_most_recent_turns(self, mock_agent_context):
        client = mock_agent_context.deps.client
        client.short_term.get_conversation.return_value = Conversation(
            session_id="test-session",
            messages=[Message(role=MessageRole.USER, content=f"message {i}") for i in range(30)],
        )
        mock_agent_context.deps.session_id = "test-session"

        results = await get_conversation_context(mock_agent_context, limit=5)

        assert [row["content"] for row in results] == [f"message {i}" for i in range(25, 30)]


class TestConversationHistory:
    async def test_history_contains_the_last_turn(self):
        now = datetime.now(timezone.utc)
        messages = []
        for i in range(30):
            role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
            messages.append(
                Message(role=role, content=f"turn {i}", created_at=now + timedelta(seconds=i))
            )
        memory = MagicMock()
        memory.short_term.get_conversation = AsyncMock(
            return_value=Conversation(session_id="s", messages=messages)
        )

        history = await get_conversation_history(memory, "s", limit=20)

        assert len(history) == 20
        # The newest message must survive the truncation -- the old code kept
        # the oldest 20 and permanently lost recent context.
        rendered = [part.content for msg in history for part in msg.parts]
        assert "turn 29" in rendered
        assert "turn 0" not in rendered
        # No `limit=` is passed: the Cypher LIMIT applies to the oldest rows.
        assert memory.short_term.get_conversation.await_args.kwargs == {"session_id": "s"}


class TestTouchedEntities:
    def test_entity_shaped_rows_become_entity_refs(self):
        refs = extract_touched_entities(
            [
                {"name": "Brian Chesky", "type": "PERSON"},
                {"name": "Airbnb", "type": "ORGANIZATION:COMPANY"},
                {"name": "not an entity", "type": "whatever"},
                {"count": 3},
            ]
        )
        assert {(r.name, r.type) for r in refs} == {
            ("Brian Chesky", "PERSON"),
            ("Airbnb", "ORGANIZATION"),
        }

    def test_non_list_results_are_ignored(self):
        assert extract_touched_entities({"name": "x", "type": "PERSON"}) == []


@pytest.fixture
def client_and_memory(monkeypatch, mock_memory_client):
    """A TestClient over the memory + threads routers with a mocked client."""
    app = FastAPI()
    app.include_router(memory_routes.router, prefix="/api")
    app.include_router(thread_routes.router, prefix="/api")
    monkeypatch.setattr(memory_routes, "get_memory_client", lambda: mock_memory_client)
    monkeypatch.setattr(thread_routes, "get_memory_client", lambda: mock_memory_client)
    with TestClient(app) as client:
        yield client, mock_memory_client


class TestLocationRoutes:
    def test_nearby_returns_bulk_geocoded_locations(self, client_and_memory):
        client, memory = client_and_memory
        # A bulk-geocoded entity has the Neo4j Point property but nothing in its
        # serialized attributes blob -- the old handler dropped every such row.
        entity = Entity(name="Oakland", type="LOCATION")
        entity.metadata["distance_km"] = 13.4
        memory.long_term.search_locations_near.return_value = [entity]
        memory.long_term.get_location_coordinates.return_value = (37.80, -122.27)

        response = client.get("/api/locations/nearby", params={"lat": 37.77, "lon": -122.42})

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "Oakland"
        assert body[0]["latitude"] == pytest.approx(37.80)
        assert body[0]["distance_km"] == pytest.approx(13.4)

    def test_bounds_returns_bulk_geocoded_locations(self, client_and_memory):
        client, memory = client_and_memory
        memory.long_term.search_locations_in_bounding_box.return_value = [
            Entity(name="Oakland", type="LOCATION")
        ]
        memory.long_term.get_location_coordinates.return_value = (37.80, -122.27)

        response = client.get(
            "/api/locations/bounds",
            params={"min_lat": 37, "max_lat": 38, "min_lon": -123, "max_lon": -122},
        )

        assert response.status_code == 200
        assert [row["name"] for row in response.json()] == ["Oakland"]


class TestPreferenceRoutes:
    def test_delete_missing_preference_is_404(self, client_and_memory):
        client, memory = client_and_memory
        memory.query.cypher = AsyncMock(return_value=[])

        response = client.delete(f"/api/preferences/{uuid4()}")

        assert response.status_code == 404
        memory.graph.execute_write.assert_not_called()

    def test_delete_existing_preference_writes(self, client_and_memory):
        client, memory = client_and_memory
        pref_id = str(uuid4())
        memory.query.cypher = AsyncMock(return_value=[{"id": pref_id}])

        response = client.delete(f"/api/preferences/{pref_id}")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        memory.graph.execute_write.assert_awaited_once()

    def test_list_preferences_uses_public_accessor(self, client_and_memory):
        client, memory = client_and_memory
        memory.long_term.get_preferences_by_category.side_effect = (
            lambda category, **_: [_preference(category)] if category == "topics" else []
        )

        response = client.get("/api/preferences")

        assert response.status_code == 200
        assert [row["category"] for row in response.json()] == ["topics"]


class TestThreadRoutes:
    def test_unknown_thread_is_404(self, client_and_memory):
        client, memory = client_and_memory
        memory.query.cypher = AsyncMock(return_value=[])

        assert client.get("/api/threads/does-not-exist").status_code == 404
        assert client.delete("/api/threads/does-not-exist").status_code == 404

    def test_threads_are_persisted_as_conversations(self, client_and_memory):
        client, memory = client_and_memory
        created = datetime.now(timezone.utc)
        memory.short_term.create_conversation.return_value = Conversation(
            session_id="chat-x", created_at=created
        )

        response = client.post("/api/threads", json={"title": "Growth questions"})

        assert response.status_code == 200
        body = response.json()
        assert body["id"].startswith(thread_routes.THREAD_PREFIX)
        assert body["title"] == "Growth questions"
        memory.short_term.create_conversation.assert_awaited_once()
        memory.graph.execute_write.assert_awaited_once()

    def test_list_threads_only_sees_chat_sessions(self, client_and_memory):
        client, memory = client_and_memory
        memory.short_term.list_sessions.return_value = []

        assert client.get("/api/threads").json() == []
        kwargs = memory.short_term.list_sessions.await_args.kwargs
        assert kwargs["prefix"] == "chat-"
