"""Audit-trail example: 1-hop reasoning-to-entity audit query.

Demonstrates the v0.3 reasoning-region polish:

* ``record_tool_call(touched_entities=[...])`` — explicit edge writes.
* ``@client.reasoning.on_tool_call_recorded`` hook — domain-specific
  inference of touched entities from tool call results.
* ``TraceOutcome`` — structured outcome with indexable ``error_kind``.
* The headline audit query: a 1-hop ``MATCH (c:Client)<-[:TOUCHED]-(s)``.

Run from the repo root::

    uv run python examples/audit-trail/main.py

If ``NEO4J_URI`` / ``NEO4J_PASSWORD`` aren't set, the script assumes
``bolt://localhost:7687`` with password ``password``.
"""

from __future__ import annotations

import asyncio
import os

from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
)
from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome

from .tool_calls import infer_touched


def build_settings() -> MemorySettings:
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        llm=None,
        embedding=EmbeddingConfig(
            provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
            model="all-MiniLM-L6-v2",
            dimensions=384,
        ),
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.NONE,
        ),
    )


async def main() -> None:
    settings = build_settings()
    async with MemoryClient(settings) as client:
        session_id = "audit-trail-demo"

        # tag::register_hook[]
        # Register a domain-specific hook that turns every tool call into
        # :TOUCHED edges. Hook errors are logged, never raised.
        @client.reasoning.on_tool_call_recorded
        async def link_touched_entities(tool_call, ctx):
            for ref in infer_touched(
                tool_call.tool_name,
                tool_call.arguments,
                tool_call.result,
            ):
                await ctx.add_touched_edge(ref)
        # end::register_hook[]

        # Reset prior demo state for an idempotent rerun.
        await client.graph.execute_write(
            "MATCH (n) WHERE n.id STARTS WITH 'demo:' OR n.session_id = $sid "
            "DETACH DELETE n",
            {"sid": session_id},
        )

        # 1) Open a trace and record a tool call. The hook fires
        #    automatically and writes :TOUCHED edges.
        trace = await client.reasoning.start_trace(
            session_id, task="Recommend a team for Anthem"
        )
        step = await client.reasoning.add_step(
            trace.id, thought="Look up consultants who match Anthem's needs"
        )

        await client.reasoning.record_tool_call(
            step.id,
            tool_name="recommend_team",
            arguments={"client_name": "Anthem"},
            result=[
                {"consultant": "Sara"},
                {"consultant": "Liam"},
            ],
        )

        # 2) Complete the trace with a structured outcome.
        await client.reasoning.complete_trace(
            trace.id,
            outcome=TraceOutcome(
                success=True,
                summary="Recommended a 2-person team for Anthem",
                error_kind=None,
                related_entities=[
                    EntityRef(name="Anthem", type="Client"),
                ],
                metrics={"tools_called": 1.0},
            ),
        )

        # 3) The headline audit query: one-hop, indexed, fast.
        rows = await client.graph.execute_read(
            """
            MATCH (e:Entity {name: 'Anthem'})<-[:TOUCHED]-(s:ReasoningStep)
                  <-[:HAS_STEP]-(rt:ReasoningTrace)
            RETURN rt.task AS task, s.thought AS thought, rt.outcome AS outcome
            """,
        )
        print("Audit trail for Anthem:")
        for row in rows:
            print(f"  - task: {row['task']}")
            print(f"    thought: {row['thought']}")
            print(f"    outcome: {row['outcome']}")


if __name__ == "__main__":
    asyncio.run(main())
