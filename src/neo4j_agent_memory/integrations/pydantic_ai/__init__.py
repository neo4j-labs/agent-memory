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
    ``OpenAIModel`` / ``AnthropicModel`` provide the provider prefix::

        from pydantic_ai.models.anthropic import AnthropicModel
        from neo4j_agent_memory.integrations.pydantic_ai import (
            llm_provider_from_pydantic_ai,
        )

        model = AnthropicModel("claude-3-5-sonnet-latest")
        provider = llm_provider_from_pydantic_ai(model)
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
