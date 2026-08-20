"""Test doubles for the Strands integration's unit tests.

Two deliberately **asymmetric** backends behind one flag:

* ``FakeMemoryClient(nams_mode=True)`` exposes the **real**
  :class:`NamsShortTermMemory` / :class:`NamsLongTermMemory`, driven by
  :class:`StubTransport` — a stub of the one narrow method the NAMS memory
  classes use to reach the network
  (``HttpTransport.request(spec, path_params=, json=, params=)``). Nothing
  about the NAMS memory API is re-implemented here, so a test can no longer
  assert against behaviour NAMS does not have. The observable surface is the
  *requests the transport recorded*, which is what NAMS would really receive.

* ``FakeMemoryClient()`` (bolt) keeps the hand-written duck-typed fakes below.
  The real bolt classes drive a live Neo4j session through
  ``Neo4jClient.execute_read/execute_write``, which a unit test cannot have;
  bolt fidelity is pinned instead by
  ``tests/integration/test_strands_memory_store_integration.py``.

That asymmetry is intentional — please do not "finish the job" by making both
sides the same. Replacing the NAMS side with a duck-typed fake reintroduces
the class of defect this module exists to remove (a fake more permissive than
the backend it stands in for); replacing the bolt side with the real classes
requires a database.

State-based tests beat call-sequence mocks for round-trip behavior
(append -> restore), which is why the bolt fakes are stateful.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from neo4j_agent_memory.memory.short_term import Conversation, Message, MessageRole
from neo4j_agent_memory.nams.endpoints import EndpointSpec
from neo4j_agent_memory.nams.transport import HttpTransport

# ---------------------------------------------------------------------------
# Stubbed NAMS wire
# ---------------------------------------------------------------------------

#: Fixed timestamp for canned NAMS payloads (NAMS returns ISO 8601 strings).
_STUB_NOW = "2026-08-19T12:00:00+00:00"

#: Sentinel: this NAMS operation has neither an override nor a canned default.
_MISSING = object()


@dataclass(frozen=True)
class StubCall:
    """One request the NAMS memory classes made on the transport."""

    method: str  #: ``spec.bridge_method`` — the NAMS operation name.
    spec: EndpointSpec
    path_params: dict[str, object] = field(default_factory=dict)
    json: Any = None
    params: dict[str, Any] | None = None


def _canned_conversation(call: StubCall) -> dict[str, Any]:
    """``POST /conversations`` — NAMS mints the id and echoes nothing else back.

    Deliberately does *not* echo ``metadata``: whether the create response
    carries it is a server detail nothing in this package relies on (the
    callers use ``created.id`` only). Assert on the recorded request body.
    """
    return {"id": str(uuid.uuid4()), "createdAt": _STUB_NOW}


def _canned_message(call: StubCall) -> dict[str, Any]:
    body = call.json if isinstance(call.json, dict) else {}
    return {
        "id": str(uuid.uuid4()),
        "role": body.get("role", "user"),
        "content": body.get("content", ""),
        "createdAt": _STUB_NOW,
    }


def _canned_bulk(call: StubCall) -> dict[str, Any]:
    body = call.json if isinstance(call.json, dict) else {}
    batch = body.get("messages") or []
    return {
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": m.get("role", "user"),
                "content": m.get("content", ""),
                "createdAt": _STUB_NOW,
            }
            for m in batch
        ]
    }


def _canned_entity(call: StubCall) -> dict[str, Any]:
    body = call.json if isinstance(call.json, dict) else {}
    return {
        "id": str(uuid.uuid4()),
        "name": body.get("name", ""),
        # NAMS stores (and returns) its own lowercase type vocabulary; the
        # memory class uppercases it again on the way back.
        "type": body.get("type", "custom"),
        "createdAt": _STUB_NOW,
    }


#: Realistic empty/echo responses per NAMS operation, matching the response
#: envelopes the memory classes parse. A test overrides any of these via
#: ``transport.responses[method] = payload`` (or a ``StubCall -> payload``
#: callable). An operation with neither an override nor a default raises —
#: an unexpected NAMS call must never quietly succeed.
_CANNED: dict[str, Any] = {
    "create_conversation": _canned_conversation,
    "list_conversations": {"conversations": []},
    "get_conversation": {"createdAt": _STUB_NOW},
    "list_messages": {"messages": []},
    "add_message": _canned_message,
    "bulk_add_messages": _canned_bulk,
    "search_messages": {"messages": [], "searchType": "vector"},
    "delete_conversation": None,
    "add_entity": _canned_entity,
    "search_entities": {"entities": [], "searchType": "vector"},
    "expand_graph": {"nodes": [], "edges": []},
    "get_extraction_status": {"messages": [], "summary": {}},
}


class _NoAuth:
    """``AuthProvider`` that adds no headers (nothing reaches the network)."""

    async def apply(self, headers: dict[str, str]) -> dict[str, str]:
        return headers


class StubTransport(HttpTransport):
    """Records NAMS requests and answers them from canned payloads.

    Subclasses the real :class:`HttpTransport` rather than duck-typing it, so
    the stub is bound to the actual constructor and the actual ``request``
    signature: if ``request`` grows or renames a parameter, the NAMS memory
    classes call this override with the new keyword and the tests fail with a
    ``TypeError`` instead of silently drifting. No socket is ever opened —
    ``request`` is overridden above the point where the httpx client is built.
    """

    def __init__(self) -> None:
        # A ``/v1`` endpoint so ``detect_protocol`` selects the REST wire, the
        # one the hosted service speaks.
        super().__init__(endpoint="https://nams.invalid/v1", auth=_NoAuth())
        self.calls: list[StubCall] = []
        #: NAMS operation name -> payload, or a ``StubCall -> payload`` callable.
        self.responses: dict[str, Any] = {}

    async def request(
        self,
        spec: EndpointSpec,
        *,
        path_params: dict[str, object] | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        call = StubCall(
            method=spec.bridge_method,
            spec=spec,
            path_params=dict(path_params or {}),
            json=json,
            params=params,
        )
        self.calls.append(call)
        canned = self.responses.get(spec.bridge_method, _CANNED.get(spec.bridge_method, _MISSING))
        if canned is _MISSING:
            raise AssertionError(
                f"StubTransport: unexpected NAMS call {spec.bridge_method!r} "
                f"({spec.rest_method} {spec.rest_path}). Set "
                f"transport.responses[{spec.bridge_method!r}] if the test intends it."
            )
        return canned(call) if callable(canned) else canned

    # ------------------------------------------------------------ assertions

    @property
    def methods(self) -> list[str]:
        """NAMS operation names, in call order."""
        return [call.method for call in self.calls]

    def calls_for(self, method: str) -> list[StubCall]:
        return [call for call in self.calls if call.method == method]

    def last(self, method: str) -> StubCall:
        calls = self.calls_for(method)
        assert calls, f"no {method!r} call was made (calls: {self.methods})"
        return calls[-1]


# ---------------------------------------------------------------------------
# Bolt fakes (see the module docstring for why these are hand-written)
# ---------------------------------------------------------------------------


class FakeShortTerm:
    """Duck-typed stand-in for the bolt ``ShortTermMemory``."""

    def __init__(self) -> None:
        #: session_id -> Conversation (bolt keys conversations by session_id).
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
        # Bolt's CREATE_CONVERSATION has no metadata property, so a bolt
        # conversation never carries metadata no matter what the caller passes.
        conv = Conversation(id=uuid.uuid4(), session_id=str(session_id), metadata={})
        self.conversations[str(session_id)] = conv
        return conv

    async def list_conversations(self, **kwargs: Any) -> list[Conversation]:
        self.list_conversations_calls.append(kwargs)
        return list(self.conversations.values())

    async def get_conversation(self, session_id: str, **kwargs: Any) -> Conversation:
        self.get_conversation_calls += 1
        self.get_conversation_kwargs.append({"session_id": session_id, **kwargs})
        conv = self.conversations.get(session_id)
        if conv is None:
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
        self.bulk_calls.append(
            {
                "session_id": session_id,
                "messages": messages,
                "kwargs": {
                    "generate_embeddings": generate_embeddings,
                    "extract_entities": extract_entities,
                    "extract_relations": extract_relations,
                    "user_identifier": user_identifier,
                },
            }
        )
        if session_id not in self.conversations:
            await self.create_conversation(session_id=session_id, user_identifier=user_identifier)
        stored = [Message(role=MessageRole(m["role"]), content=m["content"]) for m in messages]
        self.conversations[session_id].messages.extend(stored)
        return stored

    async def delete_message(self, message_id: Any, **kwargs: Any) -> bool:
        self.deleted_message_ids.append(str(message_id))
        for conv in self.conversations.values():
            conv.messages = [m for m in conv.messages if str(m.id) != str(message_id)]
        return True


class FakeLongTerm:
    """Duck-typed stand-in for the bolt ``LongTermMemory``.

    Bolt-only by construction: no ``expand_graph`` (that method exists on
    NAMS alone), and every method behaves the way the bolt implementation
    behaves — including ``add_entity`` returning the
    ``(Entity, DeduplicationResult)`` tuple.
    """

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
        self.related: list[Any] = []
        self.related_kwargs: list[dict[str, Any]] = []
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

    async def search_entities(self, query: str, **kwargs: Any) -> list[Any]:
        self.search_calls += 1
        self.search_kwargs.append({"query": query, **kwargs})
        await self._maybe_fail()
        return self.entities

    async def search_preferences(self, query: str, **kwargs: Any) -> list[Any]:
        self.search_calls += 1
        self.search_kwargs.append({"query": query, **kwargs})
        if self.fail_preferences:
            raise RuntimeError("preference backend down")
        await self._maybe_fail()
        return self.preferences

    async def search_facts(self, query: str, **kwargs: Any) -> list[Any]:
        self.search_calls += 1
        self.search_kwargs.append({"query": query, **kwargs})
        if self.fail_facts:
            raise RuntimeError("fact backend down")
        await self._maybe_fail()
        return self.facts

    async def add_preference(
        self, category: str, preference: str, *, user_identifier: str | None = None, **kwargs: Any
    ) -> Any:
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
        self.added_facts.append((subject, predicate, obj))
        from neo4j_agent_memory.memory.long_term import Fact

        return Fact(subject=subject, predicate=predicate, object=obj)

    async def add_entity(self, name: str, entity_type: str, **kwargs: Any) -> Any:
        from neo4j_agent_memory.memory.long_term import Entity

        self.added_entities.append((name, entity_type))
        # Bolt returns (Entity, DeduplicationResult); NAMS returns a bare
        # Entity -- and that path now runs the real NamsLongTermMemory.
        return Entity(name=name, type=entity_type), None

    async def get_related_entities(self, entity: Any, **kwargs: Any) -> list[tuple[Any, Any]]:
        """Real ``Relationship`` objects, shaped as the bolt path really shapes them.

        The bolt implementation cannot report a relationship's own type or
        direction: ``Neo4jClient.execute_read`` returns ``result.data()``,
        which renders a relationship as ``(start_props, type, end_props)`` and
        drops its properties, so ``memory/long_term.py``'s parse falls through
        to ``type="RELATED_TO"`` for every hit, with ``source_id`` hardcoded to
        the centre. ``tests/integration/test_strands_memory_store_integration.py``
        asserts this against a live Neo4j; the fake reproduces it rather than
        inventing a richer relationship the production stack never returns.
        """
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

    async def get_preferences_for(self, user_identifier: str, **kwargs: Any) -> list[Any]:
        self.preferences_for_calls.append({"user_identifier": user_identifier, **kwargs})
        return [*self.preferences_for, *self.preferences_by_user.get(user_identifier, [])]


class FakeReasoning:
    """Duck-typed reasoning layer.

    Bolt-shaped, and used in bolt mode only: the session manager's tool-call
    mirroring is exercised there. If a NAMS-mode test ever needs reasoning,
    wire ``NamsReasoningMemory`` to the ``StubTransport`` instead of extending
    this class.
    """

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
    """Duck-typed MemoryClient covering the Strands integration's surface.

    In NAMS mode ``short_term`` / ``long_term`` are the real NAMS classes over
    :attr:`transport`; in bolt mode they are the fakes above.
    """

    def __init__(self, nams_mode: bool = False) -> None:
        self._nams_mode = nams_mode
        self.transport: StubTransport | None = None
        self.short_term: Any
        self.long_term: Any
        if nams_mode:
            from neo4j_agent_memory.nams.long_term import NamsLongTermMemory
            from neo4j_agent_memory.nams.short_term import NamsShortTermMemory

            self.transport = StubTransport()
            self.short_term = NamsShortTermMemory(self.transport)
            self.long_term = NamsLongTermMemory(self.transport)
        else:
            self.short_term = FakeShortTerm()
            self.long_term = FakeLongTerm()
        self.reasoning = FakeReasoning()
        self.connect_calls = 0
        self.close_calls = 0
        self._connected = False

    @property
    def wire(self) -> StubTransport:
        """The stubbed NAMS wire (NAMS mode only) — where the assertions live."""
        assert self.transport is not None, "wire is NAMS-mode only"
        return self.transport

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
