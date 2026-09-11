"""Pydantic schemas for API request/response models."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# Chat schemas
class ChatRequest(BaseModel):
    """Request model for chat endpoint."""

    thread_id: str = Field(..., description="The conversation thread ID")
    message: str = Field(..., description="The user's message")
    memory_enabled: bool = Field(default=True, description="Whether to use memory")


class ToolCallInfo(BaseModel):
    """Information about a tool call."""

    id: str
    name: str
    args: dict[str, Any]
    result: Any | None = None
    status: str = "pending"
    duration_ms: float | None = None


class ChatMessage(BaseModel):
    """A chat message."""

    id: str
    role: str
    content: str
    timestamp: datetime
    tool_calls: list[ToolCallInfo] = Field(default_factory=list)


# Thread schemas
class CreateThreadRequest(BaseModel):
    """Request model for creating a thread."""

    title: str | None = Field(default=None, description="Optional thread title")


class ThreadSummary(BaseModel):
    """Summary of a conversation thread."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class Thread(BaseModel):
    """Full thread with messages."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessage] = Field(default_factory=list)


# Preference schemas
class PreferenceRequest(BaseModel):
    """Request model for adding a preference."""

    category: str = Field(..., description="Preference category")
    preference: str = Field(..., description="The preference value")
    context: str | None = Field(default=None, description="Optional context")


class Preference(BaseModel):
    """A user preference."""

    id: str
    category: str
    preference: str
    context: str | None = None
    confidence: float = 1.0
    created_at: datetime | None = None


# Entity schemas
class Entity(BaseModel):
    """An extracted entity."""

    id: str
    name: str
    type: str
    subtype: str | None = None
    description: str | None = None
    enriched_description: str | None = None
    wikipedia_url: str | None = None
    image_url: str | None = None


# Memory context schemas
class RecentMessage(BaseModel):
    """A recent message from short-term memory."""

    id: str
    role: str
    content: str
    created_at: str | None = None


class MemoryContext(BaseModel):
    """Memory context for display."""

    preferences: list[Preference] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    recent_topics: list[str] = Field(default_factory=list)
    recent_messages: list[RecentMessage] = Field(default_factory=list)


# Graph visualization schemas
class GraphNode(BaseModel):
    """A node in the memory graph."""

    id: str
    labels: list[str]
    properties: dict[str, Any]


class GraphRelationship(BaseModel):
    """A relationship in the memory graph."""

    id: str
    from_node: str = Field(..., alias="from")
    to_node: str = Field(..., alias="to")
    type: str
    properties: dict[str, Any]

    model_config = ConfigDict(populate_by_name=True)


class MemoryGraph(BaseModel):
    """The complete memory graph for visualization."""

    nodes: list[GraphNode] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)


# Reasoning memory schemas
class ToolCallResponse(BaseModel):
    """A tool call recorded in reasoning memory."""

    id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any | None = None
    status: str = "success"
    duration_ms: int | None = None
    error: str | None = None


class ReasoningStepResponse(BaseModel):
    """A reasoning step in a trace."""

    id: str
    step_number: int
    thought: str | None = None
    action: str | None = None
    observation: str | None = None
    tool_calls: list[ToolCallResponse] = Field(default_factory=list)


class ReasoningTraceResponse(BaseModel):
    """A complete reasoning trace."""

    id: str
    session_id: str
    task: str
    outcome: str | None = None
    success: bool | None = None
    started_at: str | None = None
    completed_at: str | None = None
    step_count: int = 0
    steps: list[ReasoningStepResponse] = Field(default_factory=list)
    similarity: float | None = None  # For similar trace searches


class ToolStatsResponse(BaseModel):
    """Statistics for a tool's usage."""

    name: str
    description: str | None = None
    success_rate: float = 0.0
    avg_duration_ms: float = 0.0
    total_calls: int = 0


# Location schemas for map view
class ConversationRef(BaseModel):
    """Reference to a conversation/episode."""

    id: str
    title: str | None = None


class LocationEntity(BaseModel):
    """A location entity with coordinates for map display."""

    id: str
    name: str
    subtype: str | None = None
    description: str | None = None
    enriched_description: str | None = None
    wikipedia_url: str | None = None
    latitude: float
    longitude: float
    conversations: list[ConversationRef] = Field(default_factory=list)
    distance_km: float | None = Field(
        default=None, description="Distance from search point (for nearby queries)"
    )
