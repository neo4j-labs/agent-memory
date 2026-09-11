#!/usr/bin/env python3
"""PydanticAI + `neo4j-agent-memory`: an agent that remembers, end to end.

Six numbered sections, each printing its own banner:

    1. Seed long-term memory — the preferences a returning user already has
    2. `MemoryDependency` — context injection, add/search preferences
    3. `create_memory_tools()` — the three tools the model can call itself
    4. Turn 1 — run the agent, persist the exchange, record a reasoning trace
    5. Turn 2 — prove the second turn sees the first through memory
    6. Recap — what the two turns left in the graph

Written against PydanticAI 2.x (`pydantic-ai-slim>=2.0,<3`, installed by the
`[pydantic-ai]` extra): the agent takes a *model instance*, the memory tools are
registered on the agent, the dynamic prompt uses the `instructions` hook, and the
run result exposes `.output` (`.data` was removed in 1.0).

Runs with or without an API key:

* with `OPENAI_API_KEY` — a real `OpenAIChatModel` answers, and the same model
  instance is handed to memory for entity extraction via
  `llm_provider_from_pydantic_ai()`, so credentials are configured once
* without a key — `pydantic_ai.models.test.TestModel` answers offline (canned
  output, tools called with placeholder arguments) and a local
  sentence-transformers embedder replaces OpenAI embeddings. The graph writes,
  the reasoning trace and the memory read-back are all real.

Bolt-only: it pins `BoltSettings` so exporting `MEMORY_API_KEY` cannot silently
retarget the hosted backend. For the hosted path use `nams_memory_tools()` (the
NAMS counterpart of `create_memory_tools()`) — see `examples/nams-quickstart/`.

Requirements:
    - A Neo4j to talk to: `make neo4j-start` (or set NEO4J_URI / NEO4J_PASSWORD)
    - `uv sync --extra pydantic-ai --extra sentence-transformers` (keyless), or
      add `--extra openai` plus OPENAI_API_KEY for the OpenAI path

Configuration comes from `examples/.env` — copy `examples/.env.example`.

Run:
    uv run python examples/pydantic_ai_agent.py
"""

from __future__ import annotations

import os
from typing import Any, cast

from _env import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USERNAME, OPENAI_API_KEY
from pydantic import SecretStr

from neo4j_agent_memory import (
    ExtractionConfig,
    ExtractorType,
    MemoryClient,
    Neo4jConfig,
    ReasoningMemory,
)
from neo4j_agent_memory.config.settings import BoltSettings
from neo4j_agent_memory.integrations.pydantic_ai import (
    MemoryDependency,
    create_memory_tools,
    llm_provider_from_pydantic_ai,
    record_agent_trace,
)

PYDANTIC_AI_INSTALLED = True
try:
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.agent import AgentRunResult
    from pydantic_ai.models import Model
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.models.test import TestModel
except ImportError:  # pragma: no cover - exercised only without the extra
    PYDANTIC_AI_INSTALLED = False

SESSION_ID = "pydantic-ai-demo"
TOTAL_SECTIONS = 6
FIRST_TURN = "Find me a good restaurant for dinner tonight."
SECOND_TURN = "What did I ask you about a moment ago, and what should I order there?"


def section(number: int, title: str) -> None:
    print(f"\n[{number}/{TOTAL_SECTIONS}] {title}")
    print("-" * 60)


# =====================================================================
# Configuration
# =====================================================================
def build_model() -> tuple[Model, Any]:
    """Return `(model, llm_provider)` for the agent and for memory extraction.

    With a key the *same* `OpenAIChatModel` instance backs both the agent and
    memory's entity extraction. Without one, `TestModel` keeps the whole script
    runnable offline and `None` turns LLM extraction off.
    """
    if OPENAI_API_KEY:
        # In PydanticAI 2.x the `"openai:"` model-string prefix means the
        # Responses API and `"openai-chat:"` means Chat Completions, so a model
        # instance is the unambiguous (and reusable) form.
        model: Model = OpenAIChatModel(os.getenv("OPENAI_MODEL", "gpt-5-mini"))
        print(f"Model: {type(model).__name__}({model.model_name}) | memory extraction: LLM")
        return model, llm_provider_from_pydantic_ai(model)

    # `call_tools` keeps the stub read-only: TestModel would otherwise call
    # every registered tool, including `save_preference`, with placeholder args.
    stub = TestModel(call_tools=["search_memory", "recall_preferences"])
    print("Model: TestModel (no OPENAI_API_KEY — canned output) | memory extraction: off")
    return stub, None


def build_settings(llm_provider: Any) -> BoltSettings | None:
    """Build `BoltSettings`, or return `None` when no embedder is available."""
    if OPENAI_API_KEY:
        embedding_model = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
        extraction = ExtractionConfig(extractor_type=ExtractorType.LLM)
    else:
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            print("ERROR: no embedding provider available. Either:")
            print("  1. set OPENAI_API_KEY (see examples/.env.example), or")
            print("  2. uv sync --extra sentence-transformers")
            return None
        embedding_model = os.getenv(
            "LOCAL_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        extraction = ExtractionConfig(extractor_type=ExtractorType.NONE)
    print(f"Embeddings: {embedding_model}")

    # BoltSettings pins the backend: plain MemorySettings would flip to the
    # hosted NAMS backend whenever MEMORY_API_KEY happens to be exported.
    return BoltSettings(
        neo4j=Neo4jConfig(
            uri=NEO4J_URI,
            username=NEO4J_USERNAME,
            password=SecretStr(NEO4J_PASSWORD),
        ),
        embedding=embedding_model,
        extraction=extraction,
        llm=llm_provider,
    )


# =====================================================================
# The agent
# =====================================================================
def build_agent(model: Model, tools: list[Any]) -> Agent[MemoryDependency, str]:
    """Build the memory-enabled agent: tools registered, instructions dynamic."""
    agent = Agent(
        model,
        deps_type=MemoryDependency,
        tools=tools,
        output_type=str,
    )

    @agent.instructions
    async def memory_instructions(ctx: RunContext[MemoryDependency]) -> str:
        # `instructions` is the 2.x dynamic-prompt hook (it replaced
        # `system_prompt`) and is re-rendered for every model request, so the
        # injected context tracks the conversation instead of being frozen at
        # construction time. `ctx.prompt` is this run's user message, which is
        # what makes the retrieval query turn-specific.
        context = await ctx.deps.get_context(str(ctx.prompt))
        base = (
            "You are a restaurant recommendation assistant. Use the memory "
            "tools to look up or save what you learn about the user."
        )
        if context:
            return f"{base}\n\nContext from memory:\n{context}"
        return base

    return agent


async def run_turn(
    memory: MemoryClient,
    agent: Agent[MemoryDependency, str],
    deps: MemoryDependency,
    user_input: str,
    *,
    task: str,
) -> AgentRunResult[str]:
    """Run one turn and leave all three memory layers updated."""
    print(f"user: {user_input}")
    result = await agent.run(user_input, deps=deps)
    # 2.x: `.output` (`.data` was removed); `.usage` is a property.
    print(f"agent: {result.output}")

    # Short-term memory: persist both sides of the exchange so the next turn
    # (and any later session) can retrieve it.
    await deps.save_interaction(user_input, str(result.output))

    # Reasoning memory: turn the finished run into a trace. Every tool call the
    # model made becomes a step with a ToolCall node attached.
    # The cast is a typing-only detail: `record_agent_trace` is annotated against
    # the concrete bolt `ReasoningMemory`, while a protocol-typed `MemoryClient`
    # exposes `reasoning` as `ReasoningProtocol`. Both satisfy it at runtime.
    trace = await record_agent_trace(
        cast("ReasoningMemory", memory.reasoning),
        session_id=deps.session_id,
        result=result,
        task=task,
    )

    # Read it back so the graph write is visible rather than assumed.
    stored = await memory.reasoning.get_trace_with_steps(trace.id)
    if stored is None:
        print(f"trace {trace.id}: not found on read-back")
    else:
        tool_calls = sum(len(step.tool_calls) for step in stored.steps)
        print(
            f"trace {stored.id}: {len(stored.steps)} step(s), "
            f"{tool_calls} tool call(s), success={stored.success}"
        )
        for step in stored.steps:
            names = ", ".join(call.tool_name for call in step.tool_calls) or "(no tool)"
            print(f"   step {step.step_number}: {names}")
    return result


# =====================================================================
# Sections
# =====================================================================
async def seed_long_term_memory(memory: MemoryClient) -> None:
    section(1, "Long-term memory: the preferences a returning user already has")
    for category, preference in (
        ("communication", "Prefers concise responses"),
        ("food", "Vegetarian, loves Indian cuisine"),
    ):
        await memory.long_term.add_preference(category, preference)
        print(f"   [{category}] {preference}")


async def memory_dependency(memory: MemoryClient) -> MemoryDependency:
    section(2, "MemoryDependency: context injection and preference writes")
    deps = MemoryDependency(client=memory, session_id=SESSION_ID)

    context = await deps.get_context("restaurant recommendation")
    print("Context the agent would receive:")
    print(context if context else "(no relevant context found)")

    await deps.add_preference(
        category="location",
        preference="Prefers restaurants in downtown area",
    )
    print("\nAdded a location preference through the dependency")

    prefs = await deps.search_preferences("food")
    print(f"Found {len(prefs)} food-related preference(s):")
    for pref in prefs:
        print(f"   [{pref['category']}] {pref['preference']}")
    return deps


async def memory_tools(memory: MemoryClient) -> list[Any]:
    section(3, "create_memory_tools(): the tools the model can call itself")
    tools = create_memory_tools(memory)
    # Look tools up by name, never by list position: the order is an
    # implementation detail of the library, the names are the contract.
    by_name = {getattr(tool, "__name__", ""): tool for tool in tools}
    print(f"Created {len(tools)} tools: {', '.join(sorted(by_name))}")

    print("\nsearch_memory('vegetarian food'):")
    print(await by_name["search_memory"]("vegetarian food"))

    print("\nsave_preference('cuisine', ...):")
    print(await by_name["save_preference"]("cuisine", "Also enjoys Mediterranean food"))

    print("\nrecall_preferences('food'):")
    print(await by_name["recall_preferences"]("food"))
    return tools


async def second_turn_sees_the_first(
    memory: MemoryClient,
    agent: Agent[MemoryDependency, str],
    deps: MemoryDependency,
) -> None:
    section(5, "Turn 2: the injected context now contains turn 1")
    context = await memory.get_context(SECOND_TURN, session_id=SESSION_ID)
    first_turn_recalled = FIRST_TURN.rstrip(".") in context
    print(f"Turn 1 present in the retrieved context: {first_turn_recalled}")
    print(context if context else "(no relevant context found)")
    print()
    await run_turn(memory, agent, deps, SECOND_TURN, task="Follow-up on the recommendation")


async def recap(memory: MemoryClient) -> None:
    section(6, "Recap: what the two turns left in the graph")
    conversation = await memory.short_term.get_conversation(SESSION_ID)
    messages = conversation.messages
    print(f"Messages in session {SESSION_ID!r}: {len(messages)}")
    for message in messages[-4:]:
        print(f"   [{message.role.value}] {message.content[:70]}")

    traces = await memory.reasoning.get_session_traces(SESSION_ID)
    print(f"Reasoning traces for this session: {len(traces)}")
    for trace in traces:
        print(f"   {trace.task} (success={trace.success})")


# =====================================================================
# Entry point
# =====================================================================
async def main() -> None:
    print("=" * 60)
    print("Neo4j Agent Memory — PydanticAI integration")
    print("=" * 60)

    if not PYDANTIC_AI_INSTALLED:
        print("ERROR: PydanticAI is not installed. Install it with:")
        print("  uv sync --extra pydantic-ai    # or: pip install 'pydantic-ai>=2,<3'")
        return

    model, llm_provider = build_model()
    settings = build_settings(llm_provider)
    if settings is None:
        return

    async with MemoryClient(settings) as memory:
        await seed_long_term_memory(memory)
        deps = await memory_dependency(memory)
        tools = await memory_tools(memory)

        section(4, "Turn 1: run the agent, persist the exchange, record the trace")
        agent = build_agent(model, tools)
        await run_turn(memory, agent, deps, FIRST_TURN, task="Find restaurant recommendation")

        await second_turn_sees_the_first(memory, agent, deps)
        await recap(memory)

    print("\nDemo complete!")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
