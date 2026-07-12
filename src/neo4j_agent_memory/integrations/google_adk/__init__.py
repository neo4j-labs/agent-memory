"""Google Agent Development Kit (ADK) integration for neo4j-agent-memory.

Provides Neo4j-backed memory services for Google ADK agents.

Example:
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

    settings = MemorySettings(...)
    async with MemoryClient(settings) as client:
        memory_service = Neo4jMemoryService(client)
        # Use with Google ADK agent
"""

from typing import Any

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model as _passthrough,
)

__all__ = [
    "Neo4jMemoryService",
    "llm_provider_from_google_adk",
]


def llm_provider_from_google_adk(model: Any) -> Any:
    """Translate a Google ADK / Gemini model into an :class:`LLMProvider`.

    ADK agents typically use ``gemini-2.5-flash`` / ``gemini-2.5-pro``
    strings, or :class:`google.genai.Client` instances. The shared
    introspector detects ``gemini`` from the class/module name and
    routes via LiteLLM. Bare string inputs short-circuit to the same
    via :func:`from_provider` with the ``vertex_ai/`` prefix added.
    """
    if isinstance(model, str):
        from neo4j_agent_memory.llm import from_provider

        model_id = model if "/" in model else f"vertex_ai/{model}"
        return from_provider(model_id)
    return _passthrough(model)


def __getattr__(name: str) -> Any:
    if name == "Neo4jMemoryService":
        from neo4j_agent_memory.integrations.google_adk.memory_service import (
            Neo4jMemoryService,
        )

        return Neo4jMemoryService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
