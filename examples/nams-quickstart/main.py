"""NAMS quickstart — the shortest path from an API key to a populated memory graph.

One API key, no database, no embedding provider, no extraction model: the
hosted **Neo4j Agent Memory Service (NAMS)** supplies all three. The script
walks the three memory layers plus the hosted-only surface:

1. **Short-term** — create a conversation, bulk-insert three messages.
2. **Long-term** — write one entity, then *await server-side extraction* and
   read back what NAMS extracted on its own.
3. **Context** — the server-assembled three-tier view (reflections,
   observations, recent messages).
4. **Graph** — one-hop expansion around an extracted entity.
5. **Reasoning** — record a trace with a tool call, then read the steps back
   from the server.
6. **Ontology** — the schema extraction is validated against (NAMS-only).
7. **Cypher** — the portable read-only query accessor.

Run:

    cp .env.example .env     # set MEMORY_API_KEY
    uv pip install -r requirements.txt
    uv run python main.py

``NamsSettings()`` reads ``MEMORY_API_KEY`` and, optionally,
``MEMORY_ENDPOINT`` (private deployment) and ``MEMORY_WORKSPACE_ID``
(header-scoped dev/staging deployments).

Portability: steps 1, 2 (the write), 5 and 7 are the backend-agnostic
Protocol — swap ``NamsSettings()`` for ``BoltSettings(neo4j=...)`` and they run
against your own Neo4j unchanged. Extraction status, observations,
reflections, graph expansion and ontologies are hosted-only; see the README.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from neo4j_agent_memory import NamsSettings, connect
from neo4j_agent_memory.core.exceptions import (
    AuthenticationError,
    NotSupportedError,
    RateLimitError,
    TransportError,
)

CONVERSATION_NAME = "nams-quickstart-demo"

TRANSCRIPT = [
    {"role": "user", "content": "Hi, I'm Alice."},
    {"role": "assistant", "content": "Nice to meet you, Alice!"},
    {
        "role": "user",
        "content": "I love Italian food and dislike crowded restaurants.",
    },
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


async def main() -> None:
    load_env()

    if not os.environ.get("MEMORY_API_KEY"):
        raise SystemExit(
            "Set MEMORY_API_KEY to your NAMS API key. "
            "Sign up at https://memory.neo4jlabs.com to get one."
        )

    # NamsSettings is the hosted twin of BoltSettings: no Neo4j URI, no
    # embedding provider, no LLM. `connect()` returns a NAMS-typed client, so
    # the hosted-only calls below (extraction status, graph expansion) are
    # statically visible instead of needing a capability probe.
    settings = NamsSettings()
    client = await connect(settings)
    print(f"Connected to {settings.nams.endpoint} (backend={client.backend})")
    if settings.nams.workspace_id:
        # Set MEMORY_WORKSPACE_ID for deployments that scope by header.
        print(f"Workspace: {settings.nams.workspace_id}")

    try:
        # 1. Short-term memory -------------------------------------------------
        # NAMS mints conversation ids server-side, so create the conversation
        # first and use the id it returns everywhere below.
        conversation = await client.short_term.create_conversation(CONVERSATION_NAME)
        conversation_id = str(conversation.id)
        print(f"\nConversation: {conversation_id}")

        # One round-trip for the whole transcript (up to 100 messages per call).
        stored = await client.short_term.bulk_add_messages(conversation_id, TRANSCRIPT)
        print(f"Stored {len(stored)} messages in one request:")
        for message in stored:
            print(f"   {message.role.value:>9}: {message.content}")

        # 2. Long-term memory --------------------------------------------------
        # A write of our own. NAMS resolves before it creates: a near-duplicate
        # name merges onto the existing entity and that canonical one comes back.
        entity = await client.long_term.add_entity(
            "Alice",
            "PERSON",
            description="The user introducing themselves.",
        )
        print(f"\nWrote entity: {entity.display_name} ({entity.full_type})")

        # NAMS also extracts entities from the messages — in a background
        # pipeline, so a read straight after a write can legitimately come back
        # empty. Await the pipeline explicitly instead of sleeping.
        status = await client.short_term.get_extraction_status(conversation_id)
        print(f"Extraction right after the write: {status.pending_count} message(s) pending")
        settled = await client.long_term.wait_for_extraction(
            session_id=conversation_id,
            expected_names=["Alice"],
            timeout=30.0,
        )
        extracted = await client.long_term.search_entities("Alice", limit=5)
        print(f"Extraction settled: {settled}; searchable entities: {len(extracted)}")
        for found in extracted:
            print(f"   {found.display_name} ({found.full_type})")

        # 3. Server-assembled context ------------------------------------------
        # The reason to use a hosted backend: the three-tier view is built for
        # you (reflections over observations over recent messages).
        context = await client.short_term.get_context("Italian food", session_id=conversation_id)
        observations = await client.short_term.get_observations(conversation_id)
        reflections = await client.short_term.get_reflections(conversation_id)
        print(
            f"\nContext block: {len(context)} chars, "
            f"{len(observations)} observation(s), {len(reflections)} reflection(s)"
        )

        # 4. One-hop graph expansion -------------------------------------------
        if extracted:
            neighborhood = await client.long_term.expand_graph(str(extracted[0].id))
            print(
                f"Neighborhood of {extracted[0].display_name}: "
                f"{len(neighborhood['nodes'])} node(s), {len(neighborhood['edges'])} edge(s)"
            )

        # 5. Reasoning memory --------------------------------------------------
        trace = await client.reasoning.start_trace(
            session_id=conversation_id,
            task="Recommend a restaurant for Alice.",
        )
        step = await client.reasoning.add_step(
            trace.id,
            thought="Alice likes Italian and dislikes crowds.",
            action="Look up quiet Italian places.",
            observation="Found 3 candidates.",
        )
        await client.reasoning.record_tool_call(
            step.id,
            tool_name="restaurant_search",
            arguments={"cuisine": "Italian", "noise_level": "quiet"},
            result=["Da Mario", "Trattoria Bella", "Osteria del Sole"],
        )
        await client.reasoning.complete_trace(
            trace.id, outcome="Suggested 3 restaurants.", success=True
        )
        # Read back from the server, so a silent write failure is visible. NAMS
        # stores steps and tool calls per conversation and has no Trace entity:
        # `get_session_traces` re-assembles one aggregate trace client-side, so
        # assert on the step count rather than echoing `trace.task`.
        traces = await client.reasoning.get_session_traces(conversation_id)
        steps = traces[0].steps if traces else []
        print(f"\nServer-side reasoning steps for this conversation: {len(steps)}")
        for number, recorded in enumerate(steps, start=1):
            print(f"   step {number}: {len(recorded.tool_calls)} tool call(s)")

        # 6. Active ontology (NAMS-only) ---------------------------------------
        # The schema server-side extraction is validated against.
        try:
            active = await client.ontology.get_active()
        except NotSupportedError as exc:
            print(f"\nNo active ontology bound for this workspace: {exc}")
        else:
            print(
                f"\nActive ontology: {active.document.domain.name} "
                f"(revision {active.revision}, {active.validation_mode}) — "
                f"{len(active.document.entity_types)} entity type(s)"
            )

        # 7. Portable read-only Cypher -----------------------------------------
        # Works on both backends. Write keywords are rejected client-side
        # (`ValueError`) before any request is sent; the NAMS endpoint is
        # read-only by contract.
        try:
            rows = await client.query.cypher(
                "MATCH (e:Entity {name: $name}) RETURN e.name AS name LIMIT 1",
                {"name": "Alice"},
            )
        except NotSupportedError as exc:
            # The Cypher endpoint is a Platinum-tier feature; a deployment or
            # plan without it says so here instead of failing opaquely.
            print(f"Cypher is not available on this deployment: {exc}")
        except (AuthenticationError, RateLimitError, TransportError) as exc:
            # Real failures the reader must see — don't disguise them as a
            # missing feature.
            print(f"Cypher request failed: {exc}")
            raise
        else:
            print(f"Cypher round-trip: {rows}")

        print("\nDone. Your memory graph is at https://memory.neo4jlabs.com.")
    finally:
        # `connect()` hands back an already-connected client, so we own closing
        # it. `async with MemoryClient(settings)` does this for you, at the cost
        # of the NAMS-typed layers.
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
