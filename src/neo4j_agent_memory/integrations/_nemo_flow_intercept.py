"""LLM execution intercept for the optional NeMo Flow integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._nemo_flow_content import (
    default_context_formatter,
    default_interaction_extractor,
    default_query_extractor,
    inject_memory_context,
    interaction_event_data,
    text_from_content,
)
from neo4j_agent_memory.integrations._nemo_flow_memory import Neo4jMemoryAccess
from neo4j_agent_memory.integrations._nemo_flow_runtime import (
    NemoFlowTelemetry,
    capture_metadata,
    coerce_identity,
    current_scope_metadata,
    identity_from_metadata,
    identity_observability_metadata,
    memory_identity,
)
from neo4j_agent_memory.integrations._nemo_flow_types import (
    MemoryIdentity,
    NemoFlowEvent,
    NemoFlowIdentitySource,
    NemoFlowNeo4jConfig,
    NemoFlowRecallContext,
    NemoFlowScope,
    NemoFlowSkipReason,
    NemoFlowTurnContext,
    ResolvedMemoryIdentity,
)

if TYPE_CHECKING:
    import nemo_flow

logger = logging.getLogger(__name__)


class Neo4jNemoFlowIntercept:
    """Orchestrates memory recall and capture around a NeMo Flow LLM call."""

    def __init__(self, memory: Any, config: NemoFlowNeo4jConfig, nemo_flow_module: Any):
        self.memory = memory
        self.config = config
        self._nemo_flow = nemo_flow_module
        self._memory_access = Neo4jMemoryAccess(memory, config)
        self._telemetry = NemoFlowTelemetry(nemo_flow_module, config)

    async def __call__(
        self,
        llm_name: str,
        request: nemo_flow.LLMRequest,
        next_call: Callable[[nemo_flow.LLMRequest], Any],
    ) -> nemo_flow.Json:
        resolved = self._resolve_identity(llm_name, request)
        if resolved is None or not resolved.identity.has_scope():
            return await next_call(request)

        identity = resolved.identity
        self._telemetry.emit_identity_resolved(identity, resolved.source)

        request_for_call = request
        if self.config.auto_recall:
            try:
                request_for_call = await self._recall(request, identity)
            except Exception:
                if not self.config.fail_open:
                    raise
                logger.warning("Neo4j memory recall failed in NeMo Flow intercept", exc_info=True)

        response = await next_call(request_for_call)

        if self.config.auto_capture:
            try:
                await self._capture(request, response, identity)
            except Exception:
                if not self.config.fail_open:
                    raise
                logger.warning("Neo4j memory capture failed in NeMo Flow intercept", exc_info=True)

        return response

    def _resolve_identity(
        self, llm_name: str, request: nemo_flow.LLMRequest
    ) -> ResolvedMemoryIdentity | None:
        scope_metadata = current_scope_metadata(self._nemo_flow)
        context = NemoFlowTurnContext(
            llm_name=llm_name,
            request=request,
            scope_metadata=scope_metadata,
        )

        if self.config.identity_resolver is not None:
            identity = coerce_identity(self.config.identity_resolver(context))
            if identity is not None:
                return ResolvedMemoryIdentity(
                    identity,
                    NemoFlowIdentitySource.IDENTITY_RESOLVER,
                )

        context_identity = memory_identity.get()
        if context_identity is not None and context_identity.has_scope():
            return ResolvedMemoryIdentity(context_identity, NemoFlowIdentitySource.MEMORY_SCOPE)

        metadata_identity = identity_from_metadata(scope_metadata)
        if metadata_identity is not None:
            return ResolvedMemoryIdentity(
                metadata_identity,
                NemoFlowIdentitySource.SCOPE_METADATA,
            )

        return None

    async def _recall(
        self,
        request: nemo_flow.LLMRequest,
        identity: MemoryIdentity,
    ) -> nemo_flow.LLMRequest:
        query_extractor = self.config.query_extractor or default_query_extractor
        query = query_extractor(request)
        if not query:
            self._telemetry.emit_event(
                NemoFlowEvent.RECALL_SKIPPED,
                data={"reason": NemoFlowSkipReason.EMPTY_QUERY.value},
                metadata=identity_observability_metadata(identity),
            )
            return request

        scope_handle = self._telemetry.start_scope(
            NemoFlowScope.RECALL,
            input={
                "query_length": len(query),
                "top_k": self.config.top_k,
                "max_items": self.config.max_items,
                "threshold": self.config.threshold,
            },
            metadata=identity_observability_metadata(identity),
        )
        scope_output: dict[str, Any] = {"context_chars": 0, "injected": False}

        try:
            context = await self._memory_access.build_context(query, identity)
            if not context.text:
                self._telemetry.emit_event(
                    NemoFlowEvent.RECALL_SKIPPED,
                    handle=scope_handle,
                    data={"reason": NemoFlowSkipReason.NO_CONTEXT.value},
                    metadata=identity_observability_metadata(identity),
                )
                return request

            context_event_data = context.to_event_data()
            self._telemetry.emit_event(
                NemoFlowEvent.RECALL_CONTEXT_BUILT,
                handle=scope_handle,
                data=context_event_data,
                metadata=identity_observability_metadata(identity),
            )

            memory_text = self._format_context(context)
            scope_output = {
                **context_event_data,
                "formatted_context_chars": len(memory_text),
                "injected": False,
            }
            if not memory_text:
                self._telemetry.emit_event(
                    NemoFlowEvent.RECALL_SKIPPED,
                    handle=scope_handle,
                    data={"reason": NemoFlowSkipReason.EMPTY_FORMATTED_CONTEXT.value},
                    metadata=identity_observability_metadata(identity),
                )
                return request

            injection = inject_memory_context(request.content, memory_text)
            if not injection.mutated:
                self._telemetry.emit_event(
                    NemoFlowEvent.RECALL_SKIPPED,
                    handle=scope_handle,
                    data={"reason": NemoFlowSkipReason.UNSUPPORTED_REQUEST_CONTENT.value},
                    metadata=identity_observability_metadata(identity),
                )
                return request

            scope_output = {
                **context_event_data,
                "formatted_context_chars": len(memory_text),
                "message_count_before": injection.message_count_before,
                "message_count_after": injection.message_count_after,
                "insertion_index": injection.insert_at,
                "injected": True,
            }
            self._telemetry.emit_event(
                NemoFlowEvent.RECALL_INJECTED,
                handle=scope_handle,
                data=scope_output,
                metadata=identity_observability_metadata(identity),
            )
            return self._nemo_flow.LLMRequest(request.headers, injection.content)
        except Exception as exc:
            scope_output = {"error_type": type(exc).__name__}
            self._telemetry.emit_event(
                NemoFlowEvent.RECALL_FAILED,
                handle=scope_handle,
                data={"error_type": type(exc).__name__},
                metadata=identity_observability_metadata(identity),
            )
            raise
        finally:
            self._telemetry.end_scope(scope_handle, output=scope_output)

    async def _capture(
        self,
        request: nemo_flow.LLMRequest,
        response: nemo_flow.Json,
        identity: MemoryIdentity,
    ) -> None:
        session_id = identity.resolved_session_id()
        if session_id is None:
            self._telemetry.emit_event(
                NemoFlowEvent.CAPTURE_SKIPPED,
                data={"reason": NemoFlowSkipReason.NO_SESSION_ID.value},
                metadata=identity_observability_metadata(identity),
            )
            return

        interaction_extractor = self.config.interaction_extractor or default_interaction_extractor
        messages = interaction_extractor(request, response)
        if not messages:
            self._telemetry.emit_event(
                NemoFlowEvent.CAPTURE_SKIPPED,
                data={"reason": NemoFlowSkipReason.NO_INTERACTION.value},
                metadata=identity_observability_metadata(identity),
            )
            return

        metadata = capture_metadata(identity, self.config.metadata)
        extracted_event_data = interaction_event_data(messages)
        scope_handle = self._telemetry.start_scope(
            NemoFlowScope.CAPTURE,
            input=extracted_event_data,
            metadata=identity_observability_metadata(identity),
        )
        self._telemetry.emit_event(
            NemoFlowEvent.CAPTURE_EXTRACTED,
            handle=scope_handle,
            data=extracted_event_data,
            metadata=identity_observability_metadata(identity),
        )
        scope_output: dict[str, Any] = {**extracted_event_data, "stored": False}
        try:
            attempted_count = 0
            stored_count = 0
            skipped_empty_count = 0
            for message in messages:
                role = str(message.get("role", "user"))
                content = text_from_content(message.get("content"))
                if not content:
                    skipped_empty_count += 1
                    continue
                attempted_count += 1
                result = await self._memory_access.store_message(
                    role, content, session_id, metadata
                )
                if result.error is not None:
                    raise RuntimeError(result.error)
                if result.stored:
                    stored_count += 1
                    self._telemetry.emit_event(
                        NemoFlowEvent.CAPTURE_MESSAGE_STORED,
                        handle=scope_handle,
                        data={
                            "role": result.role,
                            "content_chars": result.content_chars,
                            "result_id": result.result_id,
                            "result_type": result.result_type,
                            "extract_entities": self.config.extract_entities,
                            "extract_relations": self.config.extract_relations,
                            "generate_embeddings": self.config.generate_embeddings,
                        },
                        metadata=identity_observability_metadata(identity),
                    )
            scope_output = {
                **extracted_event_data,
                "attempted_count": attempted_count,
                "stored_count": stored_count,
                "skipped_empty_count": skipped_empty_count,
                "stored": stored_count > 0,
            }
            if attempted_count == 0:
                self._telemetry.emit_event(
                    NemoFlowEvent.CAPTURE_SKIPPED,
                    handle=scope_handle,
                    data={
                        **scope_output,
                        "reason": NemoFlowSkipReason.EMPTY_CONTENT.value,
                    },
                    metadata=identity_observability_metadata(identity),
                )
                return
            self._telemetry.emit_event(
                NemoFlowEvent.CAPTURE_STORED,
                handle=scope_handle,
                data=scope_output,
                metadata=identity_observability_metadata(identity),
            )
        except Exception as exc:
            scope_output = {
                **extracted_event_data,
                "stored": False,
                "error_type": type(exc).__name__,
            }
            self._telemetry.emit_event(
                NemoFlowEvent.CAPTURE_FAILED,
                handle=scope_handle,
                data=scope_output,
                metadata=identity_observability_metadata(identity),
            )
            raise
        finally:
            self._telemetry.end_scope(scope_handle, output=scope_output)

    def _format_context(self, context: NemoFlowRecallContext) -> str:
        formatter = self.config.context_formatter or default_context_formatter
        return formatter(context.text)
