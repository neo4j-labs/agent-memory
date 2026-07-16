"""LangChain integration for neo4j-agent-memory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model as _passthrough,
)
from neo4j_agent_memory.integrations.langchain.memory import Neo4jAgentMemory

if TYPE_CHECKING:
    from neo4j_agent_memory.llm import LLMProvider


def llm_provider_from_langchain(model: Any) -> LLMProvider:
    """Translate a LangChain ``BaseChatModel`` into an :class:`LLMProvider`.

    Lets users pass through their already-configured LangChain model::

        from langchain_anthropic import ChatAnthropic
        from neo4j_agent_memory.integrations.langchain import (
            llm_provider_from_langchain,
        )

        chat = ChatAnthropic(model_name="claude-3-5-sonnet-latest")
        provider = llm_provider_from_langchain(chat)
        # Wire provider into MemorySettings(llm=provider) or
        # MemoryClient(... llm_provider=provider)

    LangChain models expose ``model_name`` and provider-specific API-key
    attributes (``anthropic_api_key`` / ``openai_api_key``) which the
    shared introspector reads. Class names like ``ChatAnthropic`` /
    ``ChatOpenAI`` provide the provider prefix when the discovered
    ``model_name`` is not already prefixed.
    """
    return _passthrough(model)


try:
    from neo4j_agent_memory.integrations.langchain.retriever import Neo4jMemoryRetriever

    __all__ = [
        "Neo4jAgentMemory",
        "Neo4jMemoryRetriever",
        "llm_provider_from_langchain",
    ]
except ImportError:
    # langchain_core not installed for retriever
    __all__ = [
        "Neo4jAgentMemory",
        "llm_provider_from_langchain",
    ]
