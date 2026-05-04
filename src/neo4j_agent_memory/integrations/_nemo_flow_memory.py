"""Neo4j Agent Memory access used by the NeMo Flow intercept."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from typing import Any

from neo4j_agent_memory.integrations._nemo_flow_content import (
    coerce_recall_context,
    format_conversation,
    format_messages,
    message_count,
    stored_message_result,
)
from neo4j_agent_memory.integrations._nemo_flow_types import (
    MemoryIdentity,
    NemoFlowContextSource,
    NemoFlowNeo4jConfig,
    NemoFlowRecallContext,
    StoredMessageResult,
)


class Neo4jMemoryAccess:
    """Provider-facing memory operations behind the NeMo Flow adapter."""

    def __init__(self, memory: Any, config: NemoFlowNeo4jConfig):
        self.memory = memory
        self.config = config

    async def build_context(self, query: str, identity: MemoryIdentity) -> NemoFlowRecallContext:
        if self.config.context_builder is not None:
            context = await self.invoke(self.config.context_builder, self.memory, query, identity)
            return coerce_recall_context(context, NemoFlowContextSource.CUSTOM)

        client = client_for_memory(self.memory)
        session_id = identity.resolved_session_id()
        parts: list[str] = []
        source_counts: dict[str, int] = {}
        source_chars: dict[str, int] = {}

        def add_part(source: NemoFlowContextSource, text: str, count: int = 1) -> None:
            if not text:
                return
            source_key = source.value
            parts.append(text)
            source_counts[source_key] = source_counts.get(source_key, 0) + count
            source_chars[source_key] = source_chars.get(source_key, 0) + len(text)

        short_term = getattr(client, "short_term", None)
        if short_term is not None:
            if self.config.include_session_history and session_id is not None:
                get_conversation = getattr(short_term, "get_conversation", None)
                if get_conversation is not None:
                    conversation = await self.invoke(
                        get_conversation,
                        session_id,
                        limit=self.config.max_items,
                    )
                    conversation_context = format_conversation(conversation, self.config.max_items)
                    if conversation_context:
                        add_part(
                            NemoFlowContextSource.SESSION_HISTORY,
                            conversation_context,
                            message_count(getattr(conversation, "messages", None)),
                        )

            if self.config.include_user_messages:
                search_messages = getattr(short_term, "search_messages", None)
                if search_messages is not None:
                    search_kwargs: dict[str, Any] = {
                        "limit": self.config.top_k,
                        "metadata_filters": identity.metadata(),
                    }
                    if self.config.threshold is not None:
                        search_kwargs["threshold"] = self.config.threshold
                    messages = await self.invoke(search_messages, query, **search_kwargs)
                    messages_context = format_messages(messages)
                    if messages_context:
                        add_part(
                            NemoFlowContextSource.USER_MESSAGES,
                            messages_context,
                            message_count(messages),
                        )

        long_term = getattr(client, "long_term", None)
        if (
            self.config.include_long_term
            and long_term is not None
            and hasattr(long_term, "get_context")
        ):
            context = await self.invoke(
                long_term.get_context,
                query,
                max_items=self.config.max_items,
            )
            if context:
                add_part(
                    NemoFlowContextSource.LONG_TERM,
                    f"## Relevant Knowledge\n{context}",
                )

        reasoning = getattr(client, "reasoning", None)
        if (
            self.config.include_reasoning
            and reasoning is not None
            and hasattr(reasoning, "get_context")
        ):
            context = await self.invoke(
                reasoning.get_context,
                query,
                max_traces=max(1, self.config.max_items // 2),
            )
            if context:
                add_part(
                    NemoFlowContextSource.REASONING,
                    f"## Similar Past Tasks\n{context}",
                )

        if not parts and hasattr(self.memory, "get_context"):
            context = await self._invoke_get_context(
                self.memory.get_context,
                query,
                session_id=session_id,
            )
            return coerce_recall_context(
                context,
                NemoFlowContextSource.MEMORY_INTEGRATION,
                metadata={"fallback_used": True},
            )

        return NemoFlowRecallContext(
            text="\n\n".join(parts),
            source_counts=source_counts,
            source_chars=source_chars,
        )

    async def store_message(
        self,
        role: str,
        content: str,
        session_id: str,
        metadata: Mapping[str, Any],
    ) -> StoredMessageResult:
        if hasattr(self.memory, "store_message"):
            result = await self.invoke(
                self.memory.store_message,
                role,
                content,
                session_id=session_id,
                metadata=dict(metadata),
            )
            return stored_message_result(role, content, result)

        client = client_for_memory(self.memory)
        result = await self.invoke(
            client.short_term.add_message,
            session_id=session_id,
            role=role,
            content=content,
            metadata=dict(metadata),
            extract_entities=self.config.extract_entities,
            extract_relations=self.config.extract_relations,
            generate_embedding=self.config.generate_embeddings,
        )
        return stored_message_result(role, content, result)

    async def invoke(self, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        supported_kwargs = supported_kwargs_for(method, kwargs)
        if inspect.iscoroutinefunction(method):
            return await method(*args, **supported_kwargs)
        if self.config.run_sync_in_thread:
            return await asyncio.to_thread(method, *args, **supported_kwargs)
        result = method(*args, **supported_kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _invoke_get_context(
        self,
        method: Callable[..., Any],
        query: str,
        *,
        session_id: str | None,
    ) -> Any:
        kwargs = {
            "session_id": session_id,
            "include_short_term": self.config.include_session_history,
            "include_long_term": self.config.include_long_term,
            "include_reasoning": self.config.include_reasoning,
            "max_items": self.config.max_items,
        }
        if accepts_positional_query(method):
            return await self.invoke(method, query, **kwargs)
        return await self.invoke(method, query=query, **kwargs)


def client_for_memory(memory: Any) -> Any:
    try:
        return memory.client
    except Exception:
        return memory


def supported_kwargs_for(method: Callable[..., Any], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    if not kwargs:
        return {}
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return dict(kwargs)

    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in signature.parameters}


def accepts_positional_query(method: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return True

    for param in signature.parameters.values():
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            return True
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
    return False
