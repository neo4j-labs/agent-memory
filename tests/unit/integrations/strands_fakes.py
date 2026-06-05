"""Stateful fake MemoryClient for Strands session-manager unit tests.

Implements only the methods Neo4jSessionManager touches, with NAMS-mode
semantics behind a flag (server-issued conversation UUIDs, kwargs
dropped on add_message). State-based tests beat call-sequence mocks for
round-trip behavior (append -> restore).
"""

from __future__ import annotations

import uuid
from typing import Any

from neo4j_agent_memory.core.exceptions import MemoryError as NamMemoryError
from neo4j_agent_memory.memory.short_term import Conversation, Message, MessageRole


class FakeShortTerm:
    def __init__(self, nams_mode: bool) -> None:
        self._nams_mode = nams_mode
        # key -> Conversation. Bolt: key == session_id. NAMS: key == str(uuid).
        self.conversations: dict[str, Conversation] = {}
        self.add_message_calls: list[dict[str, Any]] = []
        self.deleted_message_ids: list[str] = []
        self.fail_next_add = False

    async def create_conversation(
        self, session_id: str | None = None, **kwargs: Any
    ) -> Conversation:
        conv_id = uuid.uuid4()
        key = str(conv_id) if self._nams_mode else str(session_id)
        conv = Conversation(
            id=conv_id,
            session_id=str(session_id),
            metadata=kwargs.get("metadata") or {},
        )
        self.conversations[key] = conv
        return conv

    async def list_conversations(self, **kwargs: Any) -> list[Conversation]:
        return list(self.conversations.values())

    async def get_conversation(self, session_id: str, **kwargs: Any) -> Conversation:
        conv = self.conversations.get(session_id)
        if conv is None:
            if self._nams_mode:
                raise NamMemoryError(f"NAMS: conversation {session_id} not found")
            # Bolt contract: empty conversation, no exception.
            return Conversation(session_id=session_id)
        return conv

    async def add_message(self, session_id: str, role: str, content: str, **kwargs: Any) -> Message:
        if self.fail_next_add:
            self.fail_next_add = False
            raise RuntimeError("backend down")
        self.add_message_calls.append(
            {"session_id": session_id, "role": role, "content": content, **kwargs}
        )
        if session_id not in self.conversations:
            if self._nams_mode:
                raise NamMemoryError(f"NAMS: unknown conversation {session_id}")
            await self.create_conversation(session_id=session_id)
        msg = Message(role=MessageRole(role), content=content)
        self.conversations[session_id].messages.append(msg)
        return msg

    async def delete_message(self, message_id: Any, **kwargs: Any) -> bool:
        if self._nams_mode:
            from neo4j_agent_memory.core.exceptions import NotSupportedError

            raise NotSupportedError(
                backend="nams",
                method="ShortTermMemory.delete_message",
                message="NAMS does not expose a message-delete endpoint.",
                workaround="Use clear_session(session_id) to clear an entire conversation.",
            )
        self.deleted_message_ids.append(str(message_id))
        for conv in self.conversations.values():
            conv.messages = [m for m in conv.messages if str(m.id) != str(message_id)]
        return True


class FakeLongTerm:
    def __init__(self) -> None:
        self.entities: list[Any] = []
        self.preferences: list[Any] = []
        self.facts: list[Any] = []
        self.fail_searches = False

    async def _maybe_fail(self) -> None:
        if self.fail_searches:
            raise RuntimeError("search backend down")

    async def search_entities(self, query: str, **kwargs: Any) -> list[Any]:
        await self._maybe_fail()
        return self.entities

    async def search_preferences(self, query: str, **kwargs: Any) -> list[Any]:
        await self._maybe_fail()
        return self.preferences

    async def search_facts(self, query: str, **kwargs: Any) -> list[Any]:
        await self._maybe_fail()
        return self.facts


class FakeReasoning:
    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []

    async def start_trace(self, session_id: str, task: str, **kwargs: Any) -> Any:
        trace = {"id": uuid.uuid4(), "session_id": session_id, "task": task}
        self.traces.append(trace)

        class _T:
            id = trace["id"]

        return _T()

    async def add_step(self, trace_id: Any, **kwargs: Any) -> Any:
        step = {"id": uuid.uuid4(), "trace_id": trace_id, **kwargs}
        self.steps.append(step)

        class _S:
            id = step["id"]

        return _S()

    async def record_tool_call(
        self, step_id: Any, tool_name: str, arguments: dict[str, Any], **kwargs: Any
    ) -> Any:
        self.tool_calls.append({"step_id": step_id, "tool_name": tool_name, "arguments": arguments})
        return None


class FakeMemoryClient:
    """Duck-typed MemoryClient covering the session manager's surface."""

    def __init__(self, nams_mode: bool = False) -> None:
        self._nams_mode = nams_mode
        self.short_term = FakeShortTerm(nams_mode)
        self.long_term = FakeLongTerm()
        self.reasoning = FakeReasoning()
        self.connect_calls = 0
        self.close_calls = 0
        self._connected = False

    @property
    def is_nams(self) -> bool:
        return self._nams_mode

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self.connect_calls += 1
        self._connected = True

    async def close(self) -> None:
        self.close_calls += 1
        self._connected = False
