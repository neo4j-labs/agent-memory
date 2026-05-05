"""Request, response, and context shaping for the NeMo Flow adapter."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.integrations._nemo_flow_types import (
    MemoryInjection,
    NemoFlowContextSource,
    NemoFlowRecallContext,
    StoredMessageResult,
    string_or_none,
)

if TYPE_CHECKING:
    import nemo_flow


def context_text(context: Any) -> str:
    if isinstance(context, Mapping):
        return text_from_content(context.get("context")) or ""
    return text_from_content(context) or ""


def coerce_recall_context(
    context: Any,
    source: NemoFlowContextSource,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> NemoFlowRecallContext:
    if isinstance(context, NemoFlowRecallContext):
        if not metadata:
            return context
        return NemoFlowRecallContext(
            text=context.text,
            source_counts=context.source_counts,
            source_chars=context.source_chars,
            metadata={**dict(metadata), **dict(context.metadata)},
        )

    text = context_text(context)
    event_metadata = dict(metadata or {})
    if isinstance(context, Mapping) and "has_context" in context:
        event_metadata["has_context"] = bool(context["has_context"])

    if not text:
        return NemoFlowRecallContext(text="", metadata=event_metadata)

    return NemoFlowRecallContext(
        text=text,
        source_counts={source.value: 1},
        source_chars={source.value: len(text)},
        metadata=event_metadata,
    )


def message_count(messages: Any) -> int:
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        return len(messages)
    return 0


def interaction_event_data(messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    roles: list[str] = []
    role_counts: dict[str, int] = {}
    content_chars_by_role: dict[str, int] = {}
    content_chars = 0

    for message in messages:
        role = str(message.get("role", "unknown"))
        text = text_from_content(message.get("content")) or ""
        chars = len(text)
        roles.append(role)
        role_counts[role] = role_counts.get(role, 0) + 1
        content_chars_by_role[role] = content_chars_by_role.get(role, 0) + chars
        content_chars += chars

    return {
        "message_count": len(messages),
        "roles": roles,
        "role_counts": role_counts,
        "content_chars": content_chars,
        "content_chars_by_role": content_chars_by_role,
    }


def stored_message_result(role: str, content: str, result: Any) -> StoredMessageResult:
    if isinstance(result, Mapping):
        error = result.get("error")
        if error is not None:
            return StoredMessageResult(
                role=role,
                content_chars=len(content),
                stored=False,
                result_type=string_or_none(result.get("type")) or "message",
                error=str(error),
            )
        return StoredMessageResult(
            role=role,
            content_chars=len(content),
            stored=bool(result.get("stored", True)),
            result_id=string_or_none(result.get("id")),
            result_type=string_or_none(result.get("type")) or "message",
        )

    return StoredMessageResult(
        role=role,
        content_chars=len(content),
        stored=True,
        result_id=string_or_none(getattr(result, "id", None)),
        result_type=result.__class__.__name__ if result is not None else "message",
    )


def format_conversation(conversation: Any, max_items: int) -> str:
    messages = getattr(conversation, "messages", None)
    if not messages:
        return ""

    lines = ["## Recent Conversation"]
    for message in list(messages)[-max_items:]:
        role = role_value(getattr(message, "role", "message"))
        content = text_from_content(getattr(message, "content", None))
        if content:
            lines.append(f"**{role}**: {content}")
    return "\n".join(lines) if len(lines) > 1 else ""


def format_messages(messages: Any) -> str:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)) or not messages:
        return ""

    lines = ["## Relevant Past Messages"]
    for message in messages:
        role = role_value(getattr(message, "role", "message"))
        content = text_from_content(getattr(message, "content", None))
        if content:
            lines.append(f"- [{role}] {content}")
    return "\n".join(lines) if len(lines) > 1 else ""


def role_value(role: Any) -> str:
    value = getattr(role, "value", role)
    return str(value)


def default_query_extractor(request: nemo_flow.LLMRequest) -> str | None:
    content = request.content
    if not isinstance(content, Mapping):
        return None

    messages = content.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for message in reversed(messages):
            if isinstance(message, Mapping) and message.get("role") == "user":
                text = text_from_content(message.get("content"))
                if text:
                    return text

    for key in ("input", "prompt", "query"):
        text = text_from_content(content.get(key))
        if text:
            return text
    return None


def default_interaction_extractor(
    request: nemo_flow.LLMRequest,
    response: nemo_flow.Json,
) -> Sequence[Mapping[str, Any]] | None:
    user_text = default_query_extractor(request)
    assistant_text = assistant_text_from_response(response)
    if not user_text or not assistant_text:
        return None
    return (
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    )


def assistant_text_from_response(response: Any) -> str | None:
    if isinstance(response, str):
        return response
    if not isinstance(response, Mapping):
        return text_from_content(getattr(response, "content", None))

    choices = response.get("choices")
    if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping):
                text = text_from_content(message.get("content"))
                if text:
                    return text
            text = text_from_content(first.get("text"))
            if text:
                return text

    for key in ("output_text", "text", "content", "response"):
        text = text_from_content(response.get(key))
        if text:
            return text

    output = response.get("output")
    if isinstance(output, Sequence) and not isinstance(output, (str, bytes)):
        return text_from_content(output)
    return None


def default_context_formatter(context: str) -> str:
    return f"Relevant memory context:\n{context}" if context else ""


def inject_memory_context(content: Any, memory_text: str) -> MemoryInjection:
    if not isinstance(content, Mapping):
        return MemoryInjection(content=content, mutated=False)

    messages = content.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return MemoryInjection(content=content, mutated=False)

    new_content = copy.deepcopy(dict(content))
    new_messages = list(copy.deepcopy(messages))
    insert_at = 0
    while insert_at < len(new_messages):
        message = new_messages[insert_at]
        if not isinstance(message, Mapping) or message.get("role") != "system":
            break
        insert_at += 1

    new_messages.insert(insert_at, {"role": "system", "content": memory_text})
    new_content["messages"] = new_messages
    return MemoryInjection(
        content=new_content,
        mutated=True,
        message_count_before=len(messages),
        message_count_after=len(new_messages),
        insert_at=insert_at,
    )


def text_from_content(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        for key in ("text", "content"):
            text = text_from_content(content.get(key))
            if text:
                return text
        return None
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts = [text_from_content(item) for item in content]
        text = "\n".join(part for part in parts if part)
        return text or None
    return str(content)
