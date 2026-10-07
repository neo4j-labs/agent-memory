"""Request and response shapes of the HTTP API.

The frontend is written against these shapes; change them together.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class OntologyStatus(BaseModel):
    domain_id: str | None = None
    revision: int | None = None
    validation_mode: str | None = None


class Health(BaseModel):
    status: str = "ok"
    neo4j: bool
    agent_model: str
    ontology: OntologyStatus


# ---------------------------------------------------------------------------
# Threads and chat
# ---------------------------------------------------------------------------


class CreateThreadRequest(BaseModel):
    title: str | None = None


class ThreadSummary(BaseModel):
    id: str
    title: str
    message_count: int
    updated_at: str | None = None
    seeded: bool


class ThreadMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str | None = None
    trace_id: str | None = None
    #: User messages: the tool calls of the trace they initiated, so a past
    #: turn shows the same tool cards it showed while streaming.
    tool_calls: list[ToolCallView] = Field(default_factory=list)


class Thread(BaseModel):
    id: str
    title: str
    seeded: bool
    messages: list[ThreadMessage]


class ChatRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    message: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class MemoryEntity(BaseModel):
    id: str
    name: str
    type: str | None = None
    subtype: str | None = None
    labels: list[str]
    aliases: list[str]
    mentions: int


class EntityStub(BaseModel):
    id: str
    name: str
    labels: list[str]


class PendingDuplicate(BaseModel):
    source: EntityStub
    target: EntityStub
    confidence: float
    match_type: str | None = None


class MemoryContext(BaseModel):
    entities: list[MemoryEntity]
    pending_duplicates: list[PendingDuplicate]


class ReviewRequest(BaseModel):
    source_id: str
    target_id: str
    confirm: bool


class Ok(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


class GraphNode(BaseModel):
    id: str
    caption: str
    labels: list[str]
    kind: Literal["entity", "message", "conversation"]
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphRelationship(BaseModel):
    id: str
    # ``from`` is a Python keyword: the field is ``from_`` in code, ``from`` on the wire.
    from_: str = Field(alias="from", serialization_alias="from")
    to: str
    type: str
    caption: str
    properties: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class Graph(BaseModel):
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------


class EntityTypeView(BaseModel):
    label: str
    pole_type: str
    subtype: str | None = None
    description: str | None = None


class RelationshipView(BaseModel):
    type: str
    source: str
    target: str


class ActiveOntologyView(BaseModel):
    ontology_id: str | None = None
    version_id: str | None = None
    revision: int | None = None
    validation_mode: str | None = None
    domain_id: str | None = None
    entity_types: list[EntityTypeView]
    relationships: list[RelationshipView]


class ClientBinding(BaseModel):
    domain_id: str | None = None
    validation_mode: str | None = None


class RevisionView(BaseModel):
    version_id: str
    revision: int
    validation_mode: str
    is_active: bool
    created_at: str | None = None
    labels: list[str]


class OntologyOverview(BaseModel):
    active: ActiveOntologyView | None = None
    client: ClientBinding
    revisions: list[RevisionView]
    label_counts: dict[str, int]


class DiffSection(BaseModel):
    added: list[Any] = Field(default_factory=list)
    removed: list[Any] = Field(default_factory=list)
    renamed: list[Any] = Field(default_factory=list)
    modified: list[Any] = Field(default_factory=list)


class ModeChange(BaseModel):
    # ``from`` is a Python keyword, as on GraphRelationship.
    from_: str | None = Field(default=None, alias="from", serialization_alias="from")
    to: str | None = None

    model_config = {"populate_by_name": True}


class OntologyDiffView(BaseModel):
    from_revision: int | None = None
    to_revision: int | None = None
    entity_types: DiffSection
    relationships: DiffSection
    mode_change: ModeChange | None = None


class RenameRequest(BaseModel):
    # The demo's revision (src.ontology.RENAME_FROM / RENAME_TO).
    old: str = Field(default="Warranty", min_length=1)
    new: str = Field(default="WarrantyCoverage", min_length=1)
    validation_mode: Literal["permissive", "strict"] = "strict"


class MigrationView(BaseModel):
    id: str
    status: str | None = None
    total: int | None = None
    processed: int | None = None
    errored: int | None = None


class RenameResult(BaseModel):
    revision: int
    version_id: str
    diff: OntologyDiffView
    dry_run_total: int
    migration: MigrationView
    client: ClientBinding


class ActivateRequest(BaseModel):
    version_id: str = Field(min_length=1)


class ActivateResult(BaseModel):
    revision: int
    validation_mode: str
    client: ClientBinding


# ---------------------------------------------------------------------------
# Reasoning traces
# ---------------------------------------------------------------------------


class TraceSummary(BaseModel):
    id: str
    task: str
    started_at: str | None = None
    completed_at: str | None = None
    success: bool | None = None
    outcome_summary: str | None = None
    step_count: int
    tool_call_count: int
    message_id: str | None = None
    seeded: bool


class TouchedEntity(BaseModel):
    id: str
    name: str | None = None
    labels: list[str]


class ToolCallView(BaseModel):
    id: str
    tool_name: str
    arguments: dict[str, Any]
    result: Any = None
    status: str
    duration_ms: int | None = None
    touched: list[TouchedEntity]


class StepView(BaseModel):
    id: str
    thought: str | None = None
    action: str | None = None
    observation: str | None = None
    tool_calls: list[ToolCallView]


class TraceDetail(BaseModel):
    id: str
    task: str
    success: bool | None = None
    outcome_summary: str | None = None
    metrics: dict[str, Any]
    seeded: bool
    steps: list[StepView]


class SimilarTrace(BaseModel):
    id: str
    task: str
    success: bool | None = None
    outcome_summary: str | None = None
    seeded: bool


class ToolStat(BaseModel):
    name: str
    calls: int
    success_rate: float
    avg_duration_ms: float | None = None
