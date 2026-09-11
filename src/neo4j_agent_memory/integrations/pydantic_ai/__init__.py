"""Pydantic AI integration for neo4j-agent-memory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model as _passthrough,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm import LLMProvider


def llm_provider_from_pydantic_ai(model: Any) -> LLMProvider:
    """Translate a Pydantic AI ``Model`` into an :class:`LLMProvider`.

    Pydantic AI Models expose ``model_name``; class names like
    ``OpenAIChatModel`` / ``AnthropicModel`` / ``GoogleModel`` (the 2.x
    names — ``OpenAIModel`` and ``GeminiModel`` are gone) provide the
    provider prefix::

        import os

        from pydantic_ai.models.openai import OpenAIChatModel
        from neo4j_agent_memory.integrations.pydantic_ai import (
            llm_provider_from_pydantic_ai,
        )

        model = OpenAIChatModel(os.getenv("OPENAI_MODEL", "gpt-5-mini"))
        provider = llm_provider_from_pydantic_ai(model)

    The same model instance can then back both the agent and memory's
    entity extraction, so you configure credentials once.
    """
    return _passthrough(model)


try:
    from neo4j_agent_memory.integrations.pydantic_ai.memory import (
        MemoryDependency,
        create_memory_tools,
        nams_memory_tools,
        record_agent_trace,
    )

    __all__ = [
        "MemoryDependency",
        "create_memory_tools",
        "nams_memory_tools",
        "record_agent_trace",
        "llm_provider_from_pydantic_ai",
    ]
except ImportError:
    __all__ = ["llm_provider_from_pydantic_ai"]
