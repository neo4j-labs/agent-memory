"""Memory service for Neo4j Agent Memory integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from neo4j_agent_memory import ExtractionConfig, MemoryClient, MemorySettings
from neo4j_agent_memory.config import (
    EmbeddingConfig,
    EmbeddingProvider,
    LLMConfig,
    Neo4jConfig,
)
from neo4j_agent_memory.memory.long_term import DeduplicationConfig  # noqa: F401

from ..config import get_settings

logger = logging.getLogger(__name__)


class FinancialMemoryService:
    """Service for managing financial Context Graph operations.

    This service integrates with Neo4j Agent Memory to provide:
    - Long-term memory for customer entities and relationships
    - Short-term memory for conversation context
    - Reasoning memory for investigation audit trails
    """

    def __init__(self) -> None:
        """Initialize the memory service."""
        settings = get_settings()
        memory_settings = MemorySettings(
            neo4j=Neo4jConfig(
                uri=settings.neo4j.uri,
                username=settings.neo4j.user,
                password=settings.neo4j.password,
                database=settings.neo4j.database,
            ),
            embedding=EmbeddingConfig(
                provider=EmbeddingProvider.BEDROCK,
                model=settings.bedrock.embedding_model_id,
                aws_region=settings.aws.region,
            ),
            extraction=ExtractionConfig(),
            # Use Bedrock Anthropic Claude for entity extraction (native AWS integration)
            llm=LLMConfig(
                model="bedrock/anthropic.claude-3-sonnet-20240229-v1:0",
            ),
        )
        self._client = MemoryClient(memory_settings)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    @property
    def client(self) -> MemoryClient:
        """Expose the MemoryClient for Neo4jDomainService connection sharing."""
        return self._client

    async def initialize(self) -> None:
        """Initialize the memory client and create indexes. Thread-safe."""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._client.connect()
            self._initialized = True
            logger.info("Financial Memory Service initialized")

    async def close(self) -> None:
        """Close the memory client connection."""
        await self._client.close()
        self._initialized = False
        logger.info("Financial Memory Service closed")

    # ==========================================================================
    # Conversation Memory (Short-Term)
    # ==========================================================================

    async def add_conversation_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a message to conversation history."""
        await self._client.short_term.add_message(
            session_id=session_id,
            role=role,
            content=content,
            metadata=metadata or {},
        )

    async def get_conversation_history(
        self,
        session_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get conversation history for a session."""
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
        """Search conversation history semantically."""
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

    # ==========================================================================
    # Reasoning Memory (Investigation Audit Trail)
    # ==========================================================================

    async def start_investigation_trace(
        self,
        session_id: str,
        task: str,
    ) -> str:
        """Start a reasoning trace for an investigation.

        Returns:
            Trace ID (as string)
        """
        trace = await self._client.reasoning.start_trace(
            session_id=session_id,
            task=task,
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
        """Add a reasoning step to an investigation trace.

        Returns:
            Step ID (as string)
        """
        step = await self._client.reasoning.add_step(
            UUID(trace_id),
            thought=reasoning,
            action=action,
            observation=str(result) if result else None,
            metadata={"agent": agent},
        )
        return str(step.id)

    async def complete_investigation_trace(
        self,
        trace_id: str,
        conclusion: str,
        success: bool = True,
    ) -> None:
        """Complete an investigation reasoning trace."""
        await self._client.reasoning.complete_trace(
            UUID(trace_id),
            outcome=conclusion,
            success=success,
        )

    async def get_investigation_trace(
        self,
        trace_id: str,
    ) -> dict[str, Any] | None:
        """Get the full reasoning trace for an investigation."""
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

    # ==========================================================================
    # Context Search & Session Management
    # ==========================================================================

    async def search_context(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search across all memory types (messages, entities, preferences)."""
        results = await self._client.short_term.search_messages(
            query=query,
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

    async def store_finding(
        self,
        content: str,
        session_id: str = "default",
        category: str = "investigation",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store an investigation finding as a message."""
        await self._client.short_term.add_message(
            session_id=session_id,
            role="assistant",
            content=content,
            metadata={"category": category, **(metadata or {})},
        )

    async def add_session(
        self,
        session_id: str,
        messages: list[dict[str, str]],
        extract_entities: bool = True,
    ) -> None:
        """Store a batch of conversation messages with optional entity extraction.

        When extract_entities is True, the extraction pipeline (spaCy, GLiNER,
        LLM) runs on each message to populate the knowledge graph with entities
        like PERSON, ORGANIZATION, LOCATION mentioned in the conversation.
        """
        for msg in messages:
            await self._client.short_term.add_message(
                session_id=session_id,
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                extract_entities=extract_entities,
            )

    async def clear_session(self, session_id: str) -> None:
        """Clear all messages for a session (placeholder)."""
        logger.info(f"Clear session {session_id} requested (not yet implemented)")


# Global service instance
_memory_service: FinancialMemoryService | None = None


def get_memory_service() -> FinancialMemoryService:
    """Get the global memory service instance."""
    global _memory_service
    if _memory_service is None:
        _memory_service = FinancialMemoryService()
    return _memory_service
