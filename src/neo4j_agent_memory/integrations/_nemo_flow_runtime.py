"""NeMo Flow runtime, identity, and observability helpers."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from neo4j_agent_memory.integrations._nemo_flow_types import (
    IDENTITY_KEYS,
    NEMO_FLOW_EVENT_REGISTRY,
    NEMO_FLOW_SCOPE_REGISTRY,
    MemoryIdentity,
    NemoFlowEvent,
    NemoFlowIdentitySource,
    NemoFlowNeo4jConfig,
    NemoFlowScope,
)

logger = logging.getLogger(__name__)

memory_identity: ContextVar[MemoryIdentity | None] = ContextVar(
    "neo4j_agent_memory_nemo_flow_identity",
    default=None,
)


def load_nemo_flow() -> Any:
    try:
        import nemo_flow

        return nemo_flow
    except ImportError as exc:
        raise ImportError(
            "neo4j_agent_memory.integrations.nemo_flow requires NeMo Flow. "
            "Install it with `neo4j-agent-memory[nemo-flow]` before calling install()."
        ) from exc


def maybe_load_nemo_flow() -> Any | None:
    try:
        return load_nemo_flow()
    except ImportError:
        return None


def activate_runtime_module(nemo_flow_module: Any) -> None:
    try:
        nemo_flow_module.get_scope_stack()
    except Exception:
        logger.debug("Failed to activate NeMo Flow scope stack", exc_info=True)


def push_memory_context_scope(nemo_flow_module: Any, identity: MemoryIdentity) -> Any | None:
    activate_runtime_module(nemo_flow_module)
    try:
        definition = NEMO_FLOW_SCOPE_REGISTRY[NemoFlowScope.MEMORY]
        return nemo_flow_module.scope.push(
            definition.name.value,
            getattr(nemo_flow_module.ScopeType, definition.scope_type_name),
            metadata={
                "integration": "neo4j_agent_memory",
                "neo4j_agent_memory": identity_scope_metadata(identity),
            },
        )
    except Exception:
        logger.debug("Failed to push NeMo Flow Neo4j memory context scope", exc_info=True)
        return None


def pop_memory_context_scope(nemo_flow_module: Any | None, handle: Any | None) -> None:
    if nemo_flow_module is None or handle is None:
        return

    try:
        nemo_flow_module.scope.pop(handle)
    except Exception:
        logger.debug("Failed to pop NeMo Flow Neo4j memory context scope", exc_info=True)


def current_scope_metadata(nemo_flow_module: Any) -> Mapping[str, Any] | None:
    try:
        handle = nemo_flow_module.scope.get_handle()
    except Exception:
        return None
    metadata = getattr(handle, "metadata", None) if handle is not None else None
    return metadata if isinstance(metadata, Mapping) else None


def identity_from_metadata(metadata: Mapping[str, Any] | None) -> MemoryIdentity | None:
    if metadata is None:
        return None

    neo4j_metadata = metadata.get("neo4j_agent_memory")
    if isinstance(neo4j_metadata, Mapping):
        return MemoryIdentity.from_mapping(neo4j_metadata)

    shorthand_metadata = metadata.get("neo4j")
    if isinstance(shorthand_metadata, Mapping):
        return MemoryIdentity.from_mapping(shorthand_metadata)

    memory_metadata = metadata.get("memory")
    if isinstance(memory_metadata, Mapping) and memory_metadata.get("provider") in (
        None,
        "neo4j",
        "neo4j_agent_memory",
    ):
        return MemoryIdentity.from_mapping(memory_metadata)

    if any(key in metadata for key in IDENTITY_KEYS) or "thread_id" in metadata:
        return MemoryIdentity.from_mapping(metadata)

    return None


def coerce_identity(identity: MemoryIdentity | Mapping[str, Any] | None) -> MemoryIdentity | None:
    if identity is None or isinstance(identity, MemoryIdentity):
        return identity
    if isinstance(identity, Mapping):
        return MemoryIdentity.from_mapping(identity)
    raise TypeError("identity_resolver must return MemoryIdentity, mapping, or None")


def capture_metadata(
    identity: MemoryIdentity, metadata: Mapping[str, Any] | None
) -> dict[str, Any]:
    captured = dict(metadata or {})
    for key, value in identity.metadata().items():
        captured.setdefault(key, value)
    return captured


def resolve_run_id(*, run_id: str | None, thread_id: str | None) -> str | None:
    if run_id is not None and thread_id is not None and run_id != thread_id:
        raise ValueError("run_id and thread_id cannot both be set to different values")
    return run_id if run_id is not None else thread_id


def identity_scope_metadata(identity: MemoryIdentity) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if identity.user_id is not None:
        metadata["user_id"] = identity.user_id
    if identity.agent_id is not None:
        metadata["agent_id"] = identity.agent_id
    if identity.run_id is not None:
        metadata["run_id"] = identity.run_id
    if identity.session_id is not None:
        metadata["session_id"] = identity.session_id
    if identity.filters:
        metadata["filters"] = dict(identity.filters)
    return metadata


def identity_observability_metadata(identity: MemoryIdentity) -> dict[str, Any]:
    metadata = identity.metadata()
    return {
        "filter_keys": sorted(metadata),
        "has_user_id": identity.user_id is not None,
        "has_agent_id": identity.agent_id is not None,
        "has_run_id": identity.run_id is not None,
        "has_session_id": identity.session_id is not None,
    }


class NemoFlowTelemetry:
    """Small wrapper around NeMo Flow's scope and mark APIs."""

    def __init__(self, nemo_flow_module: Any, config: NemoFlowNeo4jConfig):
        self._nemo_flow = nemo_flow_module
        self._config = config

    def emit_identity_resolved(
        self,
        identity: MemoryIdentity,
        source: NemoFlowIdentitySource,
    ) -> None:
        if not self._config.emit_identity_events:
            return

        self.emit_event(
            NemoFlowEvent.IDENTITY_RESOLVED,
            data={
                "source": source.value,
                "has_scope": identity.has_scope(),
                "filter_count": len(identity.metadata()),
            },
            metadata=identity_observability_metadata(identity),
        )

    def start_scope(
        self,
        scope: NemoFlowScope,
        *,
        input: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Any | None:
        if not self._config.enable_observability:
            return None

        try:
            definition = NEMO_FLOW_SCOPE_REGISTRY[scope]
            scope_type = getattr(self._nemo_flow.ScopeType, definition.scope_type_name)
            return self._nemo_flow.scope.push(
                definition.name.value,
                scope_type,
                input=dict(input or {}),
                metadata={"integration": "neo4j_agent_memory", **dict(metadata or {})},
            )
        except Exception:
            logger.debug("Failed to start NeMo Flow Neo4j memory scope", exc_info=True)
            return None

    def end_scope(self, handle: Any | None, *, output: Mapping[str, Any] | None = None) -> None:
        if handle is None:
            return

        try:
            self._nemo_flow.scope.pop(handle, output=dict(output or {}))
        except Exception:
            logger.debug("Failed to end NeMo Flow Neo4j memory scope", exc_info=True)

    def emit_event(
        self,
        event: NemoFlowEvent,
        *,
        handle: Any | None = None,
        data: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not self._config.enable_observability or not self._config.emit_mark_events:
            return

        try:
            definition = NEMO_FLOW_EVENT_REGISTRY[event]
            self._nemo_flow.scope.event(
                definition.name.value,
                handle=handle,
                data=dict(data or {}),
                metadata={"integration": "neo4j_agent_memory", **dict(metadata or {})},
            )
        except Exception:
            logger.debug("Failed to emit NeMo Flow Neo4j memory mark event", exc_info=True)
