"""Integration tests for the v0.3 reasoning-region polish.

Covers:

* ``record_tool_call(touched_entities=[...])`` writes
  ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` edges (P0.1).
* ``client.reasoning.on_tool_call_recorded`` hook fires after each
  ``record_tool_call`` and can add additional :TOUCHED edges (P0.1).
* ``complete_trace(outcome=TraceOutcome(...))`` persists structured
  outcome fields, including ``error_kind`` as an indexable property
  on the ReasoningTrace node (P1.3).
"""

from __future__ import annotations

import pytest

from neo4j_agent_memory.schema.models import EntityRef, TraceOutcome


@pytest.mark.integration
@pytest.mark.asyncio
class TestTouchedEdgeWriter:
    async def test_record_tool_call_writes_touched_edges(self, clean_memory_client, session_id):
        client = clean_memory_client
        trace = await client.reasoning.start_trace(session_id, "Find clients in healthcare")
        step = await client.reasoning.add_step(trace.id, thought="Look up Anthem")

        await client.reasoning.record_tool_call(
            step.id,
            tool_name="recommend_team",
            arguments={"client_name": "Anthem"},
            result=[{"consultant": "Sara"}],
            touched_entities=[
                EntityRef(name="Anthem", type="Client"),
                EntityRef(name="Sara", type="PERSON"),
            ],
        )

        rows = await client.graph.execute_read(
            """
            MATCH (s:ReasoningStep {id: $step_id})-[:TOUCHED]->(e:Entity)
            RETURN e.name AS name, e.type AS type
            ORDER BY name
            """,
            {"step_id": str(step.id)},
        )
        names = [(r["name"], r["type"]) for r in rows]
        assert names == [("Anthem", "Client"), ("Sara", "PERSON")]

    async def test_record_tool_call_idempotent_touched_edges(self, clean_memory_client, session_id):
        client = clean_memory_client
        trace = await client.reasoning.start_trace(session_id, "Repeat tool call")
        step = await client.reasoning.add_step(trace.id)

        for _ in range(3):
            await client.reasoning.record_tool_call(
                step.id,
                tool_name="get_client",
                arguments={"name": "Anthem"},
                touched_entities=[EntityRef(name="Anthem", type="Client")],
            )

        rows = await client.graph.execute_read(
            """
            MATCH (s:ReasoningStep {id: $step_id})-[r:TOUCHED]->(:Entity {name: 'Anthem'})
            RETURN count(r) AS edges
            """,
            {"step_id": str(step.id)},
        )
        assert rows[0]["edges"] == 1

    async def test_observer_hook_fires_and_can_add_edges(self, clean_memory_client, session_id):
        client = clean_memory_client

        observed: list[str] = []

        @client.reasoning.on_tool_call_recorded
        async def hook(tool_call, ctx):
            observed.append(tool_call.tool_name)
            # Hook adds an :Industry edge derived from the tool's result.
            await ctx.add_touched_edge(EntityRef(name="Healthcare", type="Industry"))

        trace = await client.reasoning.start_trace(session_id, "Hook test")
        step = await client.reasoning.add_step(trace.id)

        await client.reasoning.record_tool_call(
            step.id,
            tool_name="recommend_team",
            arguments={"client_name": "Anthem"},
            result={"industry": "healthcare"},
        )

        assert observed == ["recommend_team"]

        rows = await client.graph.execute_read(
            """
            MATCH (s:ReasoningStep {id: $step_id})-[:TOUCHED]->(e:Entity)
            RETURN e.name AS name
            ORDER BY name
            """,
            {"step_id": str(step.id)},
        )
        assert [r["name"] for r in rows] == ["Healthcare"]

    async def test_observer_hook_errors_do_not_break_record_tool_call(
        self, clean_memory_client, session_id
    ):
        client = clean_memory_client

        @client.reasoning.on_tool_call_recorded
        async def bad_hook(tool_call, ctx):
            raise RuntimeError("hook intentionally failed")

        trace = await client.reasoning.start_trace(session_id, "Hook error test")
        step = await client.reasoning.add_step(trace.id)

        # The exception inside the hook must NOT propagate to the caller.
        tool_call = await client.reasoning.record_tool_call(
            step.id,
            tool_name="recommend_team",
            arguments={"client_name": "Anthem"},
        )
        assert tool_call.tool_name == "recommend_team"


@pytest.mark.integration
@pytest.mark.asyncio
class TestTraceOutcomeStructured:
    async def test_trace_outcome_persists_structured_fields(self, clean_memory_client, session_id):
        client = clean_memory_client
        trace = await client.reasoning.start_trace(session_id, "Failed retrieval")
        step = await client.reasoning.add_step(trace.id, thought="Search")

        await client.reasoning.complete_trace(
            trace.id,
            outcome=TraceOutcome(
                success=False,
                summary="Search timed out",
                error_kind="timeout",
                related_entities=[
                    EntityRef(name="ProjectX", type="Project"),
                ],
                metrics={"latency_ms": 12000.0, "tools_called": 3.0},
            ),
        )

        rows = await client.graph.execute_read(
            """
            MATCH (rt:ReasoningTrace {id: $id})
            RETURN rt.success AS success,
                   rt.outcome AS outcome,
                   rt.error_kind AS error_kind,
                   rt.metrics_json AS metrics_json
            """,
            {"id": str(trace.id)},
        )
        assert len(rows) == 1
        assert rows[0]["success"] is False
        assert rows[0]["outcome"] == "Search timed out"
        assert rows[0]["error_kind"] == "timeout"
        assert "latency_ms" in rows[0]["metrics_json"]

        # Related entities became :TOUCHED edges off the most recent step.
        rows = await client.graph.execute_read(
            """
            MATCH (rt:ReasoningTrace {id: $trace_id})-[:HAS_STEP]->(s:ReasoningStep)
                  -[:TOUCHED]->(e:Entity)
            RETURN e.name AS name
            """,
            {"trace_id": str(trace.id)},
        )
        assert any(r["name"] == "ProjectX" for r in rows)

    async def test_legacy_string_outcome_still_works(self, clean_memory_client, session_id):
        client = clean_memory_client
        trace = await client.reasoning.start_trace(session_id, "Legacy outcome")
        await client.reasoning.add_step(trace.id, thought="Step 1")

        # Pre-0.3 callers passed a free-text outcome and a bool.
        await client.reasoning.complete_trace(trace.id, outcome="Found the answer", success=True)

        rows = await client.graph.execute_read(
            "MATCH (rt:ReasoningTrace {id: $id}) "
            "RETURN rt.outcome AS outcome, rt.success AS success, rt.error_kind AS error_kind",
            {"id": str(trace.id)},
        )
        assert rows[0]["outcome"] == "Found the answer"
        assert rows[0]["success"] is True
        assert rows[0]["error_kind"] is None
