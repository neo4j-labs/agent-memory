"""NAMS + LangChain 1.x — the same agent middleware, hosted backend, no database.

This is `examples/langchain_agent.py` with one thing changed: the settings. The
agent, the middleware and the retriever are identical; only
``NamsSettings()`` replaces ``BoltSettings(neo4j=...)``.

What the hosted service does for you:

* **Entity extraction** runs server-side — no extractor, no LLM key for memory.
* **Embeddings** are generated server-side — no embedding provider to configure.
* **The graph** is operated for you — no Neo4j to run.

What it does not do (today), and how the adapters behave:

* ``long_term.search_preferences`` → ``NotSupportedError``; the adapters return
  an empty preference list instead of raising.
* ``reasoning.get_similar_traces`` → ``NotSupportedError``; similar-task context
  comes back empty, so this example keeps ``include_reasoning=False``.
* ``long_term.get_context`` returns ``""``; the adapters fall back to
  ``search_entities``, which *is* supported.
* ``short_term.search_messages`` is conversation-scoped — always pass
  ``session_id=`` to the retriever.

Run:

    cp .env.example .env     # set MEMORY_API_KEY
    uv pip install -r requirements.txt
    uv run python main.py

Without ``OPENAI_API_KEY`` the script uses a scripted offline chat model, so the
full memory path still runs against NAMS with only a ``MEMORY_API_KEY``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from neo4j_agent_memory import MemoryClient, NamsSettings
from neo4j_agent_memory.integrations.langchain import (
    Neo4jMemoryMiddleware,
    Neo4jMemoryRetriever,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

CONVERSATION_NAME = "nams-langchain-demo"

#: One scripted answer per model call, used when there is no OPENAI_API_KEY.
OFFLINE_RESPONSES = [
    "Noted — dark mode everywhere, and I'll keep using it in future answers.",
]


def load_env() -> None:
    """Load this directory's ``.env`` so the documented setup step has an effect."""
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional; parse the file ourselves
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    else:
        load_dotenv(env_file)
    print(f"Loaded environment from {env_file}")


def build_model() -> BaseChatModel:
    """A real chat model when OPENAI_API_KEY is set, else a scripted fake."""
    if os.environ.get("OPENAI_API_KEY"):
        try:
            # langchain-openai is an example dependency (see requirements.txt).
            from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
        except ImportError:
            print("OPENAI_API_KEY set but langchain-openai is missing — using the offline model.")
        else:
            model_id = os.getenv("OPENAI_MODEL", "gpt-5-mini")
            print(f"Model: ChatOpenAI({model_id!r})")
            openai_model: BaseChatModel = ChatOpenAI(model=model_id)
            return openai_model

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    print("Model: FakeListChatModel (no OPENAI_API_KEY) — memory still runs against NAMS")
    return FakeListChatModel(responses=OFFLINE_RESPONSES)


async def main() -> None:
    load_env()

    if not os.environ.get("MEMORY_API_KEY"):
        raise SystemExit(
            "Set MEMORY_API_KEY to your NAMS API key. Sign up at https://memory.neo4jlabs.com."
        )

    # Reads MEMORY_API_KEY, and optionally MEMORY_ENDPOINT / MEMORY_WORKSPACE_ID.
    # NamsSettings is the hosted-backend twin of BoltSettings: no Neo4j URI, no
    # embedding provider, no LLM — the service supplies all three.
    settings = NamsSettings()

    async with MemoryClient(settings) as client:
        print(f"Connected to {settings.nams.endpoint}")

        # NAMS mints conversation ids server-side, so create first and use the
        # id it returns as the session id everywhere below.
        conversation = await client.short_term.create_conversation(CONVERSATION_NAME)
        session_id = str(conversation.id)
        print(f"Conversation: {session_id}")

        model = build_model()
        agent = create_agent(
            model,
            tools=[],  # see examples/langchain_agent.py for memory-backed tools
            system_prompt="You are a concise assistant. Use what you remember about the user.",
            middleware=[
                Neo4jMemoryMiddleware(
                    client,
                    session_id=session_id,
                    include_long_term=True,  # entities: supported on NAMS
                    include_reasoning=False,  # trace search: bolt-only
                    max_items=5,
                )
            ],
        )

        question = "I prefer dark mode in all my apps. Remember that."
        # `ainvoke`: the middleware implements the async hooks only.
        result = await agent.ainvoke({"messages": [HumanMessage(content=question)]})
        print(f"\nUser:      {question}")
        print(f"Assistant: {result['messages'][-1].text}")

        # Read back from the server so a silent write failure is visible.
        stored = await client.short_term.get_conversation(session_id)
        print(f"\n{len(stored.messages)} messages persisted on NAMS:")
        for message in stored.messages:
            print(f"   {message.role.value:>9}: {message.content[:60]}")

        # Server-side extraction is asynchronous, so poll for it before reading
        # entities back. This is the long-term half the hosted backend supports.
        extracted = await client.long_term.wait_for_extraction(
            query="dark mode", session_id=session_id, timeout=15.0
        )
        if not extracted:
            print("\nNo entities extracted yet (extraction is asynchronous on NAMS).")
        entities = await client.long_term.search_entities("dark mode", limit=5)
        print(f"\nEntities NAMS extracted server-side: {len(entities)}")
        for entity in entities:
            print(f"   {entity.display_name} ({entity.full_type})")

        # The retriever works the same way; pass session_id because NAMS message
        # search is conversation-scoped.
        retriever = Neo4jMemoryRetriever(
            memory_client=client,
            session_id=session_id,
            search_reasoning=False,  # bolt-only layer
            k=5,
        )
        docs = await retriever.ainvoke("dark mode preference")
        print(f"\nRetriever returned {len(docs)} documents:")
        for doc in docs:
            print(f"   [{doc.metadata.get('type')}] {doc.page_content[:60]}")

        print("\nDone. Same code on your own Neo4j: examples/langchain_agent.py")


if __name__ == "__main__":
    asyncio.run(main())
