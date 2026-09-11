"""Shared-brain demo: two Strands session managers, one graph.

Each agent gets its OWN Neo4jSessionManager (own session, own analyst),
but both write into the SAME Neo4j database — so what agent A's session
contributes to long-term memory is retrievable by agent B.

Runs without any LLM API key: instead of invoking a hosted model we drive
the real Strands hook path — a ``HookRegistry`` the manager registers into,
plus the ``AgentInitializedEvent`` / ``MessageAddedEvent`` /
``AfterInvocationEvent`` dispatches that ``strands.Agent`` performs for you.
With AWS credentials the whole ``_attach`` / ``_dispatch_message`` scaffolding
collapses into one argument::

    Agent(model=bedrock_llm_model(), session_manager=manager)
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from strands.hooks import (
    AfterInvocationEvent,
    AgentInitializedEvent,
    HookRegistry,
    MessageAddedEvent,
)
from strands.types.content import Message, Role

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import ExtractionConfig, ExtractorType
from neo4j_agent_memory.integrations.strands import (
    Neo4jRetrievalConfig,
    Neo4jSessionManager,
)

#: One session (and one analyst) per agent; both share the database.
SESSION_A = "kyc-session"
SESSION_B = "credit-session"
ANALYST_A = "analyst-1"
ANALYST_B = "analyst-2"


def build_settings() -> MemorySettings:
    """Build settings with a local sentence-transformers embedder.

    Notes
    -----
    * ``backend="bolt"`` is pinned explicitly. Without it the backend is
      inferred, and a ``MEMORY_API_KEY`` left in the environment from some
      other example would silently redirect this demo's writes to hosted
      NAMS (where preference search — and therefore half of the injected
      context block below — is unavailable).
    * ``llm=None`` keeps the example runnable without any LLM API key.
    * ``ExtractorType.NONE`` disables entity extraction, so the demo works
      whether or not the spaCy / GLiNER extras are installed. To watch the
      shared brain populate itself instead of being seeded, drop this line,
      install the ``extraction`` extra, and ``await
      client.long_term.wait_for_extraction()`` before reading back.
    * The embedding is set via the v0.3 provider-string shorthand —
      resolves to a local SentenceTransformersProvider (no network calls).
    """
    return MemorySettings(
        backend="bolt",
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "test-password")),
        ),
        llm=None,
        embedding="sentence-transformers/all-MiniLM-L6-v2",
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
    )


def _stand_in_agent(agent_id: str) -> Any:
    """A stand-in for ``strands.Agent``: the manager only touches ``messages``."""
    return SimpleNamespace(messages=[], agent_id=agent_id)


def _attach(manager: Neo4jSessionManager, agent: Any) -> HookRegistry:
    """Register the manager's hooks and fire ``AgentInitializedEvent``.

    This is what ``Agent(session_manager=manager)`` does internally: one
    ``registry.add_hook(manager)`` followed by the initialization dispatch
    that restores persisted history into ``agent.messages``.
    """
    registry = HookRegistry()
    registry.add_hook(manager)
    registry.invoke_callbacks(AgentInitializedEvent(agent=agent))
    return registry


def _dispatch_message(registry: HookRegistry, agent: Any, role: Role, text: str) -> Message:
    """Append a message and dispatch ``MessageAddedEvent``, as the agent loop does.

    The returned dict is the live message object: for a user turn with a
    ``retrieval_config``, the manager's injection hook has already rewritten
    its text block in place (in memory only — the stored copy is the
    original, deep-copied into the write-behind buffer first).
    """
    message: Message = {"role": role, "content": [{"text": text}]}
    agent.messages.append(message)
    registry.invoke_callbacks(MessageAddedEvent(agent=agent, message=message))
    return message


def _dispatch_tool_use(
    registry: HookRegistry, agent: Any, tool_name: str, tool_input: dict[str, Any]
) -> None:
    """Dispatch an assistant turn carrying a ``toolUse`` block.

    With ``record_tool_calls=True`` the manager mirrors the block into
    reasoning memory. The turn itself holds no text, so it is never stored
    as a message — tool use is reasoning, not conversation.
    """
    message: Message = {
        "role": "assistant",
        "content": [{"toolUse": {"toolUseId": "demo-1", "name": tool_name, "input": tool_input}}],
    }
    agent.messages.append(message)
    registry.invoke_callbacks(MessageAddedEvent(agent=agent, message=message))


async def _prepare(settings: MemorySettings) -> UUID:
    """Reset the demo's sessions and seed the shared brain.

    Re-runs would otherwise accumulate history in the demo sessions
    (persistence working as designed) — clearing keeps the printed counts
    matching the README.

    Seeding stands in for extraction: in production NAMS extracts entities
    server-side and the bolt backend runs the extraction pipeline when one
    is configured. This no-API-key demo disables extraction, so we write
    what extraction would have found.

    Returns the seeded organization's id, so the graph read-back below does
    not have to guess which node it got.
    """
    async with MemoryClient(settings) as client:
        for session_id in (SESSION_A, SESSION_B):
            # Deleting a never-created session is a no-op, so anything raised
            # here is a real connection/auth failure worth seeing.
            await client.short_term.clear_session(session_id)

        for identifier, role in ((ANALYST_A, "KYC analyst"), (ANALYST_B, "Credit analyst")):
            await client.users.upsert_user(identifier=identifier, attributes={"role": role})

        acme, _ = await client.long_term.add_entity(
            "Acme Corp",
            "ORGANIZATION",
            subtype="COMPANY",
            description="Company beneficially owned by Jane Doe",
        )
        jane, _ = await client.long_term.add_entity(
            "Jane Doe",
            "PERSON",
            description="Beneficial owner of Acme Corp",
        )
        await client.long_term.add_relationship(jane, acme, "BENEFICIAL_OWNER_OF")

        # Entities are the shared brain: unscoped, every session searches
        # them. Preferences are per-analyst — a manager built with
        # ``user_id=`` lists only that user's preferences, so analyst-1's
        # preference below never reaches analyst-2's context block.
        await client.long_term.add_preference(
            "compliance",
            "Always verify beneficial ownership before credit decisions",
            user_identifier=ANALYST_B,
        )
        await client.long_term.add_preference(
            "workflow",
            "Prefers bullet-point summaries",
            user_identifier=ANALYST_A,
        )
        return UUID(str(acme.id))


async def _report_graph(settings: MemorySettings, entity_id: UUID) -> None:
    """Show the graph the shared brain buys you: one hop off an entity."""
    async with MemoryClient(settings) as client:
        for other, relationship in await client.long_term.get_related_entities(entity_id):
            # relationship.type is the Neo4j edge type (always RELATED_TO);
            # the semantic relation we wrote (BENEFICIAL_OWNER_OF) lives in a
            # property on that edge. See the README's Limitations section.
            print(f"Graph: Acme Corp —[{relationship.type}]— {other.name} ({other.type})")


async def _report_user(settings: MemorySettings, identifier: str) -> None:
    """Read back the :User node the manager's ``user_id=`` linked writes to."""
    async with MemoryClient(settings) as client:
        user = await client.users.get_user(identifier)
        role = (user.attributes or {}).get("role", "?") if user else "missing"
        print(f"Owner of '{SESSION_A}': {identifier} ({role})")


async def _report_tool_trace(settings: MemorySettings, session_id: str) -> None:
    """Read back the reasoning trace ``record_tool_calls=True`` wrote."""
    async with MemoryClient(settings) as client:
        traces = await client.reasoning.get_session_traces(session_id, limit=1)
        if not traces:
            print("(no reasoning trace recorded)")
            return
        trace = await client.reasoning.get_trace_with_steps(traces[0].id)
        if trace is None:
            print("(no reasoning trace recorded)")
            return
        for step in trace.steps:
            for call in step.tool_calls:
                print(f"Reasoning: {trace.task} -> tool call {call.tool_name}({call.arguments})")


def main() -> None:
    # One settings object for every client in the demo — the point of the
    # example is that they all address the same graph. Each manager still
    # builds its own client: the manager owns a loop-bound transport, so an
    # already-connected client cannot be shared between managers.
    settings = build_settings()
    acme_id = asyncio.run(_prepare(settings))
    print("Seeded long-term memory (2 entities, 1 relationship, 2 preferences).")

    # Phase 1: Agent A learns something and persists it.
    with Neo4jSessionManager(SESSION_A, settings=settings, user_id=ANALYST_A) as manager_a:
        agent_a = _stand_in_agent("kyc-agent")
        registry_a = _attach(manager_a, agent_a)
        _dispatch_message(
            registry_a, agent_a, "user", "Jane Doe is the beneficial owner of Acme Corp."
        )
        _dispatch_message(registry_a, agent_a, "assistant", "Recorded the ownership link.")
        registry_a.invoke_callbacks(AfterInvocationEvent(agent=agent_a))
    print(f"Agent A persisted 2 messages to session {SESSION_A!r}.")

    # Phase 2: Agent B (separate session, separate analyst) has agent A's
    # knowledge injected into its question.
    #
    # min_score is a Neo4j vector-index score, not a raw cosine similarity:
    # the index reports (1 + cosine) / 2, so 0.5 means "orthogonal" and any
    # threshold below it filters nothing at all. 0.75 == cosine 0.5. Note
    # also that long-term search is database-wide, not session-scoped, so an
    # already-populated Neo4j contributes its own entities to the block.
    retrieval = Neo4jRetrievalConfig(top_k=5, min_score=0.75)
    with Neo4jSessionManager(
        SESSION_B, settings=settings, user_id=ANALYST_B, retrieval_config=retrieval
    ) as manager_b:
        agent_b = _stand_in_agent("credit-agent")
        registry_b = _attach(manager_b, agent_b)
        question = _dispatch_message(
            registry_b, agent_b, "user", "Should we approve credit for Acme Corp?"
        )
        print("Agent B's question, with injected shared-brain context:")
        print(question["content"][0]["text"])
        registry_b.invoke_callbacks(AfterInvocationEvent(agent=agent_b))
    asyncio.run(_report_graph(settings, acme_id))

    # Phase 3: Restore demo — a new manager instance restores agent A's history.
    with Neo4jSessionManager(SESSION_A, settings=settings, user_id=ANALYST_A) as manager_c:
        restored = _stand_in_agent("kyc-agent")
        _attach(manager_c, restored)
        print(f"Restored {len(restored.messages)} messages for {SESSION_A!r}.")
    asyncio.run(_report_user(settings, ANALYST_A))

    # Phase 4: record_tool_calls=True mirrors toolUse blocks into the third
    # memory layer (reasoning), keyed to the same session.
    with Neo4jSessionManager(
        SESSION_B, settings=settings, user_id=ANALYST_B, record_tool_calls=True
    ) as manager_d:
        agent_d = _stand_in_agent("credit-agent")
        registry_d = _attach(manager_d, agent_d)
        _dispatch_tool_use(registry_d, agent_d, "check_sanctions", {"entity": "Acme Corp"})
        registry_d.invoke_callbacks(AfterInvocationEvent(agent=agent_d))
    asyncio.run(_report_tool_trace(settings, SESSION_B))


if __name__ == "__main__":
    main()
