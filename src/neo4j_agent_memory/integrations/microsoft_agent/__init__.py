"""Microsoft Agent Framework integration for Neo4j Agent Memory.

This module provides memory integration for Microsoft's Agent Framework,
enabling persistent conversation history, entity knowledge, graph-enhanced
context retrieval, and reasoning trace recording.

Requires the Microsoft Agent Framework 1.x GA line. Install the adapter's
dependency with ``pip install neo4j-agent-memory[microsoft-agent]`` (which
pulls ``agent-framework-core``) plus whichever chat client you use — e.g.
``agent-framework-openai``, ``agent-framework-azure-ai``, or the
``agent-framework`` meta-package.

Example:
    from neo4j_agent_memory import MemoryClient, MemorySettings
    from neo4j_agent_memory.integrations.microsoft_agent import (
        Neo4jContextProvider,
        Neo4jChatMessageStore,
        Neo4jMicrosoftMemory,
        create_memory_tools,
        record_agent_trace,
    )
    from agent_framework.azure import AzureOpenAIResponsesClient

    async with MemoryClient(settings) as client:
        # Create context provider for memory injection
        provider = Neo4jContextProvider(
            memory_client=client,
            session_id="user-123",
        )

        # Create chat history provider for persistent history
        history = Neo4jChatMessageStore(
            memory_client=client,
            session_id="user-123",
        )

        # Create agent with Neo4j memory
        agent = chat_client.as_agent(
            instructions="You are a helpful assistant.",
            context_providers=[provider, history],
        )

        # Or use the unified interface
        memory = Neo4jMicrosoftMemory.from_memory_client(
            memory_client=client,
            session_id="user-123",
        )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model as _passthrough,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm import LLMProvider

# Target API version - document for compatibility tracking.
#
# The 1.x GA line renamed the two provider base classes this adapter extends
# (`BaseContextProvider` -> `ContextProvider`, `BaseHistoryProvider` ->
# `HistoryProvider`); the keyword-only `before_run` / `after_run` signatures are
# unchanged from the 1.0.0b2 preview. 1.13 is the floor because it is the first
# GA release carrying the renamed names that we pin against in `pyproject.toml`.
MICROSOFT_AGENT_FRAMEWORK_VERSION = "1.18.0"
MICROSOFT_AGENT_FRAMEWORK_MIN_VERSION = "1.13.0"


def llm_provider_from_microsoft_agent(model: Any) -> LLMProvider:
    """Translate a Microsoft Agent Framework chat client into an :class:`LLMProvider`.

    The Agent Framework wraps Azure OpenAI and OpenAI clients. The
    underlying client typically exposes ``deployment_name`` (Azure) or
    ``model`` (OpenAI). The shared introspector reads either.
    """
    return _passthrough(model)


try:
    from .chat_store import Neo4jChatMessageStore
    from .context_provider import Neo4jContextProvider
    from .gds import GDSAlgorithm, GDSConfig, GDSIntegration
    from .memory import Neo4jMicrosoftMemory
    from .tools import create_memory_tools, execute_memory_tool
    from .tracing import format_traces_for_prompt, get_similar_traces, record_agent_trace

    __all__ = [
        # Core components
        "Neo4jContextProvider",
        "Neo4jChatMessageStore",
        "Neo4jMicrosoftMemory",
        # Tools
        "create_memory_tools",
        "execute_memory_tool",
        # Tracing
        "record_agent_trace",
        "get_similar_traces",
        "format_traces_for_prompt",
        # GDS
        "GDSConfig",
        "GDSAlgorithm",
        "GDSIntegration",
        # Version info
        "MICROSOFT_AGENT_FRAMEWORK_VERSION",
        "MICROSOFT_AGENT_FRAMEWORK_MIN_VERSION",
        # Pass-through
        "llm_provider_from_microsoft_agent",
    ]
except ImportError as e:
    # Microsoft Agent Framework not installed
    import warnings

    warnings.warn(
        f"Microsoft Agent Framework integration requires the 'agent-framework-core' "
        f"package at version {MICROSOFT_AGENT_FRAMEWORK_MIN_VERSION} or newer. "
        f"Install with: pip install neo4j-agent-memory[microsoft-agent] "
        f"(Import error: {e})",
        ImportWarning,
        stacklevel=2,
    )
    __all__ = [
        "MICROSOFT_AGENT_FRAMEWORK_VERSION",
        "MICROSOFT_AGENT_FRAMEWORK_MIN_VERSION",
        "llm_provider_from_microsoft_agent",
    ]
