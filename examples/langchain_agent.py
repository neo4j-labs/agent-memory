#!/usr/bin/env python3
"""LangChain 1.x agent with a Neo4j context graph for memory (bolt backend).

Five numbered sections, in the order you would build them:

    1. Model and settings — one chat model drives the agent *and* memory's own
       entity extraction (``llm_provider_from_langchain``)
    2. Seed memory — a preference and a POLE+O entity to recall later
    3. The agent — ``create_agent(model, tools, middleware=[...])`` with
       ``Neo4jMemoryMiddleware`` injecting context and persisting both turns
    4. Reasoning memory — a second middleware records the run as a trace
    5. Retrieval — ``Neo4jMemoryRetriever`` directly, and as an agent tool

LangChain 1.0 retired ``BaseMemory`` / ``ConversationChain`` / ``AgentExecutor``
(they live in ``langchain-classic`` now). The supported shapes are
``create_agent`` middleware for context injection and ``BaseChatMessageHistory``
for thread history — both of which this library ships as adapters. This example
uses the middleware; ``Neo4jAgentMemory`` (the ``BaseChatMessageHistory``
adapter) is shown in the paired how-to.

Runs with no API key: without ``OPENAI_API_KEY`` the script falls back to a
scripted ``FakeListChatModel`` and a local sentence-transformers embedder, so
the whole memory path still executes end to end. Tool calling is the one thing
the fake model cannot do, so the agent is built without tools in that mode and
the retriever tool is invoked directly instead.

Requirements:
    - A Neo4j to talk to: `make neo4j-start` (or set NEO4J_URI / NEO4J_PASSWORD)
    - `uv sync --extra langchain-agents --extra sentence-transformers` for the
      offline path, i.e.
      `pip install "neo4j-agent-memory[langchain-agents,sentence-transformers]"`
    - For a real model turn, add `langchain-openai` and OPENAI_API_KEY:
      `pip install "neo4j-agent-memory[langchain-agents,openai]" langchain-openai`

Configuration comes from `examples/.env` — copy `examples/.env.example`.

Run:
    uv run python examples/langchain_agent.py

The hosted-backend twin of this script is `examples/nams-langchain/`; the paired
how-to is `docs/.../how-to/integrations/langchain.adoc`.

Verified against neo4j-agent-memory 0.6.0-dev, langchain 1.4.0,
langchain-core 1.6.2, langgraph 1.2.11 — 2026-09-10.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from _env import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USERNAME, OPENAI_API_KEY
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from langchain_core.tools.retriever import create_retriever_tool
from pydantic import SecretStr

from neo4j_agent_memory import (
    BoltSettings,
    ExtractionConfig,
    ExtractorType,
    MemoryClient,
    Neo4jConfig,
)
from neo4j_agent_memory.integrations.langchain import (
    Neo4jMemoryMiddleware,
    Neo4jMemoryRetriever,
    llm_provider_from_langchain,
)
from neo4j_agent_memory.schema.models import TraceOutcome

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.runtime import Runtime

SESSION_ID = "langchain-demo"
TOTAL_SECTIONS = 5

#: What the offline fake model "answers" with. One response per model call.
OFFLINE_RESPONSES = [
    "Thai Kitchen — you told me you like spicy food, and it is your saved favourite.",
]


def section(number: int, title: str) -> None:
    print(f"\n[{number}/{TOTAL_SECTIONS}] {title}")
    print("-" * 60)


# =====================================================================
# 1. MODEL AND SETTINGS
# =====================================================================
def build_model() -> tuple[BaseChatModel, bool]:
    """Return the chat model and whether it can call tools.

    A real provider model gets tools; the offline fake does not implement
    ``bind_tools``, so the agent is built without them in that mode.
    """
    if OPENAI_API_KEY:
        try:
            # langchain-openai is an example-only dependency, not a project dep.
            from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
        except ImportError:
            print(
                "OPENAI_API_KEY is set but langchain-openai is not installed "
                "(pip install langchain-openai) — using the offline model."
            )
        else:
            # Model ids go through the environment. `model=` is the current
            # constructor argument; `model_name=` is a legacy alias. Temperature
            # is left at the provider default: the GPT-5 family rejects
            # non-default values.
            model_id = os.getenv("OPENAI_MODEL", "gpt-5-mini")
            print(f"Model: ChatOpenAI({model_id!r}) — real model turn, tools enabled")
            return ChatOpenAI(model=model_id), True

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    print("Model: FakeListChatModel — no OPENAI_API_KEY, running the scripted offline path")
    return FakeListChatModel(responses=OFFLINE_RESPONSES), False


def build_settings(*, model: BaseChatModel, use_openai: bool) -> BoltSettings:
    """Bolt-pinned settings. `BoltSettings` cannot silently flip to NAMS.

    For the hosted backend see `examples/nams-langchain/`, which runs this same
    middleware against NAMS with `NamsSettings`.
    """
    neo4j = Neo4jConfig(
        uri=NEO4J_URI,
        username=NEO4J_USERNAME,
        password=SecretStr(NEO4J_PASSWORD),
    )

    if use_openai:
        # One model, declared once: `llm_provider_from_langchain` hands the
        # agent's own ChatOpenAI to memory for entity extraction, so the agent
        # and the memory layer never drift onto different models.
        return BoltSettings(
            neo4j=neo4j,
            embedding="openai/text-embedding-3-small",
            llm=llm_provider_from_langchain(model),
        )

    # Offline: no LLM client is ever constructed, embeddings stay local, and
    # extraction is a no-op (`enable_llm_fallback=False` is required with
    # `llm=None`).
    return BoltSettings(
        neo4j=neo4j,
        llm=None,
        embedding="sentence-transformers/all-MiniLM-L6-v2",
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.PIPELINE,
            enable_spacy=False,
            enable_gliner=False,
            enable_llm_fallback=False,
        ),
    )


# =====================================================================
# 4. REASONING MEMORY — recorded by a second middleware
# =====================================================================
class ReasoningTraceMiddleware(AgentMiddleware[AgentState[Any], Any, Any]):
    """Write each agent run into reasoning memory.

    `Neo4jMemoryMiddleware` covers short-term and long-term memory; the
    reasoning layer needs the agent's own step structure, which only the
    middleware hooks can see. Async hooks only — memory is async all the way
    down, so invoke the agent with `ainvoke`/`astream`.
    """

    def __init__(self, memory_client: MemoryClient[Any, Any, Any], session_id: str) -> None:
        super().__init__()
        self.memory_client = memory_client
        self.session_id = session_id
        self.tools: list[BaseTool] = []
        self.trace_id: Any | None = None
        self.model_calls = 0

    async def abefore_agent(self, state: AgentState[Any], runtime: Runtime[Any]) -> None:
        """Open a trace whose task is the user's request."""
        task = next(
            (m.text for m in reversed(state["messages"]) if isinstance(m, HumanMessage) and m.text),
            "(no user message)",
        )
        trace = await self.memory_client.reasoning.start_trace(self.session_id, task)
        self.trace_id = trace.id
        self.model_calls = 0

    async def aafter_model(self, state: AgentState[Any], runtime: Runtime[Any]) -> None:
        """One reasoning step per model call, plus any tool calls it requested."""
        last = state["messages"][-1]
        if self.trace_id is None or not isinstance(last, AIMessage):
            return
        self.model_calls += 1
        step = await self.memory_client.reasoning.add_step(
            self.trace_id,
            thought=last.text or "(tool call only)",
            action=", ".join(call["name"] for call in last.tool_calls) or "respond",
        )
        for call in last.tool_calls:
            await self.memory_client.reasoning.record_tool_call(
                step.id,
                call["name"],
                call["args"],
            )

    async def aafter_agent(self, state: AgentState[Any], runtime: Runtime[Any]) -> None:
        """Close the trace with a structured, indexable outcome."""
        if self.trace_id is None:
            return
        last = state["messages"][-1]
        await self.memory_client.reasoning.complete_trace(
            self.trace_id,
            outcome=TraceOutcome(
                success=True,
                summary=(last.text or "")[:200],
                metrics={"model_calls": float(self.model_calls)},
            ),
        )
        # Link the trace to the message that triggered it. The memory
        # middleware persisted that message during this run, so the id only
        # exists now — `start_trace(triggered_by_message_id=...)` is the other
        # option when you store the user turn yourself.
        conversation = await self.memory_client.short_term.get_conversation(self.session_id)
        user_messages = [m for m in conversation.messages if m.role.value == "user"]
        if user_messages:
            await self.memory_client.reasoning.link_trace_to_message(
                self.trace_id, user_messages[-1].id
            )


# =====================================================================
# 5. RETRIEVAL — the retriever, and the retriever as a tool
# =====================================================================
def build_tools(
    client: MemoryClient[Any, Any, Any], retriever: Neo4jMemoryRetriever
) -> list[BaseTool]:
    """A retriever tool plus a write tool, both plain LangChain tools."""

    search_memory = create_retriever_tool(
        retriever,
        "search_memory",
        "Search the user's memory: past messages, known entities, saved preferences.",
    )

    @tool
    async def save_preference(category: str, preference: str) -> str:
        """Record a preference the user just expressed (category: food, budget, ...)."""
        await client.long_term.add_preference(category, preference)
        return f"Saved preference: {category} — {preference}"

    return [search_memory, save_preference]


async def main() -> None:
    print("=" * 60)
    print("Neo4j Agent Memory — LangChain 1.x agent")
    print("=" * 60)

    section(1, "Model and settings")
    model, supports_tools = build_model()
    # `supports_tools` is True exactly when a real provider model was built, so
    # it also decides whether memory reuses it for extraction.
    settings = build_settings(model=model, use_openai=supports_tools)

    async with MemoryClient(settings) as client:
        # -------------------------------------------------------------
        section(2, "Seed memory: a preference and an entity to recall")
        # Re-runnable: start each run from a clean demo conversation so the
        # transcript below always shows exactly one exchange.
        await client.short_term.clear_session(SESSION_ID)
        await client.long_term.add_preference(
            "food", "Loves spicy dishes", context="Dining preferences"
        )
        # `add_entity` returns (entity, dedup_result) — see enrichment_example.py.
        # Type and subtype become PascalCase node labels *when the subtype is a
        # known POLE+O subtype*. RESTAURANT is not one of ORGANIZATION's, so
        # this node gets labels (:Entity:Organization), keeps
        # subtype="RESTAURANT" as a property, and gets no :Restaurant label.
        # Use subtype="COMPANY" if you want the third label.
        entity, _ = await client.long_term.add_entity(
            name="Thai Kitchen",
            entity_type="ORGANIZATION",
            subtype="RESTAURANT",
            description="Favourite Thai restaurant",
        )
        print(f"Preference stored; entity {entity.name!r} ({entity.full_type}) stored")

        # -------------------------------------------------------------
        section(3, "The agent: create_agent + Neo4jMemoryMiddleware")
        retriever = Neo4jMemoryRetriever(
            memory_client=client,
            session_id=SESSION_ID,  # message search is conversation-scoped on NAMS
            search_short_term=True,
            search_long_term=True,
            search_reasoning=False,  # nothing to find until section 4 has run
            k=5,
        )
        tools = build_tools(client, retriever)

        memory_middleware = Neo4jMemoryMiddleware(
            client,
            session_id=SESSION_ID,
            # Reasoning stays out of the injected block on a first run: there
            # are no traces to match yet, so the variable would always be empty.
            include_reasoning=False,
            max_items=5,
        )
        trace_middleware = ReasoningTraceMiddleware(client, SESSION_ID)

        agent = create_agent(
            model,
            # The fake offline model cannot bind tools; a real model gets them.
            tools=tools if supports_tools else [],
            system_prompt="You are a concise dining assistant. Use the user's saved memory.",
            middleware=[memory_middleware, trace_middleware],
        )
        if not supports_tools:
            print("(offline model: agent built without tools — see section 5)")

        # `ainvoke`, not `invoke`: the middleware implements the async hooks.
        question = "Where should I eat tonight? I want something spicy."
        result = await agent.ainvoke({"messages": [HumanMessage(content=question)]})
        print(f"\nUser:      {question}")
        print(f"Assistant: {result['messages'][-1].text}")

        conversation = await client.short_term.get_conversation(SESSION_ID)
        print(f"\nMemory now holds {len(conversation.messages)} messages for {SESSION_ID!r}:")
        for message in conversation.messages[-4:]:
            print(f"   {message.role.value:>9}: {message.content[:60]}")

        # -------------------------------------------------------------
        section(4, "Reasoning memory: the run as a trace")
        trace = (
            None
            if trace_middleware.trace_id is None
            else await client.reasoning.get_trace(trace_middleware.trace_id)
        )
        if trace is None:
            print("No trace recorded.")
        else:
            print(f"Trace {trace.id}")
            print(f"   task:    {trace.task}")
            print(f"   success: {trace.success}")
            print(f"   steps:   {len(trace.steps)}")
            for step in trace.steps:
                print(f"     - {step.action}: {(step.thought or '')[:60]}")

        # -------------------------------------------------------------
        section(5, "Retrieval: Neo4jMemoryRetriever, and the same retriever as a tool")
        docs = await retriever.ainvoke("spicy food preferences")
        print(f"retriever.ainvoke() returned {len(docs)} documents:")
        for doc in docs:
            print(f"   [{doc.metadata.get('type')}] {doc.page_content[:70]}")

        # A LangChain tool is invokable on its own — handy offline, and the
        # quickest way to see what the agent would receive.
        search_memory, save_preference = tools
        # `BaseTool.ainvoke` takes the tool's argument dict.
        search_args: dict[str, Any] = {"query": "spicy food preferences"}
        tool_output = await search_memory.ainvoke(search_args)
        print(f"\nsearch_memory tool returned {len(str(tool_output))} chars of context")
        save_args: dict[str, Any] = {"category": "spice", "preference": "Extra hot"}
        print(await save_preference.ainvoke(save_args))

        print("\nDone. Paired how-to: docs how-to/integrations/langchain.adoc")
        print("Hosted (NAMS) variant of this agent: examples/nams-langchain/")


if __name__ == "__main__":
    asyncio.run(main())
