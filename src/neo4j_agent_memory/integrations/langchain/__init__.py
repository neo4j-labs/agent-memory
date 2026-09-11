"""LangChain 1.x integration for neo4j-agent-memory.

Three adapters, each implementing a contract that exists in the installed
LangChain, plus a pass-through helper:

``Neo4jAgentMemory``
    ``langchain_core.chat_history.BaseChatMessageHistory`` — drop it into
    ``RunnableWithMessageHistory`` (or call its context-assembly methods
    directly). Needs ``langchain-core``: ``pip install
    neo4j-agent-memory[langchain]``.
``Neo4jMemoryRetriever``
    ``langchain_core.retrievers.BaseRetriever`` over all three memory layers.
    Needs ``langchain-core``.
``Neo4jMemoryMiddleware``
    ``langchain.agents.middleware.AgentMiddleware`` for
    :func:`langchain.agents.create_agent`. Needs the ``langchain``
    distribution: ``pip install neo4j-agent-memory[langchain-agents]``.
``llm_provider_from_langchain``
    Reuse an already-configured LangChain chat model for memory's own LLM work.
    No LangChain import required.

Nothing here targets ``langchain_core.memory.BaseMemory`` or
``langchain.chains.ConversationChain``: langchain-core 1.x dropped the former
and moved the latter to ``langchain-classic``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model as _passthrough,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.llm import LLMProvider

#: Minimum langchain-core the adapters are written against (1.x ABCs).
LANGCHAIN_CORE_MIN_VERSION = "1.0"
#: Minimum `langchain` distribution for `Neo4jMemoryMiddleware`.
LANGCHAIN_MIN_VERSION = "1.0"


def llm_provider_from_langchain(model: Any) -> LLMProvider:
    """Translate a LangChain ``BaseChatModel`` into an :class:`LLMProvider`.

    Lets users pass through their already-configured LangChain model::

        import os

        from langchain_openai import ChatOpenAI
        from neo4j_agent_memory.integrations.langchain import (
            llm_provider_from_langchain,
        )

        chat = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-5-mini"))
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


__all__ = [
    "LANGCHAIN_CORE_MIN_VERSION",
    "LANGCHAIN_MIN_VERSION",
    "llm_provider_from_langchain",
]

try:  # langchain-core: the chat-history and retriever adapters
    from neo4j_agent_memory.integrations.langchain.memory import Neo4jAgentMemory
    from neo4j_agent_memory.integrations.langchain.retriever import Neo4jMemoryRetriever

    __all__ += ["Neo4jAgentMemory", "Neo4jMemoryRetriever"]
except ImportError:  # pragma: no cover - depends on the installed extras
    pass

try:  # `langchain` (create_agent + middleware): the agent middleware
    from neo4j_agent_memory.integrations.langchain.middleware import Neo4jMemoryMiddleware

    __all__ += ["Neo4jMemoryMiddleware"]
except ImportError:  # pragma: no cover - depends on the installed extras
    pass
