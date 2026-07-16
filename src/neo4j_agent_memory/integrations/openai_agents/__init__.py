"""OpenAI Agents SDK integration for Neo4j Agent Memory.

This module provides memory integration for OpenAI's Agents SDK,
enabling persistent conversation history, entity knowledge, and
reasoning trace recording.

Example:
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.integrations.openai_agents import (
        Neo4jOpenAIMemory,
        create_memory_tools,
        record_agent_trace,
    )

    async with MemoryClient(settings) as client:
        memory = Neo4jOpenAIMemory(
            memory_client=client,
            session_id="user-123",
        )

        # Get context for system prompt
        context = await memory.get_context("user query")

        # Create function tools for the agent
        tools = create_memory_tools(memory)

        # Record agent execution as reasoning trace
        await record_agent_trace(memory, messages, task="Help user")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model as _passthrough,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm import LLMProvider


def llm_provider_from_openai_agents(model: Any) -> LLMProvider:
    """Translate an OpenAI Agents SDK model into an :class:`LLMProvider`.

    The Agents SDK uses OpenAI ``AsyncOpenAI`` clients and bare model
    strings (e.g. ``"gpt-4o-mini"``). Pass either:

    * A bare model name string — defaults to OpenAI provider.
    * An object exposing ``.model`` or ``.model_name``.

    For string inputs the helper short-circuits to
    :func:`from_provider` with the ``openai/`` prefix added.
    """
    if isinstance(model, str):
        from neo4j_agent_memory.llm import from_provider

        model_id = model if "/" in model else f"openai/{model}"
        return from_provider(model_id)
    return _passthrough(model)


try:
    from .memory import Neo4jOpenAIMemory, create_memory_tools
    from .tracing import record_agent_trace

    __all__ = [
        "Neo4jOpenAIMemory",
        "create_memory_tools",
        "record_agent_trace",
        "llm_provider_from_openai_agents",
    ]
except ImportError:
    # OpenAI not installed
    __all__ = ["llm_provider_from_openai_agents"]
