"""CrewAI integration for neo4j-agent-memory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model as _passthrough,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm import LLMProvider


def llm_provider_from_crewai(model: Any) -> LLMProvider:
    """Translate a CrewAI agent's LLM into an :class:`LLMProvider`.

    CrewAI uses LiteLLM under the hood, so a CrewAI ``LLM`` exposes a
    ``model`` attribute that is already provider-prefixed (e.g.
    ``"anthropic/claude-3-5-sonnet-latest"``). The shared introspector
    reads that directly. For string-only model configs, callers should
    pass the string to :func:`neo4j_agent_memory.llm.from_provider`
    instead — this helper is for LLM-instance handoff.
    """
    return _passthrough(model)


try:
    from neo4j_agent_memory.integrations.crewai.memory import Neo4jCrewMemory

    __all__ = [
        "Neo4jCrewMemory",
        "llm_provider_from_crewai",
    ]
except ImportError:
    __all__ = ["llm_provider_from_crewai"]
