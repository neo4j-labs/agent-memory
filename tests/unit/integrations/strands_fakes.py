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
        self.bulk_calls: list[dict[str, Any]] = []
        self.deleted_message_ids: list[str] = []
        self.fail_next_add = False
        self.list_conversations_calls: list[dict[str, Any]] = []
        self.get_conversation_calls: int = 0
        self.get_conversation_kwargs: list[dict[str, Any]] = []

    async def create_conversation(
        self, session_id: str | None = None, **kwargs: Any
    ) -> Conversation:
        conv_id = uuid.uuid4()
        key = str(conv_id) if self._nams_mode else str(session_id)
        # Real bolt's CREATE_CONVERSATION has no metadata property; only NAMS
        # accepts and stores it. Mirror that so bolt-mode tests can't lean on
        # metadata a real bolt conversation would never carry.
        metadata = kwargs.get("metadata") or {} if self._nams_mode else {}
        conv = Conversation(
            id=conv_id,
            session_id=str(session_id),
            metadata=metadata,
        )
        self.conversations[key] = conv
        return conv

    async def list_conversations(self, **kwargs: Any) -> list[Conversation]:
        self.list_conversations_calls.append(kwargs)
        return list(self.conversations.values())

    async def get_conversation(self, session_id: str, **kwargs: Any) -> Conversation:
        self.get_conversation_calls += 1
        self.get_conversation_kwargs.append({"session_id": session_id, **kwargs})
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
        # Real NAMS accepts only {content, role} on add_message and silently
        # drops everything else (metadata, user_identifier, bolt-only knobs).
        # Mirror that here so NAMS-mode tests can't lean on dropped kwargs.
        recorded = {"session_id": session_id, "role": role, "content": content}
        if not self._nams_mode:
            recorded.update(kwargs)
        self.add_message_calls.append(recorded)
        if session_id not in self.conversations:
            if self._nams_mode:
                raise NamMemoryError(f"NAMS: unknown conversation {session_id}")
            await self.create_conversation(session_id=session_id)
        msg = Message(role=MessageRole(role), content=content)
        self.conversations[session_id].messages.append(msg)
        return msg

    async def bulk_add_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        generate_embeddings: bool = True,
        extract_entities: bool = False,
        extract_relations: bool = True,
        user_identifier: str | None = None,
    ) -> list[Message]:
        # Explicit parameters mirroring ShortTermProtocol.bulk_add_messages
        # (no **kwargs catch-all) so this fake can't absorb a keyword the
        # real bolt backend would reject.
        kwargs = {
            "generate_embeddings": generate_embeddings,
            "extract_entities": extract_entities,
            "extract_relations": extract_relations,
            "user_identifier": user_identifier,
        }
        recorded_kwargs = {} if self._nams_mode else kwargs
        self.bulk_calls.append(
            {"session_id": session_id, "messages": messages, "kwargs": recorded_kwargs}
        )
        if session_id not in self.conversations:
            if self._nams_mode:
                raise NamMemoryError(f"NAMS: unknown conversation {session_id}")
            await self.create_conversation(session_id=session_id, user_identifier=user_identifier)
        stored = [Message(role=MessageRole(m["role"]), content=m["content"]) for m in messages]
        self.conversations[session_id].messages.extend(stored)
        return stored

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
        self.fail_preferences = False
        self.fail_facts = False
        self.search_calls: int = 0
        self.search_kwargs: list[dict[str, Any]] = []
        self.added_preferences: list[tuple[str, str]] = []
        self.added_facts: list[tuple[str, str, str]] = []
        self.added_entities: list[tuple[str, str]] = []
        self.nams_mode = False
        self.related: list[Any] = []
        self.related_kwargs: list[dict[str, Any]] = []
        self.expansion: dict[str, list[dict[str, Any]]] = {"nodes": [], "edges": []}
        self.expand_calls: list[str] = []
        self.preferences_for: list[Any] = []
        self.preferences_for_calls: list[dict[str, Any]] = []
        #: Preferences that got a ``(:User)-[:HAS_PREFERENCE]`` edge, keyed by
        #: user -- the only ones ``get_preferences_for`` can see.
        self.preferences_by_user: dict[str, list[Any]] = {}
        #: Mirrors ``MemorySettings.memory.multi_tenant``: bolt's
        #: ``_enforce_multi_tenant`` raises ``ValueError`` (not
        #: ``NotSupportedError``) when it is on and no identifier is passed.
        self.multi_tenant = False

    async def _maybe_fail(self) -> None:
        if self.fail_searches:
            raise RuntimeError("search backend down")

    def _reject_on_nams(self, method: str) -> None:
        if self.nams_mode:
            from neo4j_agent_memory.core.exceptions import NotSupportedError

            raise NotSupportedError(
                backend="nams",
                method=f"LongTermMemory.{method}",
                message="NAMS provides entity endpoints only.",
            )

    async def search_entities(self, query: str, **kwargs: Any) -> list[Any]:
        self.search_calls += 1
        self.search_kwargs.append({"query": query, **kwargs})
        await self._maybe_fail()
        return self.entities

    async def search_preferences(self, query: str, **kwargs: Any) -> list[Any]:
        self.search_calls += 1
        self.search_kwargs.append({"query": query, **kwargs})
        self._reject_on_nams("search_preferences")
        if self.fail_preferences:
            raise RuntimeError("preference backend down")
        await self._maybe_fail()
        return self.preferences

    async def search_facts(self, query: str, **kwargs: Any) -> list[Any]:
        self.search_calls += 1
        self.search_kwargs.append({"query": query, **kwargs})
        self._reject_on_nams("search_facts")
        if self.fail_facts:
            raise RuntimeError("fact backend down")
        await self._maybe_fail()
        return self.facts

    async def add_preference(
        self, category: str, preference: str, *, user_identifier: str | None = None, **kwargs: Any
    ) -> Any:
        self._reject_on_nams("add_preference")
        if self.multi_tenant and user_identifier is None:
            raise ValueError(
                "MemorySettings.memory.multi_tenant=True but no user_identifier was supplied."
            )
        self.added_preferences.append((category, preference))
        from neo4j_agent_memory.memory.long_term import Preference

        stored = Preference(category=category, preference=preference)
        # Bolt writes the (:User)-[:HAS_PREFERENCE] edge only when
        # user_identifier is given, and get_preferences_for reads exactly
        # that edge -- so an unscoped write is invisible to it.
        if user_identifier is not None:
            self.preferences_by_user.setdefault(user_identifier, []).append(stored)
        return stored

    async def add_fact(self, subject: str, predicate: str, obj: str, **kwargs: Any) -> Any:
        self._reject_on_nams("add_fact")
        self.added_facts.append((subject, predicate, obj))
        from neo4j_agent_memory.memory.long_term import Fact

        return Fact(subject=subject, predicate=predicate, object=obj)

    async def add_entity(self, name: str, entity_type: str, **kwargs: Any) -> Any:
        from neo4j_agent_memory.memory.long_term import Entity

        self.added_entities.append((name, entity_type))
        entity = Entity(name=name, type=entity_type)
        # Real NAMS add_entity returns a bare Entity (nams/long_term.py:205-210);
        # bolt returns (Entity, DeduplicationResult).
        if self.nams_mode:
            return entity
        return entity, None

    async def get_related_entities(self, entity: Any, **kwargs: Any) -> list[tuple[Any, Any]]:
        """Real ``Relationship`` objects, shaped as the bolt path really shapes them.

        The bolt implementation cannot report a relationship's own type or
        direction: ``Neo4jClient.execute_read`` returns ``result.data()``,
        which renders a relationship as ``(start_props, type, end_props)`` and
        drops its properties, so ``memory/long_term.py``'s parse falls through
        to ``type="RELATED_TO"`` for every hit, with ``source_id`` hardcoded
        to the centre. Verified live against Neo4j 5 (see
        ``tests/integration/test_strands_memory_store_integration.py``). This
        fake reproduces that rather than inventing a richer relationship the
        production stack never returns.
        """
        self._reject_on_nams("get_related_entities")
        self.related_kwargs.append(kwargs)
        from neo4j_agent_memory.memory.long_term import Relationship

        centre_id = getattr(entity, "id", entity)
        return [
            (
                other,
                Relationship(source_id=centre_id, target_id=other.id, type="RELATED_TO"),
            )
            for other in self.related
        ]

    async def expand_graph(self, node_id: str, **kwargs: Any) -> dict[str, list[dict[str, Any]]]:
        self.expand_calls.append(str(node_id))
        return self.expansion

    async def get_preferences_for(self, user_identifier: str, **kwargs: Any) -> list[Any]:
        self._reject_on_nams("get_preferences_for")
        self.preferences_for_calls.append({"user_identifier": user_identifier, **kwargs})
        return [*self.preferences_for, *self.preferences_by_user.get(user_identifier, [])]


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
        self.long_term.nams_mode = nams_mode
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


class FakeAgent:
    """Minimal Agent stand-in for the session manager's initialize(agent)."""

    def __init__(self, memory_manager: Any = None) -> None:
        self.memory_manager = memory_manager
        self.messages: list[Any] = []
        self.state: dict[str, Any] = {}
