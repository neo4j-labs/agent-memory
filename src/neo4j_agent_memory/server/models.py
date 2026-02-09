"""Request and response models for the Neo4j Agent Memory HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ContextRequest(BaseModel):
    """Request body for POST /context."""

    query: str = Field(description="Query to search for relevant context")
    session_id: str | None = Field(default=None, description="Session ID for short-term filtering")
    include_short_term: bool = Field(default=True, description="Include conversation history")
    include_long_term: bool = Field(default=True, description="Include entities and preferences")
    include_reasoning: bool = Field(default=True, description="Include reasoning traces")
    max_items: int = Field(default=10, ge=1, le=100, description="Max items per memory type")


class AddMessageRequest(BaseModel):
    """Request body for POST /sessions/{session_id}/messages."""

    role: str = Field(description="Message role: user, assistant, system, or tool")
    content: str = Field(description="Message content")
    conversation_id: str | None = Field(default=None, description="Parent conversation ID")
    extract_entities: bool = Field(default=True, description="Extract entities from message")
    generate_embedding: bool = Field(default=True, description="Generate embedding for search")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")


class SearchMessagesRequest(BaseModel):
    """Request body for POST /messages/search."""

    query: str = Field(description="Search query")
    session_id: str | None = Field(default=None, description="Filter by session ID")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum results")
    threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Similarity threshold")


class AddEntityRequest(BaseModel):
    """Request body for POST /entities."""

    name: str = Field(description="Entity name")
    entity_type: str = Field(
        description="Entity type (PERSON, OBJECT, LOCATION, EVENT, ORGANIZATION)"
    )
    subtype: str | None = Field(default=None, description="Entity subtype")
    description: str | None = Field(default=None, description="Entity description")
    aliases: list[str] | None = Field(default=None, description="Alternative names")
    attributes: dict[str, Any] | None = Field(default=None, description="Additional attributes")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")


class SearchEntitiesRequest(BaseModel):
    """Request body for POST /entities/search."""

    query: str = Field(description="Search query")
    entity_types: list[str] | None = Field(default=None, description="Filter by entity types")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum results")
    threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Similarity threshold")


class AddPreferenceRequest(BaseModel):
    """Request body for POST /preferences."""

    category: str = Field(description="Preference category")
    preference: str = Field(description="The preference statement")
    context: str | None = Field(default=None, description="When/where preference applies")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")


class SearchPreferencesRequest(BaseModel):
    """Request body for POST /preferences/search."""

    query: str = Field(description="Search query")
    category: str | None = Field(default=None, description="Filter by category")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum results")
    threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Similarity threshold")


class AddRelationshipRequest(BaseModel):
    """Request body for POST /relationships."""

    source_id: str = Field(description="Source entity ID")
    target_id: str = Field(description="Target entity ID")
    relationship_type: str = Field(description="Relationship type")
    description: str | None = Field(default=None, description="Relationship description")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")


class StartTraceRequest(BaseModel):
    """Request body for POST /traces."""

    session_id: str = Field(description="Session identifier")
    task: str = Field(description="Task description")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")


class AddStepRequest(BaseModel):
    """Request body for POST /traces/{trace_id}/steps."""

    thought: str | None = Field(default=None, description="Agent's thought/reasoning")
    action: str | None = Field(default=None, description="Action taken")
    observation: str | None = Field(default=None, description="Observation from action")


class CompleteTraceRequest(BaseModel):
    """Request body for POST /traces/{trace_id}/complete."""

    outcome: str | None = Field(default=None, description="Final outcome")
    success: bool | None = Field(default=None, description="Whether task succeeded")


class RecordToolCallRequest(BaseModel):
    """Request body for POST /tool-calls."""

    step_id: str = Field(description="Parent reasoning step ID")
    tool_name: str = Field(description="Name of the tool")
    arguments: dict[str, Any] = Field(description="Tool arguments")
    result: Any | None = Field(default=None, description="Tool result")
    status: str = Field(default="success", description="Call status")
    duration_ms: int | None = Field(default=None, description="Duration in milliseconds")
    error: str | None = Field(default=None, description="Error message if failed")


class SearchTracesRequest(BaseModel):
    """Request body for POST /traces/search."""

    query: str = Field(description="Task description to search for")
    limit: int = Field(default=5, ge=1, le=50, description="Maximum results")
    success_only: bool = Field(default=True, description="Only return successful traces")
    threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Similarity threshold")


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


def _str_id(val: UUID | str | None) -> str | None:
    """Convert UUID to string, pass through strings, return None for None."""
    if val is None:
        return None
    return str(val)


class MessageResponse(BaseModel):
    """A message in a conversation."""

    id: str = Field(description="Message ID")
    role: str = Field(description="Message role")
    content: str = Field(description="Message content")
    conversation_id: str | None = Field(default=None, description="Parent conversation ID")
    tool_calls: list[dict[str, Any]] | None = Field(default=None, description="Tool calls")
    created_at: datetime = Field(description="Creation time")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata")

    @classmethod
    def from_domain(cls, msg: Any) -> MessageResponse:
        return cls(
            id=str(msg.id),
            role=msg.role.value if hasattr(msg.role, "value") else str(msg.role),
            content=msg.content,
            conversation_id=_str_id(msg.conversation_id),
            tool_calls=msg.tool_calls,
            created_at=msg.created_at,
            metadata=msg.metadata or {},
        )


class SessionResponse(BaseModel):
    """Summary of a conversation session."""

    session_id: str = Field(description="Session identifier")
    title: str | None = Field(default=None, description="Session title")
    created_at: datetime = Field(description="Creation time")
    updated_at: datetime | None = Field(default=None, description="Last update time")
    message_count: int = Field(default=0, description="Number of messages")
    first_message_preview: str | None = Field(default=None, description="First message preview")
    last_message_preview: str | None = Field(default=None, description="Last message preview")

    @classmethod
    def from_domain(cls, session: Any) -> SessionResponse:
        return cls(
            session_id=session.session_id,
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
            message_count=session.message_count,
            first_message_preview=session.first_message_preview,
            last_message_preview=session.last_message_preview,
        )


class ConversationResponse(BaseModel):
    """A conversation with its messages."""

    id: str = Field(description="Conversation ID")
    session_id: str = Field(description="Session identifier")
    title: str | None = Field(default=None, description="Conversation title")
    messages: list[MessageResponse] = Field(default_factory=list, description="Messages")
    created_at: datetime = Field(description="Creation time")
    updated_at: datetime | None = Field(default=None, description="Last update time")

    @classmethod
    def from_domain(cls, conv: Any) -> ConversationResponse:
        return cls(
            id=str(conv.id),
            session_id=conv.session_id,
            title=conv.title,
            messages=[MessageResponse.from_domain(m) for m in conv.messages],
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )


class ConversationSummaryResponse(BaseModel):
    """Summary of a conversation."""

    session_id: str = Field(description="Session identifier")
    summary: str = Field(description="Generated summary text")
    message_count: int = Field(description="Number of messages summarized")
    key_entities: list[str] = Field(default_factory=list, description="Key entities")
    key_topics: list[str] = Field(default_factory=list, description="Key topics")
    generated_at: datetime = Field(description="When summary was generated")

    @classmethod
    def from_domain(cls, s: Any) -> ConversationSummaryResponse:
        return cls(
            session_id=s.session_id,
            summary=s.summary,
            message_count=s.message_count,
            key_entities=s.key_entities,
            key_topics=s.key_topics,
            generated_at=s.generated_at,
        )


class EntityResponse(BaseModel):
    """An entity from the knowledge graph."""

    id: str = Field(description="Entity ID")
    name: str = Field(description="Entity name")
    canonical_name: str | None = Field(default=None, description="Resolved canonical name")
    type: str = Field(description="Entity type")
    subtype: str | None = Field(default=None, description="Entity subtype")
    description: str | None = Field(default=None, description="Entity description")
    confidence: float = Field(description="Confidence score")
    aliases: list[str] = Field(default_factory=list, description="Alternative names")
    attributes: dict[str, Any] = Field(default_factory=dict, description="Additional attributes")
    created_at: datetime = Field(description="Creation time")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata")

    @classmethod
    def from_domain(cls, entity: Any) -> EntityResponse:
        entity_type = entity.type.value if hasattr(entity.type, "value") else str(entity.type)
        return cls(
            id=str(entity.id),
            name=entity.name,
            canonical_name=entity.canonical_name,
            type=entity_type,
            subtype=entity.subtype,
            description=entity.description,
            confidence=entity.confidence,
            aliases=entity.aliases or [],
            attributes=entity.attributes or {},
            created_at=entity.created_at,
            metadata=entity.metadata or {},
        )


class DeduplicationResultResponse(BaseModel):
    """Result of entity deduplication."""

    is_duplicate: bool = Field(description="Whether entity was a duplicate")
    action: str = Field(description="Action taken: none, merged, or flagged")
    matched_entity_id: str | None = Field(default=None, description="Matched entity ID")
    matched_entity_name: str | None = Field(default=None, description="Matched entity name")
    similarity_score: float = Field(description="Similarity score")
    match_type: str | None = Field(default=None, description="Type of match")

    @classmethod
    def from_domain(cls, result: Any) -> DeduplicationResultResponse:
        return cls(
            is_duplicate=result.is_duplicate,
            action=result.action,
            matched_entity_id=_str_id(result.matched_entity_id),
            matched_entity_name=result.matched_entity_name,
            similarity_score=result.similarity_score,
            match_type=result.match_type,
        )


class AddEntityResponse(BaseModel):
    """Response for adding an entity, includes deduplication info."""

    entity: EntityResponse = Field(description="The created or matched entity")
    deduplication: DeduplicationResultResponse = Field(description="Deduplication result")


class RelationshipResponse(BaseModel):
    """A relationship between entities."""

    id: str = Field(description="Relationship ID")
    source_id: str = Field(description="Source entity ID")
    target_id: str = Field(description="Target entity ID")
    type: str = Field(description="Relationship type")
    description: str | None = Field(default=None, description="Relationship description")
    confidence: float = Field(description="Confidence score")
    created_at: datetime = Field(description="Creation time")

    @classmethod
    def from_domain(cls, rel: Any) -> RelationshipResponse:
        return cls(
            id=str(rel.id),
            source_id=str(rel.source_id),
            target_id=str(rel.target_id),
            type=rel.type,
            description=rel.description,
            confidence=rel.confidence,
            created_at=rel.created_at,
        )


class PreferenceResponse(BaseModel):
    """A user preference."""

    id: str = Field(description="Preference ID")
    category: str = Field(description="Preference category")
    preference: str = Field(description="The preference statement")
    context: str | None = Field(default=None, description="When/where preference applies")
    confidence: float = Field(description="Confidence score")
    created_at: datetime = Field(description="Creation time")

    @classmethod
    def from_domain(cls, pref: Any) -> PreferenceResponse:
        return cls(
            id=str(pref.id),
            category=pref.category,
            preference=pref.preference,
            context=pref.context,
            confidence=pref.confidence,
            created_at=pref.created_at,
        )


class ToolCallResponse(BaseModel):
    """A tool call made during reasoning."""

    id: str = Field(description="Tool call ID")
    tool_name: str = Field(description="Tool name")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    result: Any | None = Field(default=None, description="Tool result")
    status: str = Field(description="Call status")
    duration_ms: int | None = Field(default=None, description="Duration in milliseconds")
    error: str | None = Field(default=None, description="Error message")

    @classmethod
    def from_domain(cls, tc: Any) -> ToolCallResponse:
        return cls(
            id=str(tc.id),
            tool_name=tc.tool_name,
            arguments=tc.arguments or {},
            result=tc.result,
            status=tc.status.value if hasattr(tc.status, "value") else str(tc.status),
            duration_ms=tc.duration_ms,
            error=tc.error,
        )


class StepResponse(BaseModel):
    """A reasoning step."""

    id: str = Field(description="Step ID")
    trace_id: str = Field(description="Parent trace ID")
    step_number: int = Field(description="Step number in sequence")
    thought: str | None = Field(default=None, description="Agent's thought")
    action: str | None = Field(default=None, description="Action taken")
    observation: str | None = Field(default=None, description="Observation")
    tool_calls: list[ToolCallResponse] = Field(default_factory=list, description="Tool calls")
    created_at: datetime = Field(description="Creation time")

    @classmethod
    def from_domain(cls, step: Any) -> StepResponse:
        return cls(
            id=str(step.id),
            trace_id=str(step.trace_id),
            step_number=step.step_number,
            thought=step.thought,
            action=step.action,
            observation=step.observation,
            tool_calls=[ToolCallResponse.from_domain(tc) for tc in (step.tool_calls or [])],
            created_at=step.created_at,
        )


class TraceResponse(BaseModel):
    """A reasoning trace."""

    id: str = Field(description="Trace ID")
    session_id: str = Field(description="Session identifier")
    task: str = Field(description="Task description")
    steps: list[StepResponse] = Field(default_factory=list, description="Reasoning steps")
    outcome: str | None = Field(default=None, description="Final outcome")
    success: bool | None = Field(default=None, description="Whether task succeeded")
    started_at: datetime = Field(description="Start time")
    completed_at: datetime | None = Field(default=None, description="Completion time")

    @classmethod
    def from_domain(cls, trace: Any) -> TraceResponse:
        return cls(
            id=str(trace.id),
            session_id=trace.session_id,
            task=trace.task,
            steps=[StepResponse.from_domain(s) for s in (trace.steps or [])],
            outcome=trace.outcome,
            success=trace.success,
            started_at=trace.started_at,
            completed_at=trace.completed_at,
        )


class ToolStatsResponse(BaseModel):
    """Tool usage statistics."""

    name: str = Field(description="Tool name")
    description: str | None = Field(default=None, description="Tool description")
    total_calls: int = Field(default=0, description="Total calls")
    successful_calls: int = Field(default=0, description="Successful calls")
    failed_calls: int = Field(default=0, description="Failed calls")
    success_rate: float = Field(default=0.0, description="Success rate")
    avg_duration_ms: float | None = Field(default=None, description="Average duration in ms")
    last_used_at: datetime | None = Field(default=None, description="Last usage time")

    @classmethod
    def from_domain(cls, stats: Any) -> ToolStatsResponse:
        return cls(
            name=stats.name,
            description=stats.description,
            total_calls=stats.total_calls,
            successful_calls=stats.successful_calls,
            failed_calls=stats.failed_calls,
            success_rate=stats.success_rate,
            avg_duration_ms=stats.avg_duration_ms,
            last_used_at=stats.last_used_at,
        )


class ContextResponse(BaseModel):
    """Unified context response combining all memory types."""

    context_text: str = Field(description="Pre-formatted context for system prompt injection")
    messages: list[MessageResponse] = Field(default_factory=list, description="Recent messages")
    entities: list[EntityResponse] = Field(default_factory=list, description="Relevant entities")
    preferences: list[PreferenceResponse] = Field(
        default_factory=list, description="Relevant preferences"
    )
    traces: list[TraceResponse] = Field(
        default_factory=list, description="Relevant reasoning traces"
    )
    stats: dict[str, int] = Field(default_factory=dict, description="Counts per memory type")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(description="Server status")
    memory_connected: bool = Field(description="Whether memory client is connected")
    version: str = Field(description="Package version")


class StatsResponse(BaseModel):
    """Memory statistics."""

    conversations: int = Field(default=0, description="Number of conversations")
    messages: int = Field(default=0, description="Number of messages")
    entities: int = Field(default=0, description="Number of entities")
    preferences: int = Field(default=0, description="Number of preferences")
    facts: int = Field(default=0, description="Number of facts")
    traces: int = Field(default=0, description="Number of reasoning traces")
