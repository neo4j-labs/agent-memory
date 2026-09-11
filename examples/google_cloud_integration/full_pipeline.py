#!/usr/bin/env python3
"""Full Google Cloud pipeline for neo4j-agent-memory.

Six phases, each independently readable:

1. **Vertex AI embeddings** — ``gemini-embedding-001`` and the library's 768
   truncation default (skipped without ``GOOGLE_CLOUD_PROJECT``).
2. **Google ADK memory service** — sessions in, hybrid recall out, read the ADK
   2.x way (``response.memories``). The full ``Runner`` loop lives in
   ``examples/google_adk_demo/``.
3. **MCP server** — the extended profile (16 tools) driven through an in-process
   FastMCP client, including the lifespan wiring that injects
   ``MemoryIntegration`` and ``MemoryObserver``.
4. **Reasoning trace + audit query** — ``start_trace`` → ``add_step`` →
   ``record_tool_call(touched_entities=[...])`` → ``complete_trace(outcome=...)``,
   then the one-hop
   ``(:Entity)<-[:TOUCHED]-(:ReasoningStep)<-[:HAS_STEP]-(:ReasoningTrace)``
   query that answers "which reasoning touched this entity?" — run both directly
   and through the MCP ``graph_query`` tool.
5. **Cloud Run configuration** — Streamable HTTP, Secret Manager, and buffered
   writes (``write_mode="buffered"`` + ``client.flush()``), demonstrated rather
   than only described.
6. **MemoryIntegration** — ``SessionStrategy.PER_DAY`` with auto-extraction and
   auto-preference detection.

Requirements::

    pip install "neo4j-agent-memory[google,google-adk,mcp,extraction]"
    gcloud auth application-default login      # only for phase 1

Runs with no API key: without ``OPENAI_API_KEY`` the shared settings helper uses
a local sentence-transformers embedder and a local spaCy/GLiNER pipeline. Set
``MEMORY_API_KEY`` to run against the hosted NAMS backend (phases 4's Cypher and
phase 5's buffered writes are bolt-only and say so).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from _common import (
    VERTEX_EMBEDDING_MODEL,
    build_extractor,
    build_settings,
    describe_settings,
    load_env,
)

from neo4j_agent_memory import MemoryClient, __version__
from neo4j_agent_memory.schema import EntityRef, TraceOutcome

USER_ID = "pipeline-demo-user"
AUDIT_ENTITY = "Sarah Chen"

# The payoff query of the reasoning layer: one hop from an entity back to the
# reasoning step and trace that touched it.
AUDIT_QUERY = """
MATCH (e:Entity)<-[:TOUCHED]-(s:ReasoningStep)<-[:HAS_STEP]-(rt:ReasoningTrace)
WHERE e.name = $name
RETURN rt.task AS task, s.action AS action, collect(DISTINCT e.name) AS entities
ORDER BY task
LIMIT 5
"""


def print_header(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)
    print()


def print_subheader(title: str) -> None:
    print()
    print(f"--- {title} ---")
    print()


def tool_payload(result: Any) -> dict[str, Any]:
    """Read a FastMCP tool result.

    ``result.data`` is FastMCP 4's parsed output — no reaching into
    ``result.content[0].text``. Every memory tool is annotated ``-> str`` and
    returns ``json.dumps(...)``, so the parsed value is itself a JSON string
    that needs one ``json.loads``.
    """
    return dict(json.loads(result.data))


def adk_entries(response: Any) -> list[tuple[str, str]]:
    """Flatten a ``SearchMemoryResponse`` into ``(memory_type, text)`` pairs."""
    rows: list[tuple[str, str]] = []
    for entry in response.memories:
        parts = getattr(entry.content, "parts", None) or []
        text = "".join(getattr(part, "text", None) or "" for part in parts).strip()
        meta = entry.custom_metadata or {}
        rows.append((str(meta.get("memory_type", "?")), text))
    return rows


# ── Phase 1: Vertex AI embeddings ────────────────────────────────────────


async def demo_vertex_ai_embeddings() -> None:
    """Generate embeddings through Vertex AI."""
    print_header("Phase 1: Vertex AI Embeddings")

    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        print("Skipping: GOOGLE_CLOUD_PROJECT is not set.")
        print("To enable: export GOOGLE_CLOUD_PROJECT=your-project-id")
        return

    from neo4j_agent_memory.embeddings.vertex_ai import VertexAIEmbedder

    embedder = VertexAIEmbedder(
        model=VERTEX_EMBEDDING_MODEL,
        project_id=os.getenv("GOOGLE_CLOUD_PROJECT"),
        location=os.getenv("VERTEX_AI_LOCATION", "us-central1"),
    )

    print(f"Model: {embedder.model}")
    print(f"Dimensions: {embedder.dimensions}")
    print(f"Output dimensionality: {embedder.output_dimensionality} (native 3072)")

    print_subheader("Generating Embeddings")
    texts = [
        "Graph databases store relationships as first-class citizens.",
        "Vector search enables semantic similarity matching.",
        "Agent memory combines multiple storage strategies.",
    ]
    for text, emb in zip(texts, await embedder.embed_batch(texts), strict=False):
        print(f"  ✓ {text[:45]}... → {len(emb)} dims")


# ── Phase 2: Google ADK ──────────────────────────────────────────────────


async def demo_adk_memory_service(client: MemoryClient) -> str:
    """Store ADK sessions and read hybrid recall back. Returns the first session."""
    print_header("Phase 2: Google ADK MemoryService")

    from neo4j_agent_memory.integrations.google_adk import Neo4jMemoryService

    memory_service = Neo4jMemoryService(
        memory_client=client,
        user_id=USER_ID,
        include_entities=True,
        include_preferences=True,
    )
    print(f"Neo4jMemoryService user_id: {memory_service.user_id}")
    print("(the full ADK Runner + load_memory loop lives in examples/google_adk_demo/)")

    print_subheader("Storing Conversation Sessions")

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    first_session = f"pipeline-{stamp}-1"
    sessions: list[dict[str, Any]] = [
        {
            "id": first_session,
            "messages": [
                {
                    "role": "user",
                    "content": "I'm working with Dr. Sarah Chen on the GraphRAG "
                    "research project at Stanford University.",
                },
                {
                    "role": "assistant",
                    "content": "Interesting! GraphRAG combines graph databases with "
                    "retrieval-augmented generation. How's the project progressing?",
                },
                {
                    "role": "user",
                    "content": "Great! We've published initial results. I prefer "
                    "working in Python and use Neo4j for the knowledge graph.",
                },
            ],
        },
        {
            "id": f"pipeline-{stamp}-2",
            "messages": [
                {
                    "role": "user",
                    "content": "I have a meeting with the Neo4j team in San Francisco "
                    "next week to discuss the agent memory integration.",
                },
                {
                    "role": "assistant",
                    "content": "That sounds productive! Neo4j's agent memory library "
                    "looks promising for your research.",
                },
            ],
        },
    ]
    for session in sessions:
        await memory_service.add_session_to_memory(session)
        print(f"  ✓ Stored session: {session['id']}")

    # Curate the entity the audit phase will reference, so it carries an
    # embedding (NER gives a name and a type, not a vector).
    entity, dedup = await client.long_term.add_entity(
        AUDIT_ENTITY, "PERSON", description="Researcher on the GraphRAG project"
    )
    print(f"  ✓ Curated entity: {entity.name} ({entity.full_type}, dedup={dedup.action})")

    print_subheader("Searching Memories")
    for query, description in (
        (AUDIT_ENTITY, "person entity"),
        ("GraphRAG research", "project context"),
        ("programming preferences", "user preferences"),
    ):
        response = await memory_service.search_memory(
            query=query, session_id=first_session, limit=3
        )
        print(f"  Query: '{query}' ({description}) — {len(response.memories)} entr(ies)")
        for memory_type, text in adk_entries(response):
            print(f"    [{memory_type}] {text[:60]}...")
        print()

    return first_session


# ── Phase 3: MCP server ──────────────────────────────────────────────────


@asynccontextmanager
async def mcp_client(client: MemoryClient) -> AsyncIterator[Any]:
    """An in-process MCP server wired to this ``MemoryClient``.

    The non-obvious part is the lifespan dict: the tools read ``client``,
    ``integration`` and ``observer`` out of it, so all three have to be put
    there — you could not guess this from the tool signatures.
    """
    from fastmcp import Client, FastMCP

    from neo4j_agent_memory.integration import MemoryIntegration
    from neo4j_agent_memory.mcp._observer import MemoryObserver
    from neo4j_agent_memory.mcp._prompts import register_prompts
    from neo4j_agent_memory.mcp._resources import register_resources
    from neo4j_agent_memory.mcp._tools import register_tools

    integration = MemoryIntegration(client)
    observer = MemoryObserver(client)
    integration.observer = observer

    @asynccontextmanager
    async def _lifespan(server: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"client": client, "integration": integration, "observer": observer}

    mcp = FastMCP("google-cloud-pipeline", lifespan=_lifespan)
    register_tools(mcp, profile="extended")
    register_resources(mcp, profile="extended")
    register_prompts(mcp, profile="extended")

    async with Client(mcp) as session:
        yield session


async def demo_mcp_server(client: MemoryClient) -> None:
    """Drive the extended MCP tool surface."""
    print_header("Phase 3: MCP Server Tools")

    async with mcp_client(client) as mcp:
        tools = await mcp.list_tools()
        print(f"MCP server exposes {len(tools)} tools (extended profile; core has 6):")
        for tool in tools:
            print(f"  • {tool.name}")
        print()

        print_subheader("Tool Demonstrations")

        print("1. memory_store_message")
        result = await mcp.call_tool(
            "memory_store_message",
            {
                "content": "Remember to review the MCP protocol documentation.",
                "session_id": "mcp-demo",
                "role": "user",
            },
        )
        data = tool_payload(result)
        print(f"   stored id={data.get('id', 'N/A')}")

        print()
        print("2. memory_search")
        result = await mcp.call_tool("memory_search", {"query": "MCP protocol", "limit": 3})
        data = tool_payload(result)
        by_type = data.get("results", {})
        print("   " + ", ".join(f"{key}={len(value)}" for key, value in by_type.items()))

        print()
        print("3. memory_get_conversation")
        result = await mcp.call_tool(
            "memory_get_conversation", {"session_id": "mcp-demo", "limit": 5}
        )
        data = tool_payload(result)
        print(f"   retrieved {data.get('message_count', 0)} messages")

        print()
        print("4. graph_query (read-only Cypher)")
        result = await mcp.call_tool(
            "graph_query",
            {
                "query": "MATCH (n) RETURN labels(n) AS type, count(*) AS count "
                "ORDER BY count DESC LIMIT 5"
            },
        )
        data = tool_payload(result)
        for row in data.get("rows", []):
            print(f"   {row}")

        print()
        print("5. memory_get_entity")
        result = await mcp.call_tool(
            "memory_get_entity",
            {"name": AUDIT_ENTITY, "include_neighbors": True, "max_hops": 1},
        )
        data = tool_payload(result)
        if data.get("found"):
            print(f"   found entity: {data['entity'].get('name', 'N/A')}")
        else:
            print("   no entity found (run phase 2 first)")


# ── Phase 4: reasoning trace + audit ─────────────────────────────────────


async def demo_reasoning_audit(client: MemoryClient, session_id: str) -> None:
    """Record a reasoning trace, then audit it with the TOUCHED one-hop query."""
    print_header("Phase 4: Reasoning Trace + TOUCHED Audit Query")

    # Anchor the trace to the message that triggered it, so the audit trail runs
    # message → trace → step → entity.
    conversation = await client.short_term.get_conversation(session_id, limit=5)
    trigger = conversation.messages[0] if conversation.messages else None

    trace = await client.reasoning.start_trace(
        session_id,
        task="Summarise what we know about the GraphRAG project",
        triggered_by_message_id=trigger.id if trigger else None,
    )
    print(f"  trace id: {trace.id}")

    step = await client.reasoning.add_step(
        trace.id,
        thought="Look up the people attached to the project.",
        action="search_entities(query='GraphRAG')",
    )

    # touched_entities writes (:ReasoningStep)-[:TOUCHED]->(:Entity) edges, which
    # is what makes the audit query below possible. Entities are matched by
    # name+type (or id when you have one).
    await client.reasoning.record_tool_call(
        step.id,
        tool_name="search_entities",
        arguments={"query": "GraphRAG", "limit": 5},
        result={"entities": [AUDIT_ENTITY]},
        touched_entities=[EntityRef(name=AUDIT_ENTITY, type="PERSON")],
        message_id=trigger.id if trigger else None,
    )
    print(f"  step {step.id} touched: {AUDIT_ENTITY}")

    await client.reasoning.complete_trace(
        trace.id,
        outcome=TraceOutcome(
            success=True,
            summary="Identified the collaborator on the GraphRAG project.",
            related_entities=[EntityRef(name=AUDIT_ENTITY, type="PERSON")],
            metrics={"steps": 1, "tool_calls": 1},
        ),
    )
    print("  trace completed with a structured TraceOutcome")

    print_subheader("Audit: which reasoning touched this entity?")
    print("  MATCH (e:Entity)<-[:TOUCHED]-(s:ReasoningStep)<-[:HAS_STEP]-(rt:ReasoningTrace)")
    rows = await client.query.cypher(AUDIT_QUERY, {"name": AUDIT_ENTITY})
    if not rows:
        print("  (no rows — TOUCHED edges need a matching :Entity node)")
    for row in rows:
        print(f"  task={row['task']!r}")
        print(f"    action={row['action']!r} entities={row['entities']}")

    # The same query through the MCP graph_query tool, which is how an agent
    # would reach it.
    async with mcp_client(client) as mcp:
        result = await mcp.call_tool(
            "graph_query",
            {"query": AUDIT_QUERY, "parameters": {"name": AUDIT_ENTITY}},
        )
        print(f"  via MCP graph_query: {tool_payload(result).get('row_count', 0)} row(s)")


# ── Phase 5: Cloud Run configuration + buffered writes ───────────────────


async def demo_production_config(client: MemoryClient) -> None:
    """Cloud Run deployment shape, plus the latency primitive that serves it."""
    print_header("Phase 5: Production Configuration (Cloud Run)")

    print("Dockerfile (see deploy/cloudrun/ for the maintained version):")
    print("-" * 40)
    print("""
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install "neo4j-agent-memory[google,google-adk,mcp]"
EXPOSE 8080
# Streamable HTTP at /mcp/ — HTTP+SSE is deprecated in the MCP spec.
CMD ["neo4j-agent-memory", "mcp", "serve", \\
     "--transport", "http", "--host", "0.0.0.0", "--port", "8080"]
""")

    print("Environment (use Secret Manager for the secrets):")
    print("-" * 40)
    print("""
NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
NEO4J_PASSWORD=<from-secret-manager>
GOOGLE_CLOUD_PROJECT=your-project-id
EMBEDDING_PROVIDER=vertex_ai
EMBEDDING_MODEL=gemini-embedding-001

# Or skip the database entirely and point at the hosted service:
# MEMORY_API_KEY=<from-secret-manager>
""")

    print("Deploy:")
    print("-" * 40)
    print("""
gcloud run deploy neo4j-memory-mcp \\
  --source deploy/cloudrun \\
  --region us-central1 \\
  --set-secrets NEO4J_URI=neo4j-uri:latest \\
  --set-secrets NEO4J_PASSWORD=neo4j-password:latest \\
  --set-env-vars GOOGLE_CLOUD_PROJECT=$PROJECT_ID
""")

    print_subheader("Buffered writes — keeping Neo4j off the response path")

    if client.backend == "nams":
        print("  Skipped: client.buffered is bolt-only (NAMS commits server-side).")
        return

    buffered_settings = build_settings()
    buffered_settings.memory.write_mode = "buffered"

    async with MemoryClient(buffered_settings, extractor=None) as buffered:
        print(f"  write_mode=buffered → is_buffered={buffered.buffered.is_buffered}")
        # submit() is a coroutine: await it. In buffered mode the await returns
        # as soon as the write is queued; a background task drains the queue.
        for index in range(5):
            await buffered.buffered.submit(
                "MERGE (n:PipelineDemo {i: $i}) SET n.written_at = datetime()",
                {"i": index},
            )
        print(f"  queued writes, pending={buffered.buffered.pending}")
        await buffered.flush()
        print(f"  after flush(), pending={buffered.buffered.pending}")
        print(f"  background write errors: {len(buffered.write_errors)}")
        rows = await buffered.query.cypher("MATCH (n:PipelineDemo) RETURN count(n) AS n")
        print(f"  :PipelineDemo nodes written: {rows[0]['n']}")


# ── Phase 6: MemoryIntegration ───────────────────────────────────────────


async def demo_memory_integration(client: MemoryClient) -> None:
    """MemoryIntegration with PER_DAY sessions, auto-extraction, preferences."""
    print_header("Phase 6: MemoryIntegration with Session Strategies")

    from neo4j_agent_memory.integration import MemoryIntegration, SessionStrategy

    print("Session strategies:")
    print("  - PER_CONVERSATION: new UUID per instance (default)")
    print("  - PER_DAY: daily continuity (user_id-YYYY-MM-DD)")
    print("  - PERSISTENT: fixed user_id for maximum continuity")

    print_subheader("Using PER_DAY")

    async with MemoryIntegration(
        client,
        session_strategy=SessionStrategy.PER_DAY,
        user_id=USER_ID,
        auto_extract=True,
        auto_preferences=True,
    ) as memory:
        session_id = memory.resolve_session_id()
        print(f"  resolved session: {session_id}")

        await memory.store_message(
            "user",
            "I'm researching GraphRAG with Dr. Sarah Chen at Stanford. "
            "I prefer using Python and Neo4j for knowledge graphs.",
        )
        print("  stored one message (entity extraction + preference detection)")

        # search() returns {"query": ..., "results": {<memory type>: [...]}} —
        # len() of the top-level dict would always be 2, not a match count.
        results = await memory.search("GraphRAG research")
        by_type = results.get("results", {})
        print("  search: " + ", ".join(f"{key}={len(value)}" for key, value in by_type.items()))

        # get_context() returns {"session_id", "context", "has_context"} — there
        # is no MemoryIntegration.session_id attribute.
        context = await memory.get_context()
        print(
            f"  context for session {context['session_id']} — has_context={context['has_context']}"
        )
        excerpt = (context["context"] or "").strip().splitlines()[:6]
        for line in excerpt:
            print(f"    | {line[:80]}")

    print()
    print("MemoryIntegration owns session-id resolution and background preference")
    print("detection; passing an existing client keeps one connection for both.")


# ── Phase 7: consolidation ───────────────────────────────────────────────


async def demo_consolidation(client: MemoryClient) -> None:
    """The hygiene job you would schedule, in its default dry-run mode."""
    print_header("Phase 7: Consolidation (dry run)")

    if client.backend == "nams":
        print("Skipped: client.consolidation is bolt-only.")
        return

    report = await client.consolidation.dedupe_entities(dry_run=True)
    print(f"  duplicate candidates: {report.candidate_count}")
    print(f"  actions that would be taken: {report.actions_taken}")
    print("  (dry_run=True is the default — nothing was merged)")


# ── main ─────────────────────────────────────────────────────────────────


async def main() -> None:
    """Run the complete Google Cloud integration pipeline."""
    load_env()

    print("\n" + "=" * 70)
    print("  Neo4j Agent Memory - Google Cloud Integration Pipeline")
    print(f"  neo4j-agent-memory {__version__}")
    print("=" * 70)
    print()
    print("Phases:")
    print("  1. Vertex AI embeddings (gemini-embedding-001)")
    print("  2. Google ADK MemoryService")
    print("  3. MCP server — 6 core / 16 extended tools")
    print("  4. Reasoning trace + TOUCHED audit query")
    print("  5. Cloud Run configuration + buffered writes")
    print("  6. MemoryIntegration with SessionStrategy.PER_DAY")
    print("  7. Consolidation dry run")
    print()
    print(f"Timestamp: {datetime.now().isoformat()}")

    settings = build_settings()
    extractor = build_extractor(settings)
    print()
    print("Configuration:")
    describe_settings(settings, extractor)

    try:
        await demo_vertex_ai_embeddings()

        async with MemoryClient(settings, extractor=extractor) as client:
            print(f"\nConnected backend: {client.backend}")
            session_id = await demo_adk_memory_service(client)
            await demo_mcp_server(client)
            await demo_reasoning_audit(client, session_id)
            await demo_production_config(client)
            await demo_memory_integration(client)
            await demo_consolidation(client)

    except Exception as error:
        print(f"\n❌ Error: {error}")
        print("\nTroubleshooting:")
        print("  1. Is Neo4j running?  docker ps | grep neo4j")
        print("  2. Credentials: NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD")
        print("  3. For Vertex AI: gcloud auth application-default login")
        print("  4. For hosted NAMS: MEMORY_API_KEY")
        raise

    print_header("Pipeline Complete!")
    print("Demonstrated:")
    print("  ✓ Vertex AI embeddings with gemini-embedding-001")
    print("  ✓ ADK MemoryService session storage and hybrid recall")
    print("  ✓ MCP server tools (6 core / 16 extended) over FastMCP 4")
    print("  ✓ Reasoning trace with TOUCHED audit edges and the one-hop query")
    print("  ✓ Cloud Run shape (Streamable HTTP) + buffered writes")
    print("  ✓ MemoryIntegration with PER_DAY sessions and preference detection")
    print("  ✓ Consolidation dry run")
    print()
    print("Next steps:")
    print("  • Explore the graph in Neo4j Browser")
    print("  • Serve memory over HTTP: neo4j-agent-memory mcp serve --transport http")
    print("  • Deploy to Cloud Run: see deploy/cloudrun/README.md")
    print()


if __name__ == "__main__":
    asyncio.run(main())
