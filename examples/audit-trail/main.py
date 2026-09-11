"""Audit-trail example: 1-hop reasoning-to-entity audit queries.

Demonstrates the reasoning audit edges, added in ``neo4j-agent-memory``
v0.2.0:

* ``record_tool_call(touched_entities=[...])`` — explicit edge writes for
  entities you already know at call time (*Approach 1*).
* ``@client.reasoning.on_tool_call_recorded`` — a hook that infers touched
  entities from the tool *result*, which you only have after the call
  returns (*Approach 2*).
* ``TraceOutcome`` — a structured outcome with an indexable ``error_kind``.
  The run records one successful and one failed trace so both show up in
  the audit trail.
* ``start_trace(triggered_by_message_id=...)`` — links the trace back to
  the user message that caused it.
* The headline audit query, one hop from the entity::

      MATCH (e:Entity {name: 'Anthem'})<-[:TOUCHED]-(s:ReasoningStep)
            <-[:HAS_STEP]-(rt:ReasoningTrace)

**Bolt only.** ``:TOUCHED`` edges are a bolt-side schema feature: the
hosted NAMS backend silently drops ``touched_entities`` today, and
``client.graph`` (used here for the idempotency reset) raises
``NotSupportedError`` there. The audit *read* goes through
``client.query.cypher``, which works on both backends.

Run from the repo root::

    uv run python examples/audit-trail/main.py

If ``NEO4J_URI`` / ``NEO4J_PASSWORD`` aren't set, the script assumes
``bolt://localhost:7687`` with password ``password``.
"""

from __future__ import annotations

import asyncio
import os
import sys

from pydantic import SecretStr

# Allow running as a standalone script (uv run python examples/audit-trail/main.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_calls import infer_touched

from neo4j_agent_memory import (
    BoltMemoryClient,
    ExtractionConfig,
    ExtractorType,
    Neo4jConfig,
    ToolCall,
    ToolCallStatus,
    connect,
)
from neo4j_agent_memory.config.settings import BoltSettings
from neo4j_agent_memory.memory.reasoning import HookContext

# EntityRef and TraceOutcome are not re-exported from the package root.
from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome

SESSION_ID = "audit-trail-demo"

#: Tools this demo records — used to filter the shared ``Tool`` stats.
DEMO_TOOLS = ("recommend_team", "lookup_industry", "find_consultants")


def build_settings() -> BoltSettings:
    return BoltSettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        llm=None,
        embedding="sentence-transformers/all-MiniLM-L6-v2",
        extraction=ExtractionConfig(
            extractor_type=ExtractorType.NONE,
        ),
    )


async def reset_demo_state(client: BoltMemoryClient) -> None:
    """Delete this demo's session data so the script is rerunnable.

    Reasoning steps and tool calls carry no ``session_id``, so they have to
    be reached by traversal from the trace rather than matched by property.
    """
    await client.graph.execute_write(
        """
        MATCH (rt:ReasoningTrace {session_id: $sid})
        OPTIONAL MATCH (rt)-[:HAS_STEP]->(s:ReasoningStep)
        OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
        DETACH DELETE rt, s, tc
        """,
        {"sid": SESSION_ID},
    )
    await client.graph.execute_write(
        """
        MATCH (c:Conversation {session_id: $sid})
        OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
        DETACH DELETE c, m
        """,
        {"sid": SESSION_ID},
    )
    # ``Tool`` nodes carry pre-aggregated counters that deleting ToolCall
    # nodes does not decrement, so drop the demo tools once nothing else
    # references them — they are re-MERGEd, zeroed, on the next call.
    await client.graph.execute_write(
        """
        MATCH (t:Tool)
        WHERE t.name IN $tools AND NOT (t)<-[:INSTANCE_OF]-(:ToolCall)
        DETACH DELETE t
        """,
        {"tools": list(DEMO_TOOLS)},
    )


async def main() -> None:
    settings = build_settings()
    # ``:TOUCHED`` edges are bolt-only, so connect through the typed factory:
    # it returns a concrete ``BoltMemoryClient`` rather than the
    # base-Protocol-typed ``MemoryClient``, which is what gives the audit hook
    # and the tool-stats read a checked type.
    client = await connect(settings)
    try:
        await reset_demo_state(client)

        # tag::register_hook[]
        # Approach 2: register a domain-specific hook that turns every tool
        # call into :TOUCHED edges. Hook errors are logged, never raised.
        @client.reasoning.on_tool_call_recorded
        async def link_touched_entities(tool_call: ToolCall, ctx: HookContext) -> None:
            for ref in infer_touched(
                tool_call.tool_name,
                tool_call.arguments,
                tool_call.result,
            ):
                await ctx.add_touched_edge(ref)

        # end::register_hook[]

        # 1) The user message that sets the agent off. Linking it to the trace
        #    is what lets the audit trail answer "who asked for this?".
        question = await client.short_term.add_message(
            SESSION_ID,
            "user",
            "Who should we staff on the Anthem engagement?",
            extraction_mode="skip",
        )

        # 2) A successful trace. The hook fires after each record_tool_call
        #    and writes the :TOUCHED edges.
        trace = await client.reasoning.start_trace(
            SESSION_ID,
            task="Recommend a team for Anthem",
            triggered_by_message_id=question.id,
        )
        step = await client.reasoning.add_step(
            trace.id, thought="Look up consultants who match Anthem's needs"
        )

        # Approach 2 in action: the consultants are only known from the
        # result, so ``infer_touched`` reads them out of it.
        await client.reasoning.record_tool_call(
            step.id,
            tool_name="recommend_team",
            arguments={"client_name": "Anthem"},
            result=[
                {"consultant": "Sara"},
                {"consultant": "Liam"},
            ],
            message_id=question.id,
        )

        # Approach 1: the entity is known at call time, so pass it straight
        # in. ``infer_touched`` maps this tool too — the edge is keyed on the
        # (step, entity) pair, so both paths converge on the same one edge.
        await client.reasoning.record_tool_call(
            step.id,
            tool_name="lookup_industry",
            arguments={"industry": "Healthcare"},
            result={"segment": "Payer"},
            touched_entities=[EntityRef(name="Healthcare", type="INDUSTRY")],
            message_id=question.id,
        )

        await client.reasoning.complete_trace(
            trace.id,
            outcome=TraceOutcome(
                success=True,
                summary="Recommended a 2-person team for Anthem",
                error_kind=None,
                # Trace-level entities are materialized as :TOUCHED edges on
                # the trace's most recent step.
                related_entities=[
                    EntityRef(name="Anthem", type="CLIENT"),
                ],
                metrics={"tools_called": 2.0},
            ),
        )

        # 3) A failing trace against the same client. ``error_kind`` is an
        #    indexed property, so failure-mode analytics stay cheap.
        followup = await client.short_term.add_message(
            SESSION_ID,
            "user",
            "Can you add a FedRAMP specialist to that team?",
            extraction_mode="skip",
        )
        failed_trace = await client.reasoning.start_trace(
            SESSION_ID,
            task="Add a FedRAMP specialist to the Anthem team",
            triggered_by_message_id=followup.id,
        )
        failed_step = await client.reasoning.add_step(
            failed_trace.id, thought="Search the bench for FedRAMP experience"
        )
        await client.reasoning.record_tool_call(
            failed_step.id,
            tool_name="find_consultants",
            arguments={"skill": "FedRAMP"},
            result=None,
            status=ToolCallStatus.TIMEOUT,
            error="bench search timed out after 30s",
            message_id=followup.id,
        )
        await client.reasoning.complete_trace(
            failed_trace.id,
            outcome=TraceOutcome(
                success=False,
                summary="No consultants matched the required skills",
                error_kind="timeout",
                related_entities=[
                    EntityRef(name="Anthem", type="CLIENT"),
                ],
                metrics={"tools_called": 1.0},
            ),
        )

        # 4) The headline audit query: one hop from the entity, plus one more
        #    hop back to the message that triggered the trace. Going through
        #    ``client.query.cypher`` keeps the read portable across backends.
        rows = await client.query.cypher(
            """
            MATCH (e:Entity {name: $entity})<-[:TOUCHED]-(s:ReasoningStep)
                  <-[:HAS_STEP]-(rt:ReasoningTrace)
            OPTIONAL MATCH (rt)-[:INITIATED_BY]->(m:Message)
            RETURN rt.task AS task,
                   s.thought AS thought,
                   rt.outcome AS summary,
                   rt.success AS success,
                   rt.error_kind AS error_kind,
                   rt.metrics_json AS metrics,
                   m.content AS triggered_by
            ORDER BY rt.completed_at DESC
            """,
            {"entity": "Anthem"},
        )
        print(f"Audit trail for Anthem ({len(rows)} step(s)):")
        for row in rows:
            print(f"  - task: {row['task']}")
            print(f"    triggered_by: {row['triggered_by']}")
            print(f"    thought: {row['thought']}")
            print(f"    summary: {row['summary']}")
            print(f"    success: {row['success']}  error_kind: {row['error_kind']}")
            print(f"    metrics: {row['metrics']}")

        # Every entity the successful trace's step touched, either path.
        touched = await client.query.cypher(
            """
            MATCH (rt:ReasoningTrace {id: $trace_id})-[:HAS_STEP]->(:ReasoningStep)
                  -[:TOUCHED]->(e:Entity)
            RETURN e.name AS name, e.type AS type
            ORDER BY type, name
            """,
            {"trace_id": str(trace.id)},
        )
        print("\nEntities touched by the successful trace:")
        for row in touched:
            print(f"  - {row['type']}: {row['name']}")

        # 5) The read side of reasoning memory: pre-aggregated tool stats.
        stats = [s for s in await client.reasoning.get_tool_stats() if s.name in DEMO_TOOLS]
        print("\nTool usage:")
        for tool in sorted(stats, key=lambda s: s.name):
            print(
                f"  - {tool.name}: {tool.total_calls} call(s), success rate {tool.success_rate:.0%}"
            )
    finally:
        # ``connect()`` hands back an already-connected client, so the caller
        # owns the lifecycle (unlike ``async with MemoryClient(...)``).
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
