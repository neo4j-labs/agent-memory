"""Shared types for the optional NeMo Flow integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import nemo_flow


IDENTITY_KEYS = ("user_id", "agent_id", "run_id", "session_id")
RESERVED_IDENTITY_KEYS = {"filters", "provider", "thread_id"}


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


class NemoFlowScope(str, Enum):
    """Named NeMo Flow scopes emitted by the integration."""

    MEMORY = "neo4j_agent_memory.memory"
    RECALL = "neo4j_agent_memory.recall"
    CAPTURE = "neo4j_agent_memory.capture"


class NemoFlowEvent(str, Enum):
    """Named NeMo Flow mark events emitted by the integration."""

    IDENTITY_RESOLVED = "neo4j_agent_memory.identity.resolved"
    RECALL_SKIPPED = "neo4j_agent_memory.recall.skipped"
    RECALL_CONTEXT_BUILT = "neo4j_agent_memory.recall.context_built"
    RECALL_INJECTED = "neo4j_agent_memory.recall.injected"
    RECALL_FAILED = "neo4j_agent_memory.recall.failed"
    CAPTURE_SKIPPED = "neo4j_agent_memory.capture.skipped"
    CAPTURE_EXTRACTED = "neo4j_agent_memory.capture.extracted"
    CAPTURE_MESSAGE_STORED = "neo4j_agent_memory.capture.message_stored"
    CAPTURE_STORED = "neo4j_agent_memory.capture.stored"
    CAPTURE_FAILED = "neo4j_agent_memory.capture.failed"


class NemoFlowIdentitySource(str, Enum):
    """Where memory identity came from for an intercepted LLM call."""

    IDENTITY_RESOLVER = "identity_resolver"
    MEMORY_SCOPE = "memory_scope"
    SCOPE_METADATA = "scope_metadata"


class NemoFlowContextSource(str, Enum):
    """Context sources the adapter can observe during recall."""

    SESSION_HISTORY = "session_history"
    USER_MESSAGES = "user_messages"
    LONG_TERM = "long_term"
    REASONING = "reasoning"
    MEMORY_INTEGRATION = "memory_integration"
    CUSTOM = "custom"


class NemoFlowSkipReason(str, Enum):
    """Known reasons a recall or capture operation was skipped."""

    EMPTY_QUERY = "empty_query"
    NO_CONTEXT = "no_context"
    EMPTY_FORMATTED_CONTEXT = "empty_formatted_context"
    UNSUPPORTED_REQUEST_CONTENT = "unsupported_request_content"
    NO_SESSION_ID = "no_session_id"
    NO_INTERACTION = "no_interaction"
    EMPTY_CONTENT = "empty_content"


@dataclass(frozen=True)
class NemoFlowScopeDefinition:
    """Registry entry describing a NeMo Flow scope."""

    name: NemoFlowScope
    scope_type_name: str
    description: str


@dataclass(frozen=True)
class NemoFlowEventDefinition:
    """Registry entry describing a NeMo Flow mark event."""

    name: NemoFlowEvent
    description: str


NEMO_FLOW_SCOPE_REGISTRY: Mapping[NemoFlowScope, NemoFlowScopeDefinition] = {
    NemoFlowScope.MEMORY: NemoFlowScopeDefinition(
        NemoFlowScope.MEMORY,
        "Custom",
        "Lexical scope carrying Neo4j Agent Memory identity.",
    ),
    NemoFlowScope.RECALL: NemoFlowScopeDefinition(
        NemoFlowScope.RECALL,
        "Retriever",
        "Recall memory context before the provider LLM call.",
    ),
    NemoFlowScope.CAPTURE: NemoFlowScopeDefinition(
        NemoFlowScope.CAPTURE,
        "Custom",
        "Capture the user/assistant interaction after the provider LLM call.",
    ),
}

NEMO_FLOW_EVENT_REGISTRY: Mapping[NemoFlowEvent, NemoFlowEventDefinition] = {
    NemoFlowEvent.IDENTITY_RESOLVED: NemoFlowEventDefinition(
        NemoFlowEvent.IDENTITY_RESOLVED,
        "Memory identity was resolved for an intercepted LLM call.",
    ),
    NemoFlowEvent.RECALL_SKIPPED: NemoFlowEventDefinition(
        NemoFlowEvent.RECALL_SKIPPED,
        "Recall did not mutate the LLM request.",
    ),
    NemoFlowEvent.RECALL_CONTEXT_BUILT: NemoFlowEventDefinition(
        NemoFlowEvent.RECALL_CONTEXT_BUILT,
        "Recall built context from one or more memory sources.",
    ),
    NemoFlowEvent.RECALL_INJECTED: NemoFlowEventDefinition(
        NemoFlowEvent.RECALL_INJECTED,
        "Recall injected formatted memory context into the LLM request.",
    ),
    NemoFlowEvent.RECALL_FAILED: NemoFlowEventDefinition(
        NemoFlowEvent.RECALL_FAILED,
        "Recall raised an exception.",
    ),
    NemoFlowEvent.CAPTURE_SKIPPED: NemoFlowEventDefinition(
        NemoFlowEvent.CAPTURE_SKIPPED,
        "Capture did not store any interaction messages.",
    ),
    NemoFlowEvent.CAPTURE_EXTRACTED: NemoFlowEventDefinition(
        NemoFlowEvent.CAPTURE_EXTRACTED,
        "Capture extracted messages from the LLM request and response.",
    ),
    NemoFlowEvent.CAPTURE_MESSAGE_STORED: NemoFlowEventDefinition(
        NemoFlowEvent.CAPTURE_MESSAGE_STORED,
        "Capture stored one message.",
    ),
    NemoFlowEvent.CAPTURE_STORED: NemoFlowEventDefinition(
        NemoFlowEvent.CAPTURE_STORED,
        "Capture completed storage for the extracted interaction.",
    ),
    NemoFlowEvent.CAPTURE_FAILED: NemoFlowEventDefinition(
        NemoFlowEvent.CAPTURE_FAILED,
        "Capture raised an exception.",
    ),
}


@dataclass(frozen=True)
class MemoryIdentity:
    """Neo4j memory scope used by the NeMo Flow intercept."""

    user_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    filters: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> MemoryIdentity:
        """Build identity from user metadata or NeMo Flow scope metadata."""
        filters: dict[str, Any] = {}
        nested_filters = value.get("filters")
        if isinstance(nested_filters, Mapping):
            filters.update(
                {str(key): item for key, item in nested_filters.items() if item is not None}
            )

        for key, item in value.items():
            if key in IDENTITY_KEYS or key in RESERVED_IDENTITY_KEYS or item is None:
                continue
            filters[str(key)] = item

        run_id = value.get("run_id", value.get("thread_id"))
        return cls(
            user_id=string_or_none(value.get("user_id")),
            agent_id=string_or_none(value.get("agent_id")),
            run_id=string_or_none(run_id),
            session_id=string_or_none(value.get("session_id")),
            filters=filters,
        )

    def resolved_session_id(self) -> str | None:
        """Resolve the Neo4j short-term memory session ID."""
        if self.session_id:
            return self.session_id
        if self.user_id and self.run_id:
            return f"{self.user_id}:{self.run_id}"
        if self.agent_id and self.run_id:
            return f"{self.agent_id}:{self.run_id}"
        if self.run_id:
            return self.run_id
        if self.user_id:
            return self.user_id
        if self.agent_id:
            return self.agent_id
        return None

    def metadata(self) -> dict[str, Any]:
        """Metadata stored on captured messages and used for scoped search."""
        metadata = dict(self.filters)
        if self.user_id is not None:
            metadata["user_id"] = self.user_id
        if self.agent_id is not None:
            metadata["agent_id"] = self.agent_id
        if self.run_id is not None:
            metadata["run_id"] = self.run_id
        session_id = self.resolved_session_id()
        if session_id is not None:
            metadata["session_id"] = session_id
        return metadata

    def has_scope(self) -> bool:
        """Return whether enough identity exists to scope Neo4j memory."""
        return self.resolved_session_id() is not None


@dataclass(frozen=True)
class NemoFlowRecallContext:
    """Recall context text plus observable source statistics."""

    text: str
    source_counts: Mapping[str, int] = field(default_factory=dict)
    source_chars: Mapping[str, int] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def context_chars(self) -> int:
        """Number of characters in the assembled context text."""
        return len(self.text)

    def to_event_data(self) -> dict[str, Any]:
        """Convert context statistics into NeMo Flow mark-event data."""
        return {
            "context_chars": self.context_chars,
            "sources": sorted(self.source_counts),
            "source_counts": dict(self.source_counts),
            "source_chars": dict(self.source_chars),
            **dict(self.metadata),
        }


@dataclass(frozen=True)
class ResolvedMemoryIdentity:
    identity: MemoryIdentity
    source: NemoFlowIdentitySource


@dataclass(frozen=True)
class StoredMessageResult:
    role: str
    content_chars: int
    stored: bool
    result_id: str | None = None
    result_type: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class MemoryInjection:
    content: Any
    mutated: bool
    message_count_before: int = 0
    message_count_after: int = 0
    insert_at: int | None = None


@dataclass(frozen=True)
class NemoFlowTurnContext:
    """Context passed to a custom identity resolver."""

    llm_name: str
    request: nemo_flow.LLMRequest
    scope_metadata: Mapping[str, Any] | None


IdentityResolver = Callable[[NemoFlowTurnContext], MemoryIdentity | Mapping[str, Any] | None]
QueryExtractor = Callable[["nemo_flow.LLMRequest"], str | None]
InteractionExtractor = Callable[
    ["nemo_flow.LLMRequest", "nemo_flow.Json"], Sequence[Mapping[str, Any]] | None
]
ContextBuilder = Callable[[Any, str, MemoryIdentity], Any]
ContextFormatter = Callable[[str], str]


@dataclass(frozen=True)
class NemoFlowNeo4jConfig:
    """Configuration for the Neo4j Agent Memory NeMo Flow intercept."""

    name: str = "neo4j_agent_memory.memory"
    priority: int = 50
    auto_recall: bool = True
    auto_capture: bool = True
    max_items: int = 10
    top_k: int = 5
    threshold: float | None = 0.7
    include_session_history: bool = True
    include_user_messages: bool = True
    include_long_term: bool = False
    include_reasoning: bool = False
    extract_entities: bool = True
    extract_relations: bool = True
    generate_embeddings: bool = True
    metadata: Mapping[str, Any] | None = None
    fail_open: bool = True
    enable_observability: bool = True
    emit_mark_events: bool = True
    emit_identity_events: bool = True
    run_sync_in_thread: bool = False
    identity_resolver: IdentityResolver | None = None
    query_extractor: QueryExtractor | None = None
    interaction_extractor: InteractionExtractor | None = None
    context_builder: ContextBuilder | None = None
    context_formatter: ContextFormatter | None = None
