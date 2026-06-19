"""Strands Agents SDK integration for neo4j-agent-memory.

This module provides tools for integrating Neo4j Agent Memory with AWS
Strands Agents SDK, enabling agents to use Context Graphs for semantic
memory and knowledge graph operations.

Example:
    from strands import Agent
    from neo4j_agent_memory.integrations.strands import context_graph_tools

    tools = context_graph_tools(
        neo4j_uri=os.environ["NEO4J_URI"],
        neo4j_password=os.environ["NEO4J_PASSWORD"],
        embedding_provider="bedrock",
    )

    agent = Agent(
        model="anthropic.claude-sonnet-4-20250514-v1:0",
        tools=tools,
    )

    response = agent("What do you know about our project?")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model as _passthrough,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm import LLMProvider


def llm_provider_from_strands(model: Any) -> LLMProvider:
    """Translate a Strands Agents model into an :class:`LLMProvider`.

    Strands typically uses Bedrock model identifier strings (e.g.
    ``"anthropic.claude-sonnet-4-20250514-v1:0"``). Strings without a
    provider prefix are routed to the ``bedrock/`` provider; objects are
    introspected via the shared helper.
    """
    if isinstance(model, str):
        from neo4j_agent_memory.llm import from_provider

        model_id = model if "/" in model else f"bedrock/{model}"
        # from_provider returns LLMProvider | EmbeddingProvider (not overloaded);
        # kind defaults to "llm", so the result is an LLMProvider here.
        return cast("LLMProvider", from_provider(model_id))
    # _passthrough introspects an arbitrary framework model object (returns Any).
    return cast("LLMProvider", _passthrough(model))


try:
    from neo4j_agent_memory.integrations.strands.config import (
        BEDROCK_EMBEDDING_MODELS,
        BEDROCK_LLM_MODELS,
        StrandsConfig,
    )
    from neo4j_agent_memory.integrations.strands.session_manager import (
        Neo4jRetrievalConfig,
        Neo4jSessionManager,
    )
    from neo4j_agent_memory.integrations.strands.tools import (
        clear_client_cache,
        context_graph_tools,
        nams_context_graph_tools,
    )

    __all__ = [
        "context_graph_tools",
        "nams_context_graph_tools",
        "clear_client_cache",
        "StrandsConfig",
        "BEDROCK_EMBEDDING_MODELS",
        "BEDROCK_LLM_MODELS",
        "llm_provider_from_strands",
        "Neo4jSessionManager",
        "Neo4jRetrievalConfig",
    ]
except ImportError:
    # strands-agents not installed
    __all__ = ["llm_provider_from_strands"]
