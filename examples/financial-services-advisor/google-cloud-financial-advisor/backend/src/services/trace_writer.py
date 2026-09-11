"""Audit-grade reasoning-trace recording for an ADK run.

This is where the example earns its "explainable AI" claim. For each run we
write, through the library's reasoning layer:

* a ``ReasoningTrace`` linked to the user message that started it
  (``triggered_by_message_id`` → ``(:ReasoningTrace)-[:INITIATED_BY]->(:Message)``);
* one ``ReasoningStep`` per agent activation;
* one ``ToolCall`` per tool result, carrying ``touched_entities`` so the graph
  gets ``(:ReasoningStep)-[:TOUCHED]->(:Entity)`` edges;
* a structured :class:`TraceOutcome` at completion, with ``error_kind``,
  ``related_entities`` and metrics.

Which makes the regulator's question a one-hop query::

    MATCH (e:Entity {name: $customer})<-[:TOUCHED]-(s:ReasoningStep)
          <-[:HAS_STEP]-(t:ReasoningTrace)
    RETURN t.task, s.thought, s.action

Steps are written as the run proceeds, so a crash leaves a partial trace rather
than nothing. Every write is guarded: a Neo4j problem must degrade the audit
trail, never break the user's response.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory import ToolCallStatus
from neo4j_agent_memory.schema import EntityRef, TraceOutcome

from .adk_events import RunEvent, truncate_result

if TYPE_CHECKING:
    from .memory_service import FinancialMemoryService

logger = logging.getLogger(__name__)

#: Tool arguments that name a domain entity, mapped to a POLE+O entity type.
ENTITY_ARG_TYPES: dict[str, str] = {
    "customer_id": "PERSON",
    "person_name": "PERSON",
    "entity_id": "ORGANIZATION",
    "entity_name": "ORGANIZATION",
    "organization_id": "ORGANIZATION",
    "organization_name": "ORGANIZATION",
    "transaction_id": "EVENT",
}


def entity_refs(args: dict[str, Any]) -> list[EntityRef]:
    """Build ``EntityRef``s from a tool call's arguments."""
    refs: list[EntityRef] = []
    seen: set[str] = set()
    for arg_name, entity_type in ENTITY_ARG_TYPES.items():
        value = args.get(arg_name)
        if not isinstance(value, str) or not value.strip() or value in seen:
            continue
        seen.add(value)
        refs.append(EntityRef(name=value, type=entity_type))
    return refs


class TraceWriter:
    """Writes a reasoning trace incrementally as run events arrive."""

    def __init__(self, memory_service: FinancialMemoryService, session_id: str) -> None:
        self._memory_service = memory_service
        self._session_id = session_id
        self._reasoning = memory_service.client.reasoning
        self._current_step_id: Any = None
        self.trace_id: Any = None
        self.step_count = 0
        self.tool_call_count = 0
        self.touched: list[EntityRef] = []

    async def start(self, task: str, triggered_by_message_id: Any = None) -> None:
        """Open the trace, linking it to the message that triggered the run."""
        try:
            trace = await self._reasoning.start_trace(
                session_id=self._session_id,
                task=task,
                generate_embedding=False,
                triggered_by_message_id=triggered_by_message_id,
            )
            self.trace_id = trace.id
        except Exception as exc:
            logger.error("Failed to start reasoning trace: %s", exc, exc_info=True)

    async def handle(self, run_event: RunEvent) -> None:
        """Record one normalised run event."""
        if self.trace_id is None:
            return
        try:
            if run_event.kind == "agent_start":
                step = await self._reasoning.add_step(
                    trace_id=self.trace_id,
                    thought=f"Agent {run_event.agent} activated",
                    action=f"Processing as {run_event.agent}",
                    generate_embedding=False,
                )
                self._current_step_id = step.id
                self.step_count += 1
            elif run_event.kind == "tool_result" and self._current_step_id is not None:
                refs = entity_refs(run_event.args)
                await self._reasoning.record_tool_call(
                    step_id=self._current_step_id,
                    tool_name=run_event.tool or "unknown",
                    arguments=run_event.args,
                    result=truncate_result(run_event.result),
                    status=ToolCallStatus.SUCCESS,
                    touched_entities=refs or None,
                )
                self.tool_call_count += 1
                known = {ref.name for ref in self.touched}
                self.touched.extend(ref for ref in refs if ref.name not in known)
        except Exception as exc:
            logger.error("Failed to record reasoning step: %s", exc, exc_info=True)

    async def complete(
        self,
        *,
        summary: str,
        success: bool,
        duration_ms: int,
        error_kind: str | None = None,
    ) -> None:
        """Close the trace with a structured outcome."""
        if self.trace_id is None:
            return
        try:
            await self._reasoning.complete_trace(
                trace_id=self.trace_id,
                outcome=TraceOutcome(
                    success=success,
                    summary=summary[:500],
                    error_kind=error_kind,
                    related_entities=self.touched,
                    metrics={
                        "tool_calls": float(self.tool_call_count),
                        "steps": float(self.step_count),
                        "duration_ms": float(duration_ms),
                    },
                ),
            )
        except Exception as exc:
            logger.error("Failed to complete reasoning trace: %s", exc, exc_info=True)
