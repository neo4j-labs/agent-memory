"""Shared fixtures for the backend test suite.

Everything here runs **offline**: no Neo4j, no Google credentials, no network.
The FastAPI app is exercised with a stubbed ``FinancialMemoryService`` and a
stubbed ``Neo4jDomainService``, which is exactly what catches the class of bug
that used to live in these routes (attributes that do not exist on the real
objects are still attributes that do not exist on a spec'd mock).
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Settings require a Neo4j password and must not pick up a developer's .env.
os.environ.setdefault("NEO4J_PASSWORD", "test-password")
os.environ["NEO4J_URI"] = "bolt://localhost:7688"
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_GENAI_USE_VERTEXAI", None)


@pytest.fixture
def fake_reasoning() -> MagicMock:
    """A stub reasoning layer that records what it was asked to write."""
    reasoning = MagicMock()
    trace = MagicMock()
    trace.id = "11111111-1111-1111-1111-111111111111"
    step = MagicMock()
    step.id = "22222222-2222-2222-2222-222222222222"
    reasoning.start_trace = AsyncMock(return_value=trace)
    reasoning.add_step = AsyncMock(return_value=step)
    reasoning.record_tool_call = AsyncMock()
    reasoning.complete_trace = AsyncMock()
    reasoning.get_trace_with_steps = AsyncMock(return_value=None)
    return reasoning


@pytest.fixture
def fake_memory_service(fake_reasoning: MagicMock) -> MagicMock:
    """A stub ``FinancialMemoryService``."""
    service = MagicMock()
    client = MagicMock()
    client.reasoning = fake_reasoning
    client.query.cypher = AsyncMock(return_value=[])
    service.client = client
    service.adk_memory_service = MagicMock()
    service.search_context = AsyncMock(
        return_value=[
            {
                "content": "CUST-003 shows structuring behaviour",
                "type": "message",
                "score": 0.91,
                "metadata": {"role": "assistant"},
            }
        ]
    )
    message = MagicMock()
    message.id = "33333333-3333-3333-3333-333333333333"
    service.store_message = AsyncMock(return_value=message)
    service.store_finding = AsyncMock(return_value="Stored finding as fact abc")
    service.clear_session = AsyncMock()
    service.get_conversation_history = AsyncMock(return_value=[])
    return service


@pytest.fixture
def fake_neo4j_service() -> MagicMock:
    """A stub ``Neo4jDomainService``."""
    service = MagicMock()
    service.get_graph_stats = AsyncMock(
        return_value={
            "total_nodes": 7,
            "total_relationships": 3,
            "nodes_by_label": {"Customer": 3, "Entity": 4},
            "relationships_by_type": {"MENTIONS": 3},
        }
    )
    service.get_neighbors = AsyncMock(
        return_value={
            "entity_id": "CUST-001",
            "depth": 1,
            "nodes": [],
            "edges": [],
            "total_nodes": 0,
            "total_edges": 0,
        }
    )
    service.get_memory_graph = AsyncMock(return_value={"nodes": [], "relationships": []})
    service.get_entity_audit_trail = AsyncMock(return_value=[])
    service.get_customer = AsyncMock(return_value={"id": "CUST-001", "name": "Alice Johnson"})
    service.list_investigations = AsyncMock(return_value=[])
    service.get_investigation = AsyncMock(return_value=None)
    service.create_investigation = AsyncMock(
        return_value={
            "id": "INV-TEST0001",
            "customer_id": "CUST-001",
            "type": "comprehensive",
            "reason": "Periodic review",
            "status": "pending",
            "priority": "normal",
        }
    )
    service.update_investigation = AsyncMock(
        return_value={
            "id": "INV-TEST0001",
            "customer_id": "CUST-001",
            "type": "comprehensive",
            "reason": "Periodic review",
            "status": "pending",
            "priority": "normal",
            "session_id": "inv-session-INV-TEST0001",
        }
    )
    return service


@pytest.fixture
def client(fake_memory_service: MagicMock, fake_neo4j_service: MagicMock) -> Any:
    """A ``TestClient`` over the real app, with the services stubbed.

    The lifespan is deliberately not entered, so nothing tries to connect to
    Neo4j or Vertex AI.
    """
    from fastapi.testclient import TestClient

    from src.main import app
    from src.services.memory_service import get_initialized_memory_service

    app.dependency_overrides[get_initialized_memory_service] = lambda: fake_memory_service
    app.state.neo4j_service = fake_neo4j_service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        app.state.neo4j_service = None
