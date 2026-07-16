"""Strands ↔ neo4j-agent-memory message mapping (pure functions).

One concern: converting between Strands' content-block message dicts and
the library's stored ``Message`` model. No session state, no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from strands.types.content import Message as StrandsMessage

    from neo4j_agent_memory.memory.short_term import Message as StoredMessage


def _message_text(message: StrandsMessage) -> str:
    """Concatenate the text blocks of a Strands message (tool blocks ignored)."""
    blocks = message.get("content") or []
    texts = [b["text"] for b in blocks if isinstance(b, dict) and b.get("text")]
    return "\n".join(texts)


def _to_strands_message(stored: StoredMessage) -> StrandsMessage:
    """Convert a stored neo4j-agent-memory Message to a Strands message dict."""
    role = stored.role.value if hasattr(stored.role, "value") else str(stored.role)
    if role not in ("user", "assistant"):
        role = "assistant"
    return {
        "role": cast("Literal['user', 'assistant']", role),
        "content": [{"text": stored.content}],
    }
