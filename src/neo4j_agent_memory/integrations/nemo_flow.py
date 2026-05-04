"""Optional NeMo Flow integration for Neo4j Agent Memory.

This module is intentionally the public facade. The implementation lives in
private modules so the user-facing API stays small and stable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._nemo_flow_intercept import Neo4jNemoFlowIntercept
from neo4j_agent_memory.integrations._nemo_flow_runtime import (
    activate_runtime_module,
    load_nemo_flow,
    maybe_load_nemo_flow,
    memory_identity,
    pop_memory_context_scope,
    push_memory_context_scope,
    resolve_run_id,
)
from neo4j_agent_memory.integrations._nemo_flow_types import (
    NEMO_FLOW_EVENT_REGISTRY,
    NEMO_FLOW_SCOPE_REGISTRY,
    ContextBuilder,
    ContextFormatter,
    IdentityResolver,
    InteractionExtractor,
    MemoryIdentity,
    NemoFlowContextSource,
    NemoFlowEvent,
    NemoFlowEventDefinition,
    NemoFlowIdentitySource,
    NemoFlowNeo4jConfig,
    NemoFlowRecallContext,
    NemoFlowScope,
    NemoFlowScopeDefinition,
    NemoFlowSkipReason,
    NemoFlowTurnContext,
    QueryExtractor,
)

if TYPE_CHECKING:
    import nemo_flow


class NemoFlowNeo4jHandle:
    """Registration handle returned by `install`."""

    def __init__(
        self,
        name: str,
        nemo_flow_module: Any,
        intercept: Neo4jNemoFlowIntercept,
    ):
        self.name = name
        self._nemo_flow = nemo_flow_module
        self._intercept = intercept
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    @property
    def intercept(
        self,
    ) -> Callable[
        [str, nemo_flow.LLMRequest, Callable[[nemo_flow.LLMRequest], Any]],
        Any,
    ]:
        return self._intercept

    def uninstall(self) -> bool:
        """Deregister the NeMo Flow execution intercept."""
        if not self._active:
            return False
        removed = self._nemo_flow.intercepts.deregister_llm_execution(self.name)
        self._active = False
        return bool(removed)


@contextmanager
def memory_scope(
    user_id: str | None = None,
    *,
    agent_id: str | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    session_id: str | None = None,
    filters: Mapping[str, Any] | None = None,
    activate_runtime: bool = True,
) -> Iterator[MemoryIdentity]:
    """Set Neo4j memory identity for framework calls in the current scope.

    If NeMo Flow is installed, this also pushes a NeMo Flow scope so
    instrumented LLM calls can find the same identity without application code
    importing NeMo Flow directly. ``thread_id`` is a LangGraph-friendly alias
    for ``run_id``.
    """
    run_id = resolve_run_id(run_id=run_id, thread_id=thread_id)
    identity = MemoryIdentity(
        user_id=user_id,
        agent_id=agent_id,
        run_id=run_id,
        session_id=session_id,
        filters=filters or {},
    )
    token = memory_identity.set(identity)
    nemo_flow_module = maybe_load_nemo_flow() if activate_runtime else None
    scope_handle = (
        push_memory_context_scope(nemo_flow_module, identity)
        if nemo_flow_module is not None and identity.has_scope()
        else None
    )
    try:
        yield identity
    finally:
        pop_memory_context_scope(nemo_flow_module, scope_handle)
        memory_identity.reset(token)


memory_context = memory_scope
neo4j_memory_context = memory_scope


def install(
    memory: Any,
    *,
    name: str = "neo4j_agent_memory.memory",
    priority: int = 50,
    auto_recall: bool = True,
    auto_capture: bool = True,
    max_items: int = 10,
    top_k: int = 5,
    threshold: float | None = 0.7,
    include_session_history: bool = True,
    include_user_messages: bool = True,
    include_long_term: bool = False,
    include_reasoning: bool = False,
    extract_entities: bool = True,
    extract_relations: bool = True,
    generate_embeddings: bool = True,
    metadata: Mapping[str, Any] | None = None,
    fail_open: bool = True,
    enable_observability: bool = True,
    emit_mark_events: bool = True,
    emit_identity_events: bool = True,
    activate_runtime: bool = True,
    run_sync_in_thread: bool = False,
    identity_resolver: IdentityResolver | None = None,
    query_extractor: QueryExtractor | None = None,
    interaction_extractor: InteractionExtractor | None = None,
    context_builder: ContextBuilder | None = None,
    context_formatter: ContextFormatter | None = None,
) -> NemoFlowNeo4jHandle:
    """Register Neo4j Agent Memory on NeMo Flow's LLM execution path."""
    nemo_flow_module = load_nemo_flow()
    if activate_runtime:
        activate_runtime_module(nemo_flow_module)
    config = NemoFlowNeo4jConfig(
        name=name,
        priority=priority,
        auto_recall=auto_recall,
        auto_capture=auto_capture,
        max_items=max_items,
        top_k=top_k,
        threshold=threshold,
        include_session_history=include_session_history,
        include_user_messages=include_user_messages,
        include_long_term=include_long_term,
        include_reasoning=include_reasoning,
        extract_entities=extract_entities,
        extract_relations=extract_relations,
        generate_embeddings=generate_embeddings,
        metadata=metadata,
        fail_open=fail_open,
        enable_observability=enable_observability,
        emit_mark_events=emit_mark_events,
        emit_identity_events=emit_identity_events,
        run_sync_in_thread=run_sync_in_thread,
        identity_resolver=identity_resolver,
        query_extractor=query_extractor,
        interaction_extractor=interaction_extractor,
        context_builder=context_builder,
        context_formatter=context_formatter,
    )
    intercept = Neo4jNemoFlowIntercept(memory, config, nemo_flow_module)
    nemo_flow_module.intercepts.register_llm_execution(name, priority, intercept)
    return NemoFlowNeo4jHandle(name, nemo_flow_module, intercept)


install_neo4j = install


def activate_runtime() -> None:
    """Activate NeMo Flow in the current context without exposing its API."""
    activate_runtime_module(load_nemo_flow())


__all__ = [
    "ContextBuilder",
    "ContextFormatter",
    "IdentityResolver",
    "InteractionExtractor",
    "MemoryIdentity",
    "NEMO_FLOW_EVENT_REGISTRY",
    "NEMO_FLOW_SCOPE_REGISTRY",
    "NemoFlowContextSource",
    "NemoFlowEvent",
    "NemoFlowEventDefinition",
    "NemoFlowIdentitySource",
    "NemoFlowNeo4jConfig",
    "NemoFlowNeo4jHandle",
    "NemoFlowRecallContext",
    "NemoFlowScope",
    "NemoFlowScopeDefinition",
    "NemoFlowSkipReason",
    "NemoFlowTurnContext",
    "QueryExtractor",
    "activate_runtime",
    "install",
    "install_neo4j",
    "memory_context",
    "memory_scope",
    "neo4j_memory_context",
]
