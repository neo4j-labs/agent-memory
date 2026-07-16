"""LlamaIndex integration for neo4j-agent-memory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model as _passthrough,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm import LLMProvider


def llm_provider_from_llamaindex(model: Any) -> LLMProvider:
    """Translate a LlamaIndex ``LLM`` into an :class:`LLMProvider`.

    LlamaIndex LLM classes expose ``model``; class names like
    ``OpenAI`` (in ``llama_index.llms.openai``) and ``Anthropic`` (in
    ``llama_index.llms.anthropic``) drive provider detection.
    """
    return _passthrough(model)


try:
    from neo4j_agent_memory.integrations.llamaindex.memory import Neo4jLlamaIndexMemory

    __all__ = [
        "Neo4jLlamaIndexMemory",
        "llm_provider_from_llamaindex",
    ]
except ImportError:
    __all__ = ["llm_provider_from_llamaindex"]
