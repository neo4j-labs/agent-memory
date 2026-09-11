"""Unit tests for the PydanticAI integration against the 2.x API.

These tests never reach the network: agent runs use
``pydantic_ai.models.test.TestModel`` and the memory client is a mock.
They pin the PydanticAI 2.x shapes the integration depends on:

* ``AgentRunResult.output`` (``.data`` is gone)
* ``AgentRunResult.usage`` / ``.response`` as properties
* plain (no ``RunContext``) tool functions in ``Agent(tools=...)``
* ``FunctionToolset`` for the ``toolsets=`` registration path
* ``@agent.instructions`` as the dynamic-prompt hook, with ``ctx.prompt``
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from neo4j_agent_memory.integrations.pydantic_ai import (
    MemoryDependency,
    create_memory_tools,
    record_agent_trace,
)
from neo4j_agent_memory.schema.models import TraceOutcome

pydantic_ai = pytest.importorskip("pydantic_ai", reason="pydantic-ai not installed")


@pytest.fixture
def mock_client() -> MagicMock:
    """A mock MemoryClient with every layer the tools touch stubbed."""
    client = MagicMock()
    client.short_term = MagicMock()
    client.long_term = MagicMock()
    client.reasoning = MagicMock()
    client.get_context = AsyncMock(return_value="remembered: likes Indian food")
    client.short_term.add_message = AsyncMock()
    client.short_term.search_messages = AsyncMock(return_value=[])
    client.long_term.search_entities = AsyncMock(return_value=[])
    client.long_term.search_preferences = AsyncMock(return_value=[])
    client.long_term.add_preference = AsyncMock()
    client.reasoning.get_similar_traces = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_reasoning() -> MagicMock:
    """A mock ReasoningMemory that records what record_agent_trace writes."""
    reasoning = MagicMock()
    trace = MagicMock()
    trace.id = uuid4()
    reasoning.start_trace = AsyncMock(return_value=trace)

    def _new_step(*_args: object, **_kwargs: object) -> MagicMock:
        return MagicMock(id=uuid4())

    reasoning.add_step = AsyncMock(side_effect=_new_step)
    reasoning.record_tool_call = AsyncMock()
    reasoning.complete_trace = AsyncMock(return_value=trace)
    return reasoning


class TestPydanticAI2xShapes:
    """Guard the 2.x identifiers this integration is written against."""

    def test_agent_has_no_result_type_parameter(self) -> None:
        params = inspect.signature(pydantic_ai.Agent.__init__).parameters
        assert "output_type" in params
        assert "result_type" not in params
        assert "toolsets" in params

    def test_run_result_has_output_not_data(self) -> None:
        from pydantic_ai.agent import AgentRunResult

        assert "output" in AgentRunResult.__dataclass_fields__
        assert "data" not in AgentRunResult.__dataclass_fields__

    def test_usage_and_response_are_properties(self) -> None:
        from pydantic_ai.agent import AgentRunResult

        assert isinstance(AgentRunResult.usage, property)
        assert isinstance(AgentRunResult.response, property)

    def test_current_model_class_names(self) -> None:
        from pydantic_ai.models import google, openai

        assert hasattr(openai, "OpenAIChatModel")
        assert not hasattr(openai, "OpenAIModel")
        assert hasattr(google, "GoogleModel")
        assert not hasattr(google, "GeminiModel")


class TestCreateMemoryTools:
    """``create_memory_tools`` returns plain callables usable on 2.x."""

    def test_tool_names(self, mock_client: MagicMock) -> None:
        names = [t.__name__ for t in create_memory_tools(mock_client)]
        assert names == ["search_memory", "save_preference", "recall_preferences"]

    def test_tools_take_no_run_context(self, mock_client: MagicMock) -> None:
        # PydanticAI 2.x accepts both ctx-first and plain tool functions;
        # these are plain, which is what the docstring promises.
        for tool in create_memory_tools(mock_client):
            first = next(iter(inspect.signature(tool).parameters), None)
            assert first != "ctx"

    async def test_tools_register_and_run_via_tools_kwarg(self, mock_client: MagicMock) -> None:
        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel

        agent = Agent(TestModel(), tools=create_memory_tools(mock_client))
        result = await agent.run("what do you remember?")

        called = _tool_names_called(result)
        assert {"search_memory", "save_preference", "recall_preferences"} <= set(called)
        mock_client.long_term.add_preference.assert_awaited()

    async def test_tools_register_as_function_toolset(self, mock_client: MagicMock) -> None:
        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel
        from pydantic_ai.toolsets import FunctionToolset

        toolset = FunctionToolset(create_memory_tools(mock_client), id="memory")
        agent = Agent(TestModel(), toolsets=[toolset])
        result = await agent.run("what do you remember?")

        assert "search_memory" in _tool_names_called(result)


class TestMemoryDependency:
    """The dependency works as ``deps_type`` with the 2.x instructions hook."""

    async def test_instructions_hook_uses_run_prompt(self, mock_client: MagicMock) -> None:
        from pydantic_ai import Agent, RunContext
        from pydantic_ai.models.test import TestModel

        agent = Agent(TestModel(), deps_type=MemoryDependency)

        @agent.instructions
        async def memory_context(ctx: RunContext[MemoryDependency]) -> str:
            context = await ctx.deps.get_context(str(ctx.prompt))
            return f"Context:\n{context}"

        deps = MemoryDependency(client=mock_client, session_id="s-1")
        result = await agent.run("find me a restaurant", deps=deps)

        mock_client.get_context.assert_awaited_once_with("find me a restaurant", session_id="s-1")
        assert isinstance(result.output, str)

    async def test_save_interaction_persists_both_turns(self, mock_client: MagicMock) -> None:
        deps = MemoryDependency(client=mock_client, session_id="s-2")
        await deps.save_interaction("hi", "hello", extract_entities=False)

        assert mock_client.short_term.add_message.await_count == 2
        roles = [call.args[1] for call in mock_client.short_term.add_message.await_args_list]
        assert roles == ["user", "assistant"]


class TestRecordAgentTrace:
    """``record_agent_trace`` reads a real 2.x ``AgentRunResult``."""

    async def _run(self, mock_client: MagicMock) -> Any:
        from pydantic_ai import Agent
        from pydantic_ai.models.test import TestModel

        agent = Agent(TestModel(), tools=create_memory_tools(mock_client))
        return await agent.run("find me a restaurant")

    async def test_records_steps_and_tool_calls(
        self, mock_client: MagicMock, mock_reasoning: MagicMock
    ) -> None:
        result = await self._run(mock_client)

        await record_agent_trace(
            mock_reasoning,
            session_id="s-3",
            result=result,
            task="Find restaurant recommendation",
        )

        start_kwargs = mock_reasoning.start_trace.await_args.kwargs
        assert start_kwargs["session_id"] == "s-3"
        assert start_kwargs["task"] == "Find restaurant recommendation"
        assert start_kwargs["metadata"]["source"] == "pydantic_ai"
        # ``result.response.model_name`` — a 2.x property.
        assert start_kwargs["metadata"]["model"] == "test"

        tools_called = _tool_names_called(result)
        assert mock_reasoning.add_step.await_count == len(tools_called)
        assert mock_reasoning.record_tool_call.await_count == len(tools_called)
        recorded = {
            call.kwargs["tool_name"] for call in mock_reasoning.record_tool_call.await_args_list
        }
        assert recorded == set(tools_called)

    async def test_completes_with_trace_outcome_and_usage_metrics(
        self, mock_client: MagicMock, mock_reasoning: MagicMock
    ) -> None:
        result = await self._run(mock_client)

        await record_agent_trace(mock_reasoning, session_id="s-4", result=result)

        outcome = mock_reasoning.complete_trace.await_args.kwargs["outcome"]
        assert isinstance(outcome, TraceOutcome)
        assert outcome.success is True
        # ``result.output`` — ``.data`` was removed in PydanticAI 1.0.
        assert outcome.summary == str(result.output)[:1000]
        # ``result.usage`` is a property in 2.x, so the metrics are populated.
        assert outcome.metrics["requests"] >= 1
        assert outcome.metrics["tool_calls"] == float(len(_tool_names_called(result)))
        assert "input_tokens" in outcome.metrics

    async def test_task_defaults_to_first_user_prompt(
        self, mock_client: MagicMock, mock_reasoning: MagicMock
    ) -> None:
        result = await self._run(mock_client)

        await record_agent_trace(mock_reasoning, session_id="s-5", result=result)

        assert mock_reasoning.start_trace.await_args.kwargs["task"] == "find me a restaurant"

    async def test_include_tool_calls_false_skips_steps(
        self, mock_client: MagicMock, mock_reasoning: MagicMock
    ) -> None:
        result = await self._run(mock_client)

        await record_agent_trace(
            mock_reasoning,
            session_id="s-6",
            result=result,
            include_tool_calls=False,
        )

        mock_reasoning.add_step.assert_not_awaited()
        mock_reasoning.record_tool_call.assert_not_awaited()
        mock_reasoning.complete_trace.assert_awaited_once()


def _tool_names_called(result: Any) -> list[str]:
    """Tool names invoked during a run, read off the 2.x message history."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    names: list[str] = []
    for message in result.all_messages():
        if isinstance(message, ModelResponse):
            names.extend(part.tool_name for part in message.parts if isinstance(part, ToolCallPart))
    return names
