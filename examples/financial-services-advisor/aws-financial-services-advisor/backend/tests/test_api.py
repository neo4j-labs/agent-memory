"""Tests for FastAPI endpoints including SSE streaming and traces."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

#: The event sequence Strands' ``stream_async`` produces for one tool call:
#: text deltas (``data``), one ``current_tool_use`` per tool-use delta, a
#: ``message`` carrying the ``toolResult``, then the terminal ``result``.
STREAMED_EVENTS: list[dict] = [
    {"data": "Delegating to the KYC agent"},
    {
        "current_tool_use": {
            "toolUseId": "tu-1",
            "name": "delegate_to_kyc_agent",
            "input": '{"customer_id": "CUST-003", "task": "verify identity"}',
        }
    },
    {
        "message": {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": "tu-1",
                        "status": "success",
                        "content": [{"text": "Identity unverified; 2 documents missing"}],
                    }
                }
            ],
        }
    },
    {"result": "Investigation complete. Risk level: CRITICAL."},
]


def _make_supervisor(events: list[dict] | None = None) -> MagicMock:
    """A stand-in Strands agent exposing the async API the routes now use."""
    agent = MagicMock()
    agent.invoke_async = AsyncMock(return_value="Investigation complete. Risk level: MEDIUM.")

    async def stream(_prompt):
        for event in events if events is not None else STREAMED_EVENTS:
            yield event

    agent.stream_async = stream
    return agent


@pytest.fixture
def mock_supervisor() -> MagicMock:
    return _make_supervisor()


@pytest.fixture
def app_client(mock_memory_service, mock_supervisor):
    """Create a TestClient with mocked services."""
    mock_neo4j_service = MagicMock()

    with (
        patch("src.services.memory_service.get_memory_service", return_value=mock_memory_service),
        patch("src.agents.supervisor.get_supervisor_agent", return_value=mock_supervisor),
        patch("src.api.routes.chat.get_supervisor_agent", return_value=mock_supervisor),
        patch("src.api.routes.chat.get_memory_service", return_value=mock_memory_service),
    ):
        from src.main import app

        app.state.neo4j_service = mock_neo4j_service
        yield TestClient(app, raise_server_exceptions=False)


def _sse_events(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event name, payload) pairs."""
    events: list[tuple[str, dict]] = []
    name: str | None = None
    for line in body.split("\n"):
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: ") and name is not None:
            try:
                events.append((name, json.loads(line[6:])))
            except json.JSONDecodeError:
                pass
    return events


class TestHealthEndpoints:
    def test_root(self, app_client):
        response = app_client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Financial Services Advisor"

    def test_health(self, app_client):
        response = app_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "components" in data

    def test_api_info(self, app_client):
        response = app_client.get("/api/info")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert "supervisor" in data["agents"]
        assert "kyc" in data["agents"]
        assert "aml" in data["agents"]
        assert "memory_types" in data


class TestChatAPI:
    def test_chat_sync(self, app_client):
        response = app_client.post(
            "/api/chat",
            json={
                "message": "Investigate CUST-001",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        assert data["agent"] == "supervisor"

    def test_chat_with_customer_context(self, app_client):
        response = app_client.post(
            "/api/chat",
            json={
                "message": "What is the risk level?",
                "customer_id": "CUST-003",
            },
        )
        assert response.status_code == 200

    def test_chat_missing_message(self, app_client):
        response = app_client.post("/api/chat", json={})
        assert response.status_code == 422

    def test_chat_sync_awaits_the_agent(self, app_client, mock_supervisor):
        """The route must use ``invoke_async``; ``agent(prompt)`` blocks the loop."""
        app_client.post("/api/chat", json={"message": "Investigate CUST-001"})
        mock_supervisor.invoke_async.assert_awaited_once()

    def test_chat_sync_writes_each_turn_once(self, app_client, mock_memory_service):
        """One turn is exactly two messages.

        The old code wrote the pair with ``add_conversation_message`` and then
        again through ``add_session``, which doubled every message, broke the
        FIRST_MESSAGE/NEXT_MESSAGE chain and made history return each turn twice.
        """
        app_client.post("/api/chat", json={"message": "Investigate CUST-001"})
        roles = [
            call.kwargs["role"]
            for call in mock_memory_service.add_conversation_message.await_args_list
        ]
        assert roles == ["user", "assistant"]

    def test_chat_stream_emits_tool_events_during_the_run(self, app_client):
        response = app_client.post(
            "/api/chat/stream", json={"message": "Investigate CUST-003", "customer_id": "CUST-003"}
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        events = _sse_events(response.text)
        names = [name for name, _ in events]
        assert names.index("tool_call") < names.index("response")
        assert {"agent_start", "thinking", "tool_call", "tool_result", "response", "done"} <= set(
            names
        )

        payloads = dict(events)
        assert payloads["tool_call"]["tool"] == "delegate_to_kyc_agent"
        assert "CRITICAL" in payloads["response"]["content"]
        assert payloads["trace_saved"]["tool_call_count"] == 1

    def test_chat_stream_records_tool_calls_with_audit_edges(self, app_client, mock_memory_service):
        """Each tool use becomes a step plus a ToolCall with :TOUCHED refs."""
        app_client.post(
            "/api/chat/stream", json={"message": "Investigate CUST-003", "customer_id": "CUST-003"}
        )

        mock_memory_service.start_investigation_trace.assert_awaited_once()
        assert (
            "triggered_by_message_id"
            in mock_memory_service.start_investigation_trace.await_args.kwargs
        )

        mock_memory_service.record_tool_call.assert_awaited_once()
        kwargs = mock_memory_service.record_tool_call.await_args.kwargs
        assert kwargs["tool_name"] == "delegate_to_kyc_agent"
        assert kwargs["arguments"]["customer_id"] == "CUST-003"
        touched = {ref.id for ref in kwargs["touched_entities"]}
        assert "CUST-003" in touched

        completion = mock_memory_service.complete_investigation_trace.await_args
        assert completion.kwargs["metrics"]["tool_calls"] == 1.0

    def test_chat_stream_reports_agent_failure(self, app_client, mock_memory_service):
        """A mid-stream failure closes the trace rather than leaking it."""

        async def failing_stream(_prompt):
            yield {"data": "starting"}
            raise RuntimeError("bedrock unavailable")

        with patch("src.api.routes.chat.get_supervisor_agent") as get_agent:
            agent = MagicMock()
            agent.stream_async = failing_stream
            get_agent.return_value = agent
            response = app_client.post("/api/chat/stream", json={"message": "Investigate"})

        names = [name for name, _ in _sse_events(response.text)]
        assert "error" in names
        assert (
            mock_memory_service.complete_investigation_trace.await_args.kwargs["error_kind"]
            == "agent_error"
        )

    def test_chat_history(self, app_client, mock_memory_service):
        mock_memory_service.get_conversation_history.return_value = [
            {"role": "user", "content": "Hello", "timestamp": "2024-01-01T00:00:00"},
            {"role": "assistant", "content": "Hi", "timestamp": "2024-01-01T00:00:01"},
        ]

        with patch("src.api.routes.chat.get_memory_service", return_value=mock_memory_service):
            response = app_client.get("/api/chat/history/test-session")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 2

    def test_chat_search(self, app_client, mock_memory_service):
        mock_memory_service.search_conversations.return_value = [
            {"content": "money laundering", "role": "user", "metadata": {}},
        ]

        with patch("src.api.routes.chat.get_memory_service", return_value=mock_memory_service):
            response = app_client.post(
                "/api/chat/search",
                json={
                    "query": "money laundering",
                },
            )
            assert response.status_code == 200


class TestTracesAPI:
    def test_get_session_traces(self, app_client, mock_memory_service):
        mock_reasoning = MagicMock()
        mock_reasoning.list_traces = AsyncMock(return_value=[])
        mock_memory_service.client.reasoning = mock_reasoning

        with patch("src.api.routes.traces.get_memory_service", return_value=mock_memory_service):
            response = app_client.get("/api/traces/test-session")
            assert response.status_code == 200
            assert response.json() == []

    def test_get_trace_detail_not_found(self, app_client, mock_memory_service):
        mock_reasoning = MagicMock()
        mock_reasoning.get_trace = AsyncMock(return_value=None)
        mock_memory_service.client.reasoning = mock_reasoning

        with patch("src.api.routes.traces.get_memory_service", return_value=mock_memory_service):
            response = app_client.get("/api/traces/detail/nonexistent-id")
            assert response.status_code == 404
