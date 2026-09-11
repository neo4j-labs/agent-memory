"""Unit tests for the FinancialMemoryService with correct API signatures."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from neo4j_agent_memory.schema import TraceOutcome


class MockRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class MockMessage:
    def __init__(self, role, content, created_at=None, metadata=None):
        self.role = MockRole(role)
        self.content = content
        self.created_at = created_at or datetime.utcnow()
        self.metadata = metadata or {}


class MockConversation:
    def __init__(self, messages):
        self.messages = messages


class MockReasoningTrace:
    def __init__(self, trace_id=None, task="test", outcome=None, success=None):
        self.id = trace_id or uuid4()
        self.task = task
        self.outcome = outcome
        self.success = success
        self.started_at = datetime.utcnow()
        self.completed_at = None
        self.steps = []
        self.metadata = {}
        self.session_id = "test-session"


class MockReasoningStep:
    def __init__(self, step_id=None):
        self.id = step_id or uuid4()
        self.step_number = 1
        self.thought = "thinking"
        self.action = "doing"
        self.observation = "result"
        self.tool_calls = []
        self.metadata = {}


class TestMemoryServiceConversation:
    """Test conversation memory methods use correct API."""

    @pytest.mark.asyncio
    async def test_get_conversation_history_accesses_messages(self):
        """get_conversation() returns Conversation object; must access .messages."""
        with (
            patch("src.services.memory_service.get_settings"),
            patch("src.services.memory_service.MemoryClient"),
        ):
            from src.services.memory_service import FinancialMemoryService

            svc = FinancialMemoryService.__new__(FinancialMemoryService)
            svc._client = MagicMock()
            svc._connected = True

            messages = [
                MockMessage("user", "Hello"),
                MockMessage("assistant", "Hi there"),
            ]
            svc._client.short_term.get_conversation = AsyncMock(
                return_value=MockConversation(messages)
            )

            result = await svc.get_conversation_history("session-1")
            assert len(result) == 2
            assert result[0]["role"] == "user"
            assert result[1]["content"] == "Hi there"
            assert result[0]["timestamp"] is not None

    @pytest.mark.asyncio
    async def test_search_conversations_uses_search_messages(self):
        """search_conversations uses search_messages (not search)."""
        with (
            patch("src.services.memory_service.get_settings"),
            patch("src.services.memory_service.MemoryClient"),
        ):
            from src.services.memory_service import FinancialMemoryService

            svc = FinancialMemoryService.__new__(FinancialMemoryService)
            svc._client = MagicMock()
            svc._connected = True

            svc._client.short_term.search_messages = AsyncMock(
                return_value=[MockMessage("user", "test query")]
            )

            result = await svc.search_conversations("test")
            assert len(result) == 1
            assert result[0]["content"] == "test query"
            svc._client.short_term.search_messages.assert_called_once()


class TestMemoryServiceReasoning:
    """Test reasoning memory methods use correct parameter names."""

    @pytest.mark.asyncio
    async def test_start_trace_returns_string_id(self):
        """start_trace returns ReasoningTrace; we return str(trace.id)."""
        with (
            patch("src.services.memory_service.get_settings"),
            patch("src.services.memory_service.MemoryClient"),
        ):
            from src.services.memory_service import FinancialMemoryService

            svc = FinancialMemoryService.__new__(FinancialMemoryService)
            svc._client = MagicMock()
            svc._connected = True

            mock_trace = MockReasoningTrace()
            svc._client.reasoning.start_trace = AsyncMock(return_value=mock_trace)

            result = await svc.start_investigation_trace("session-1", "Investigate CUST-003")
            assert isinstance(result, str)
            assert result == str(mock_trace.id)

    @pytest.mark.asyncio
    async def test_add_step_passes_thought_action_observation(self):
        """add_step uses thought/action/observation, not reasoning/result."""
        with (
            patch("src.services.memory_service.get_settings"),
            patch("src.services.memory_service.MemoryClient"),
        ):
            from src.services.memory_service import FinancialMemoryService

            svc = FinancialMemoryService.__new__(FinancialMemoryService)
            svc._client = MagicMock()
            svc._connected = True

            mock_step = MockReasoningStep()
            svc._client.reasoning.add_step = AsyncMock(return_value=mock_step)

            trace_id = str(uuid4())
            await svc.add_reasoning_step(
                trace_id=trace_id,
                agent="kyc",
                action="verify_identity",
                reasoning="Checking documents",
                result={"status": "verified"},
            )

            call_kwargs = svc._client.reasoning.add_step.call_args
            # First positional arg should be UUID
            assert isinstance(call_kwargs[0][0], UUID)
            # Keyword args should use thought, action, observation
            assert "thought" in call_kwargs[1]
            assert "action" in call_kwargs[1]
            assert "observation" in call_kwargs[1]
            # Should NOT have 'reasoning' or 'result' or 'session_id'
            assert "reasoning" not in call_kwargs[1]
            assert "result" not in call_kwargs[1]
            assert "session_id" not in call_kwargs[1]

    @pytest.mark.asyncio
    async def test_complete_trace_passes_outcome(self):
        """complete_trace uses outcome, not conclusion."""
        with (
            patch("src.services.memory_service.get_settings"),
            patch("src.services.memory_service.MemoryClient"),
        ):
            from src.services.memory_service import FinancialMemoryService

            svc = FinancialMemoryService.__new__(FinancialMemoryService)
            svc._client = MagicMock()
            svc._connected = True

            mock_trace = MockReasoningTrace()
            svc._client.reasoning.complete_trace = AsyncMock(return_value=mock_trace)

            trace_id = str(uuid4())
            await svc.complete_investigation_trace(trace_id, conclusion="Investigation complete")

            call_kwargs = svc._client.reasoning.complete_trace.call_args
            assert isinstance(call_kwargs[0][0], UUID)
            # v0.3+: a structured TraceOutcome, not a bare string plus a bool.
            outcome = call_kwargs[1]["outcome"]
            assert isinstance(outcome, TraceOutcome)
            assert outcome.summary == "Investigation complete"
            assert outcome.success is True
            assert "conclusion" not in call_kwargs[1]
            assert "session_id" not in call_kwargs[1]

    @pytest.mark.asyncio
    async def test_get_trace_serializes_correctly(self):
        """get_investigation_trace correctly serializes trace with steps."""
        with (
            patch("src.services.memory_service.get_settings"),
            patch("src.services.memory_service.MemoryClient"),
        ):
            from src.services.memory_service import FinancialMemoryService

            svc = FinancialMemoryService.__new__(FinancialMemoryService)
            svc._client = MagicMock()
            svc._connected = True

            mock_trace = MockReasoningTrace(
                task="Investigate CUST-003", outcome="Complete", success=True
            )
            mock_trace.steps = [MockReasoningStep()]
            svc._client.reasoning.get_trace = AsyncMock(return_value=mock_trace)

            result = await svc.get_investigation_trace(str(uuid4()))
            assert result is not None
            assert result["task"] == "Investigate CUST-003"
            assert result["outcome"] == "Complete"
            assert len(result["steps"]) == 1
            assert result["steps"][0]["thought"] == "thinking"
            assert result["steps"][0]["action"] == "doing"

    @pytest.mark.asyncio
    async def test_get_trace_returns_none_when_not_found(self):
        with (
            patch("src.services.memory_service.get_settings"),
            patch("src.services.memory_service.MemoryClient"),
        ):
            from src.services.memory_service import FinancialMemoryService

            svc = FinancialMemoryService.__new__(FinancialMemoryService)
            svc._client = MagicMock()
            svc._connected = True
            svc._client.reasoning.get_trace = AsyncMock(return_value=None)

            result = await svc.get_investigation_trace(str(uuid4()))
            assert result is None


class TestMemoryServiceClient:
    """Test that the client property is exposed."""

    def test_client_property_returns_memory_client(self):
        with (
            patch("src.services.memory_service.get_settings"),
            patch("src.services.memory_service.MemoryClient"),
        ):
            from src.services.memory_service import FinancialMemoryService

            svc = FinancialMemoryService.__new__(FinancialMemoryService)
            mock_client = MagicMock()
            svc._client = mock_client

            assert svc.client is mock_client
