"""Tests for the Neo4j Agent Memory HTTP API server.

Uses FastAPI TestClient with a mocked MemoryClient to verify
endpoint routing, request validation, and response models without
requiring a live Neo4j instance.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neo4j_agent_memory.server import create_app
from neo4j_agent_memory.server.config import ServerConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_message(role="user", content="Hello", session_id="test-session"):
    """Create a mock Message object."""
    msg = MagicMock()
    msg.id = uuid4()
    msg.role = MagicMock(value=role)
    msg.content = content
    msg.conversation_id = None
    msg.tool_calls = None
    msg.created_at = datetime(2025, 1, 1, 12, 0, 0)
    msg.updated_at = None
    msg.metadata = {}
    return msg


def _make_entity(name="Acme Corp", entity_type="ORGANIZATION"):
    """Create a mock Entity object."""
    ent = MagicMock()
    ent.id = uuid4()
    ent.name = name
    ent.canonical_name = None
    ent.type = entity_type
    ent.subtype = None
    ent.description = f"A test entity: {name}"
    ent.confidence = 0.95
    ent.aliases = []
    ent.attributes = {}
    ent.created_at = datetime(2025, 1, 1, 12, 0, 0)
    ent.metadata = {}
    return ent


def _make_preference(category="food", preference="Likes pizza"):
    """Create a mock Preference object."""
    pref = MagicMock()
    pref.id = uuid4()
    pref.category = category
    pref.preference = preference
    pref.context = None
    pref.confidence = 1.0
    pref.created_at = datetime(2025, 1, 1, 12, 0, 0)
    return pref


def _make_trace(task="Find restaurants", success=True):
    """Create a mock ReasoningTrace object."""
    trace = MagicMock()
    trace.id = uuid4()
    trace.session_id = "test-session"
    trace.task = task
    trace.steps = []
    trace.outcome = "Found 3 restaurants"
    trace.success = success
    trace.started_at = datetime(2025, 1, 1, 12, 0, 0)
    trace.completed_at = datetime(2025, 1, 1, 12, 0, 5)
    return trace


def _make_session(session_id="test-session"):
    """Create a mock SessionInfo object."""
    session = MagicMock()
    session.session_id = session_id
    session.title = None
    session.created_at = datetime(2025, 1, 1, 12, 0, 0)
    session.updated_at = datetime(2025, 1, 1, 12, 5, 0)
    session.message_count = 5
    session.first_message_preview = "Hello"
    session.last_message_preview = "Goodbye"
    return session


def _make_conversation(session_id="test-session"):
    """Create a mock Conversation object."""
    conv = MagicMock()
    conv.id = uuid4()
    conv.session_id = session_id
    conv.title = None
    conv.messages = [_make_message(), _make_message(role="assistant", content="Hi there!")]
    conv.created_at = datetime(2025, 1, 1, 12, 0, 0)
    conv.updated_at = datetime(2025, 1, 1, 12, 5, 0)
    return conv


def _make_dedup_result():
    """Create a mock DeduplicationResult."""
    result = MagicMock()
    result.is_duplicate = False
    result.action = "none"
    result.matched_entity_id = None
    result.matched_entity_name = None
    result.similarity_score = 0.0
    result.match_type = None
    return result


def _make_tool_stats(name="search_web"):
    """Create a mock ToolStats object."""
    stats = MagicMock()
    stats.name = name
    stats.description = "Web search tool"
    stats.total_calls = 10
    stats.successful_calls = 8
    stats.failed_calls = 2
    stats.success_rate = 0.8
    stats.avg_duration_ms = 150.0
    stats.last_used_at = datetime(2025, 1, 1, 12, 0, 0)
    return stats


def _create_mock_memory_client():
    """Create a comprehensive mock MemoryClient."""
    client = MagicMock()
    client.is_connected = True

    # Short-term
    client.short_term = MagicMock()
    client.short_term.list_sessions = AsyncMock(return_value=[_make_session()])
    client.short_term.get_conversation = AsyncMock(return_value=_make_conversation())
    client.short_term.add_message = AsyncMock(return_value=_make_message())
    client.short_term.search_messages = AsyncMock(return_value=[_make_message()])
    client.short_term.clear_session = AsyncMock(return_value=None)
    client.short_term.delete_message = AsyncMock(return_value=True)
    client.short_term.get_conversation_summary = AsyncMock(
        return_value=MagicMock(
            session_id="test-session",
            summary="A test conversation",
            message_count=5,
            key_entities=["Acme"],
            key_topics=["food"],
            generated_at=datetime(2025, 1, 1, 12, 0, 0),
        )
    )

    # Long-term
    client.long_term = MagicMock()
    client.long_term.add_entity = AsyncMock(return_value=(_make_entity(), _make_dedup_result()))
    client.long_term.search_entities = AsyncMock(return_value=[_make_entity()])
    client.long_term.get_entity_by_name = AsyncMock(return_value=_make_entity())
    client.long_term.get_related_entities = AsyncMock(return_value=[])
    client.long_term.add_preference = AsyncMock(return_value=_make_preference())
    client.long_term.search_preferences = AsyncMock(return_value=[_make_preference()])
    client.long_term.get_preferences_by_category = AsyncMock(return_value=[_make_preference()])
    client.long_term.add_relationship = AsyncMock(
        return_value=MagicMock(
            id=uuid4(),
            source_id=uuid4(),
            target_id=uuid4(),
            type="WORKS_AT",
            description=None,
            confidence=1.0,
            created_at=datetime(2025, 1, 1, 12, 0, 0),
        )
    )

    # Reasoning
    client.reasoning = MagicMock()
    client.reasoning.list_traces = AsyncMock(return_value=[_make_trace()])
    client.reasoning.start_trace = AsyncMock(return_value=_make_trace())
    client.reasoning.get_trace = AsyncMock(return_value=_make_trace())
    client.reasoning.complete_trace = AsyncMock(return_value=_make_trace())
    client.reasoning.add_step = AsyncMock(
        return_value=MagicMock(
            id=uuid4(),
            trace_id=uuid4(),
            step_number=1,
            thought="Thinking...",
            action="search",
            observation="Found results",
            tool_calls=[],
            created_at=datetime(2025, 1, 1, 12, 0, 0),
        )
    )
    client.reasoning.get_similar_traces = AsyncMock(return_value=[_make_trace()])
    client.reasoning.record_tool_call = AsyncMock(
        return_value=MagicMock(
            id=uuid4(),
            tool_name="search_web",
            arguments={"q": "test"},
            result="results",
            status=MagicMock(value="success"),
            duration_ms=100,
            error=None,
            created_at=datetime(2025, 1, 1, 12, 0, 0),
        )
    )
    client.reasoning.get_tool_stats = AsyncMock(return_value=[_make_tool_stats()])

    # Top-level
    client.get_context = AsyncMock(return_value="## Context\nSome context text")
    client.get_context_structured = AsyncMock(
        return_value=MagicMock(
            context_text="## Context\nSome context text",
            messages=[_make_message()],
            entities=[_make_entity()],
            preferences=[_make_preference()],
            traces=[_make_trace()],
            stats={"messages": 1, "entities": 1, "preferences": 1, "traces": 1},
        )
    )
    client.get_stats = AsyncMock(
        return_value={
            "conversations": 1,
            "messages": 5,
            "entities": 3,
            "preferences": 2,
            "facts": 0,
            "traces": 1,
        }
    )

    return client


def _create_test_app(mock_client, *, api_key: str | None = None):
    """Create a FastAPI app with mocked lifespan for testing."""
    from fastapi.middleware.cors import CORSMiddleware

    from neo4j_agent_memory.server.models import HealthResponse
    from neo4j_agent_memory.server.routes import create_api_router

    @asynccontextmanager
    async def mock_lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.memory_client = mock_client
        yield

    app = FastAPI(title="Test", lifespan=mock_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if api_key:
        from neo4j_agent_memory.server.auth import APIKeyMiddleware

        app.add_middleware(APIKeyMiddleware, api_key=api_key)

    api_router = create_api_router()
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health_check() -> HealthResponse:
        client = getattr(app.state, "memory_client", None)
        return HealthResponse(
            status="healthy" if client and client.is_connected else "degraded",
            memory_connected=bool(client and client.is_connected),
            version="test",
        )

    return app


@pytest.fixture
def mock_client():
    return _create_mock_memory_client()


@pytest.fixture
def test_client(mock_client):
    """Create a TestClient with a mocked MemoryClient."""
    app = _create_test_app(mock_client)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["memory_connected"] is True
        assert "version" in data


# ---------------------------------------------------------------------------
# Context endpoints
# ---------------------------------------------------------------------------


class TestContextEndpoints:
    def test_post_context(self, test_client):
        resp = test_client.post(
            "/api/v1/context",
            json={"query": "restaurant recommendations"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "context_text" in data
        assert "messages" in data
        assert "entities" in data
        assert "preferences" in data
        assert "traces" in data
        assert "stats" in data
        assert isinstance(data["messages"], list)
        assert isinstance(data["entities"], list)

    def test_post_context_text(self, test_client):
        resp = test_client.post(
            "/api/v1/context/text",
            json={"query": "test"},
        )
        assert resp.status_code == 200
        assert "Context" in resp.text

    def test_get_stats(self, test_client):
        resp = test_client.get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == 5
        assert data["entities"] == 3


# ---------------------------------------------------------------------------
# Short-term endpoints
# ---------------------------------------------------------------------------


class TestShortTermEndpoints:
    def test_list_sessions(self, test_client):
        resp = test_client.get("/api/v1/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["session_id"] == "test-session"

    def test_get_session(self, test_client):
        resp = test_client.get("/api/v1/sessions/test-session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "test-session"
        assert len(data["messages"]) == 2

    def test_delete_session(self, test_client):
        resp = test_client.delete("/api/v1/sessions/test-session")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_add_message(self, test_client):
        resp = test_client.post(
            "/api/v1/sessions/test-session/messages",
            json={"role": "user", "content": "Hello!"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["role"] == "user"
        assert data["content"] == "Hello"  # from mock

    def test_get_messages(self, test_client):
        resp = test_client.get("/api/v1/sessions/test-session/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_search_messages(self, test_client):
        resp = test_client.post(
            "/api/v1/messages/search",
            json={"query": "restaurant"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_delete_message(self, test_client):
        resp = test_client.delete(f"/api/v1/messages/{uuid4()}")
        assert resp.status_code == 200

    def test_get_session_summary(self, test_client):
        resp = test_client.get("/api/v1/sessions/test-session/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "test-session"
        assert "summary" in data


# ---------------------------------------------------------------------------
# Long-term endpoints
# ---------------------------------------------------------------------------


class TestLongTermEndpoints:
    def test_add_entity(self, test_client):
        resp = test_client.post(
            "/api/v1/entities",
            json={"name": "Acme Corp", "entity_type": "ORGANIZATION"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "entity" in data
        assert "deduplication" in data
        assert data["entity"]["name"] == "Acme Corp"
        assert data["deduplication"]["is_duplicate"] is False

    def test_search_entities(self, test_client):
        resp = test_client.post(
            "/api/v1/entities/search",
            json={"query": "Acme"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_get_entity_by_name(self, test_client):
        resp = test_client.get("/api/v1/entities/Acme Corp")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Acme Corp"

    def test_add_preference(self, test_client):
        resp = test_client.post(
            "/api/v1/preferences",
            json={"category": "food", "preference": "Likes pizza"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["category"] == "food"

    def test_search_preferences(self, test_client):
        resp = test_client.post(
            "/api/v1/preferences/search",
            json={"query": "food"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_preferences_by_category(self, test_client):
        resp = test_client.get("/api/v1/preferences/category/food")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_add_relationship(self, test_client):
        resp = test_client.post(
            "/api/v1/relationships",
            json={
                "source_id": "John",
                "target_id": "Acme Corp",
                "relationship_type": "WORKS_AT",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "WORKS_AT"


# ---------------------------------------------------------------------------
# Reasoning endpoints
# ---------------------------------------------------------------------------


class TestReasoningEndpoints:
    def test_list_traces(self, test_client):
        resp = test_client.get("/api/v1/traces")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_start_trace(self, test_client):
        resp = test_client.post(
            "/api/v1/traces",
            json={"session_id": "test-session", "task": "Find restaurants"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["task"] == "Find restaurants"

    def test_get_trace(self, test_client):
        trace_id = str(uuid4())
        resp = test_client.get(f"/api/v1/traces/{trace_id}")
        assert resp.status_code == 200

    def test_complete_trace(self, test_client):
        trace_id = str(uuid4())
        resp = test_client.post(
            f"/api/v1/traces/{trace_id}/complete",
            json={"outcome": "Found 3 restaurants", "success": True},
        )
        assert resp.status_code == 200

    def test_add_step(self, test_client):
        trace_id = str(uuid4())
        resp = test_client.post(
            f"/api/v1/traces/{trace_id}/steps",
            json={"thought": "Thinking...", "action": "search"},
        )
        assert resp.status_code == 201

    def test_search_traces(self, test_client):
        resp = test_client.post(
            "/api/v1/traces/search",
            json={"query": "restaurant"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_record_tool_call(self, test_client):
        resp = test_client.post(
            "/api/v1/tool-calls",
            json={
                "step_id": str(uuid4()),
                "tool_name": "search_web",
                "arguments": {"q": "restaurants"},
            },
        )
        assert resp.status_code == 201

    def test_get_tool_stats(self, test_client):
        resp = test_client.get("/api/v1/tool-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "search_web"


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    def test_no_auth_by_default(self, test_client):
        """Without API key config, all endpoints should be accessible."""
        resp = test_client.get("/health")
        assert resp.status_code == 200

    def test_auth_rejects_without_key(self):
        """With API key config, requests without key should be rejected."""
        app = _create_test_app(_create_mock_memory_client(), api_key="test-secret-key")
        with TestClient(app) as client:
            # Health should still work (public path)
            resp = client.get("/health")
            assert resp.status_code == 200

            # API endpoint should be rejected
            resp = client.get("/api/v1/sessions")
            assert resp.status_code == 401

    def test_auth_accepts_with_key(self):
        """With correct API key, requests should succeed."""
        app = _create_test_app(_create_mock_memory_client(), api_key="test-secret-key")
        with TestClient(app) as client:
            resp = client.get(
                "/api/v1/sessions",
                headers={"X-API-Key": "test-secret-key"},
            )
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class TestRequestValidation:
    def test_context_requires_query(self, test_client):
        resp = test_client.post("/api/v1/context", json={})
        assert resp.status_code == 422

    def test_add_message_requires_role_and_content(self, test_client):
        resp = test_client.post(
            "/api/v1/sessions/test/messages",
            json={"role": "user"},
        )
        assert resp.status_code == 422

    def test_add_entity_requires_name_and_type(self, test_client):
        resp = test_client.post("/api/v1/entities", json={"name": "Acme"})
        assert resp.status_code == 422

    def test_start_trace_requires_session_and_task(self, test_client):
        resp = test_client.post("/api/v1/traces", json={"session_id": "test"})
        assert resp.status_code == 422

    def test_max_items_validation(self, test_client):
        resp = test_client.post(
            "/api/v1/context",
            json={"query": "test", "max_items": 0},
        )
        assert resp.status_code == 422

        resp = test_client.post(
            "/api/v1/context",
            json={"query": "test", "max_items": 200},
        )
        assert resp.status_code == 422
