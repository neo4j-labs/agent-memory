"""Neo4j Agent Memory integration for the Financial Services Advisor.

Thin wrapper around :class:`~neo4j_agent_memory.MemoryClient` exposing the
three memory tiers this app uses:

* **short-term** — one ``:Conversation`` per chat session, written exactly once
  per turn (``extract_entities=True`` populates long-term memory from the same
  write; a second ``add_message`` for the same turn would corrupt the
  ``FIRST_MESSAGE`` / ``NEXT_MESSAGE`` chain and double every message count).
* **long-term** — screening outcomes recorded as facts, so a second
  investigation of the same customer can cite the first one's result.
* **reasoning** — a trace per turn, with one step per delegation and a
  ``ToolCall`` per tool invocation, carrying ``(:ReasoningStep)-[:TOUCHED]->(:Entity)``
  audit edges.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from neo4j_agent_memory import ExtractionConfig, MemoryClient, MemorySettings
from neo4j_agent_memory.config import Neo4jConfig
from neo4j_agent_memory.llm import from_provider
from neo4j_agent_memory.memory.reasoning import ToolCallStatus
from neo4j_agent_memory.schema import EntityRef, TraceOutcome
from pydantic import SecretStr

from ..config import get_settings

logger = logging.getLogger(__name__)

#: Predicate used for every sanctions/PEP screening fact.
SCREENED_AGAINST = "SCREENED_AGAINST"


class FinancialMemoryService:
    """Owns the ``MemoryClient`` lifecycle and the app's memory vocabulary."""

    def __init__(self) -> None:
        settings = get_settings()
        # v0.3+: build a BedrockEmbeddingProvider via from_provider. The
        # adapter reads the standard boto3 credential chain plus the
        # explicit aws_region we pass through.
        embedding_provider = from_provider(
            f"bedrock/{settings.bedrock.embedding_model_id}",
            kind="embedding",
            aws_region=settings.aws.region,
        )
        memory_settings = MemorySettings(
            neo4j=Neo4jConfig(
                uri=settings.neo4j.uri,
                username=settings.neo4j.user,
                password=SecretStr(settings.neo4j.password),
                database=settings.neo4j.database,
            ),
            embedding=embedding_provider,
            extraction=ExtractionConfig(),
        )
        self._client = MemoryClient(memory_settings)
        self._connected = False
        self._init_lock = asyncio.Lock()

    @property
    def client(self) -> MemoryClient:
        """Expose the MemoryClient (domain service, Cypher reads, tools)."""
        return self._client

    @property
    def connected(self) -> bool:
        """Whether :meth:`initialize` has completed. Read by ``/health``."""
        return self._connected

    async def initialize(self) -> None:
        """Connect and create indexes. Safe to call concurrently."""
        if self._connected:
            return
        async with self._init_lock:
            if self._connected:
                return
            await self._client.connect()
            self._connected = True
            logger.info("Financial memory service connected")

    async def close(self) -> None:
        await self._client.close()
        self._connected = False
        logger.info("Financial memory service closed")

    async def ping(self) -> bool:
        """Round-trip to the backend. ``client.query`` works on bolt and NAMS."""
        rows = await self._client.query.cypher("RETURN 1 AS ok")
        return bool(rows and rows[0].get("ok") == 1)

    # ------------------------------------------------------------------
    # Short-term memory
    # ------------------------------------------------------------------

    async def add_conversation_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        *,
        extract_entities: bool = False,
    ) -> Any:
        """Store one message and return it (its ``id`` links traces and tool calls).

        This is the single write path for a conversation turn. ``extract_entities``
        runs the configured extraction pipeline on this message, which is what
        populates long-term entities — it is not a second write.
        """
        return await self._client.short_term.add_message(
            session_id=session_id,
            role=role,
            content=content,
            metadata=metadata or {},
            extract_entities=extract_entities,
        )

    async def get_conversation_history(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        conversation = await self._client.short_term.get_conversation(
            session_id=session_id,
            limit=limit,
        )
        return [
            {
                "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                "content": m.content,
                "timestamp": m.created_at.isoformat() if m.created_at else None,
                "metadata": m.metadata,
            }
            for m in conversation.messages
        ]

    async def search_conversations(
        self,
        query: str,
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        results = await self._client.short_term.search_messages(
            query=query,
            session_id=session_id,
            limit=limit,
        )
        return [
            {
                "content": r.content,
                "role": r.role.value if hasattr(r.role, "value") else str(r.role),
                "metadata": r.metadata,
            }
            for r in results
        ]

    # ------------------------------------------------------------------
    # Long-term memory
    # ------------------------------------------------------------------

    async def record_screening(
        self,
        subject: str,
        screened_list: str,
        *,
        result: str,
        confidence: float = 1.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record a sanctions/PEP screening outcome as a long-term fact.

        Facts are deduplicated on subject+predicate, so repeat screenings update
        the existing fact rather than piling up. :meth:`prior_screenings` reads
        them back at the start of the next investigation.
        """
        await self._client.long_term.add_fact(
            subject=subject,
            predicate=SCREENED_AGAINST,
            obj=screened_list,
            confidence=confidence,
            metadata={
                "result": result,
                "checked_at": datetime.now(UTC).isoformat(),
                **(details or {}),
            },
        )

    async def prior_screenings(self, subject: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Screenings already recorded for ``subject``."""
        facts = await self._client.long_term.get_facts_about(subject)
        screenings = [
            {
                "subject": fact.subject,
                "list": fact.object,
                "result": (fact.metadata or {}).get("result"),
                "checked_at": (fact.metadata or {}).get("checked_at"),
                "confidence": fact.confidence,
            }
            for fact in facts
            if fact.predicate == SCREENED_AGAINST
        ]
        return screenings[:limit]

    async def set_analyst_preference(
        self,
        preference: str,
        *,
        category: str = "investigation",
        context: str | None = None,
    ) -> None:
        """Record how an analyst wants investigations run.

        Preferences are deduplicated per category, so "prefers depth-3
        ownership traces" stated twice stays one node.
        """
        await self._client.long_term.add_preference(
            category=category, preference=preference, context=context
        )

    async def analyst_preferences(
        self, *, category: str = "investigation", limit: int = 20
    ) -> list[dict[str, Any]]:
        """Read back the recorded investigation preferences for a category."""
        preferences = await self._client.long_term.search_preferences(
            category, category=category, limit=limit
        )
        return [
            {
                "category": p.category,
                "preference": p.preference,
                "context": p.context,
                "confidence": p.confidence,
            }
            for p in preferences
        ]

    # ------------------------------------------------------------------
    # Reasoning memory
    # ------------------------------------------------------------------

    async def start_investigation_trace(
        self,
        session_id: str,
        task: str,
        *,
        triggered_by_message_id: UUID | str | None = None,
    ) -> str:
        """Open a trace, optionally linked to the message that triggered it."""
        trace = await self._client.reasoning.start_trace(
            session_id=session_id,
            task=task,
            triggered_by_message_id=triggered_by_message_id,
        )
        return str(trace.id)

    async def add_reasoning_step(
        self,
        trace_id: str,
        agent: str,
        action: str,
        reasoning: str,
        result: dict[str, Any] | None = None,
    ) -> str:
        step = await self._client.reasoning.add_step(
            UUID(trace_id),
            thought=reasoning,
            action=action,
            observation=str(result) if result else None,
            metadata={"agent": agent},
        )
        return str(step.id)

    async def record_tool_call(
        self,
        step_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        result: Any = None,
        error: str | None = None,
        duration_ms: int | None = None,
        message_id: UUID | str | None = None,
        touched_entities: list[EntityRef] | None = None,
    ) -> None:
        """Record one tool call under a step, with its audit edges.

        ``touched_entities`` materialises ``(:ReasoningStep)-[:TOUCHED]->(:Entity)``
        so "which customers did this investigation touch?" is a one-hop query
        instead of a three-hop join through tool-call arguments.
        """
        await self._client.reasoning.record_tool_call(
            UUID(step_id),
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            status=ToolCallStatus.ERROR if error else ToolCallStatus.SUCCESS,
            error=error,
            duration_ms=duration_ms,
            message_id=message_id,
            touched_entities=touched_entities or None,
        )

    async def complete_investigation_trace(
        self,
        trace_id: str,
        conclusion: str,
        success: bool = True,
        *,
        error_kind: str | None = None,
        related_entities: list[EntityRef] | None = None,
        metrics: dict[str, float] | None = None,
    ) -> None:
        """Close a trace with a structured :class:`TraceOutcome`."""
        await self._client.reasoning.complete_trace(
            UUID(trace_id),
            outcome=TraceOutcome(
                success=success,
                summary=conclusion,
                error_kind=error_kind,
                related_entities=related_entities or [],
                metrics=metrics or {},
            ),
        )

    async def get_investigation_trace(self, trace_id: str) -> dict[str, Any] | None:
        trace = await self._client.reasoning.get_trace(trace_id)
        if not trace:
            return None

        return {
            "trace_id": str(trace.id),
            "task": trace.task,
            "outcome": trace.outcome,
            "success": trace.success,
            "started_at": trace.started_at.isoformat() if trace.started_at else None,
            "completed_at": trace.completed_at.isoformat() if trace.completed_at else None,
            "steps": [
                {
                    "step_id": str(s.id),
                    "step_number": s.step_number,
                    "thought": s.thought,
                    "action": s.action,
                    "observation": s.observation,
                    "metadata": s.metadata,
                    "tool_calls": [
                        {
                            "tool_name": tc.tool_name,
                            "arguments": tc.arguments,
                            "result": tc.result,
                            "status": tc.status.value
                            if hasattr(tc.status, "value")
                            else str(tc.status),
                            "duration_ms": tc.duration_ms,
                        }
                        for tc in (s.tool_calls or [])
                    ],
                }
                for s in (trace.steps or [])
            ],
            "metadata": trace.metadata,
        }

    async def audit_trail(self, trace_id: str) -> list[dict[str, Any]]:
        """Entities a trace touched, in one hop from the ``:TOUCHED`` edges.

        This is the query the whole "explainable AI" claim rests on: given a
        trace, which real-world entities did the agents act on, via which tool?
        """
        return await self._client.query.cypher(
            """
            MATCH (rt:ReasoningTrace {id: $trace_id})-[:HAS_STEP]->(s:ReasoningStep)
            MATCH (s)-[:TOUCHED]->(e:Entity)
            OPTIONAL MATCH (s)-[:USES_TOOL]->(tc:ToolCall)
            RETURN e.name AS entity, e.type AS entity_type, e.id AS entity_id,
                   s.step_number AS step_number, s.action AS action,
                   collect(DISTINCT tc.tool_name) AS tools
            ORDER BY step_number
            """,
            {"trace_id": str(trace_id)},
        )

    # ------------------------------------------------------------------

    async def search_context(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search conversation memory (messages) semantically."""
        return await self.search_conversations(query, limit=limit)

    async def store_finding(
        self,
        content: str,
        session_id: str = "default",
        category: str = "investigation",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store an investigation finding as an assistant message."""
        await self._client.short_term.add_message(
            session_id=session_id,
            role="assistant",
            content=content,
            metadata={"category": category, **(metadata or {})},
        )


# Global service instance
_memory_service: FinancialMemoryService | None = None


def get_memory_service() -> FinancialMemoryService:
    """Get the process-wide memory service instance."""
    global _memory_service
    if _memory_service is None:
        _memory_service = FinancialMemoryService()
    return _memory_service


def reset_memory_service() -> None:
    """Drop the cached instance (tests, and lifespan shutdown)."""
    global _memory_service
    _memory_service = None
