"""Unit tests for the optional NeMo Flow integration."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from neo4j_agent_memory.integrations import nemo_flow


class FakeLLMRequest:
    def __init__(self, headers: dict[str, Any], content: dict[str, Any]):
        self.headers = dict(headers)
        self.content = dict(content)


class FakeInterceptors:
    def __init__(self) -> None:
        self.registered: dict[str, dict[str, Any]] = {}
        self.deregistered: list[str] = []

    def register_llm_execution(self, name: str, priority: int, callback: Any) -> None:
        self.registered[name] = {"priority": priority, "callback": callback}

    def deregister_llm_execution(self, name: str) -> bool:
        self.deregistered.append(name)
        return self.registered.pop(name, None) is not None


class FakeScope:
    def __init__(self) -> None:
        self.handle: Any | None = None
        self.stack: list[Any] = []
        self.pushed: list[dict[str, Any]] = []
        self.popped: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def get_handle(self) -> Any | None:
        return self.handle

    def push(
        self,
        name: str,
        scope_type: str,
        *,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        handle = SimpleNamespace(name=name, scope_type=scope_type, input=input, metadata=metadata)
        self.stack.append(handle)
        self.handle = handle
        self.pushed.append(
            {
                "name": name,
                "scope_type": scope_type,
                "input": input,
                "metadata": metadata,
                "handle": handle,
            }
        )
        return handle

    def pop(self, handle: Any, *, output: dict[str, Any] | None = None) -> None:
        self.popped.append({"name": handle.name, "output": output, "handle": handle})
        if self.stack and self.stack[-1] is handle:
            self.stack.pop()
        elif handle in self.stack:
            self.stack.remove(handle)
        self.handle = self.stack[-1] if self.stack else None

    def event(
        self,
        name: str,
        *,
        handle: Any | None = None,
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.events.append(
            {
                "name": name,
                "handle": handle,
                "data": data,
                "metadata": metadata,
            }
        )


class FakeShortTermMemory:
    def __init__(self) -> None:
        self.get_conversation_calls: list[dict[str, Any]] = []
        self.search_messages_calls: list[dict[str, Any]] = []
        self.add_message_calls: list[dict[str, Any]] = []
        self.conversations: dict[str, list[Any]] = {
            "user-1:thread-1": [
                SimpleNamespace(role="user", content="Alex prefers tea in the afternoon.")
            ],
            "scope-session": [SimpleNamespace(role="assistant", content="Stored scope context.")],
        }

    async def get_conversation(self, session_id: str, *, limit: int | None = None) -> Any:
        self.get_conversation_calls.append({"session_id": session_id, "limit": limit})
        messages = self.conversations.get(session_id, [])
        if limit is not None:
            messages = messages[-limit:]
        return SimpleNamespace(messages=messages)

    async def search_messages(self, query: str, **kwargs: Any) -> list[Any]:
        self.search_messages_calls.append({"query": query, "kwargs": kwargs})
        return [SimpleNamespace(role="user", content="Past memory: Alex also likes green tea.")]

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        extract_entities: bool = True,
        extract_relations: bool = True,
        generate_embedding: bool = True,
    ) -> Any:
        self.add_message_calls.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": metadata,
                "extract_entities": extract_entities,
                "extract_relations": extract_relations,
                "generate_embedding": generate_embedding,
            }
        )
        return SimpleNamespace(id=f"message-{len(self.add_message_calls)}")


class FakeLongTermMemory:
    def __init__(self) -> None:
        self.get_context_calls: list[dict[str, Any]] = []

    async def get_context(self, query: str, **kwargs: Any) -> str:
        self.get_context_calls.append({"query": query, "kwargs": kwargs})
        return "### User Preferences\n- [drink] Alex prefers jasmine tea."


class FakeMemoryClient:
    def __init__(self) -> None:
        self.short_term = FakeShortTermMemory()
        self.long_term = FakeLongTermMemory()
        self.reasoning = SimpleNamespace()


class FakeMemoryIntegration:
    def __init__(self) -> None:
        self.client = FakeMemoryClient()
        self.store_message_calls: list[dict[str, Any]] = []

    async def store_message(
        self,
        role: str,
        content: str,
        *,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.store_message_calls.append(
            {
                "role": role,
                "content": content,
                "session_id": session_id,
                "metadata": metadata,
            }
        )
        return {"stored": True}


@pytest.fixture
def fake_nemo_flow(monkeypatch: pytest.MonkeyPatch) -> Any:
    intercepts = FakeInterceptors()
    scope = FakeScope()
    module = SimpleNamespace(
        LLMRequest=FakeLLMRequest,
        Json=dict,
        ScopeType=SimpleNamespace(Agent="agent", Retriever="retriever", Custom="custom"),
        intercepts=intercepts,
        scope=scope,
        get_scope_stack=lambda: SimpleNamespace(),
    )
    monkeypatch.setitem(sys.modules, "nemo_flow", module)
    return module


def test_install_registers_and_uninstalls_execution_intercept(fake_nemo_flow: Any) -> None:
    memory = FakeMemoryClient()

    handle = nemo_flow.install(memory, name="neo4j.test", priority=12)

    assert handle.active is True
    assert fake_nemo_flow.intercepts.registered["neo4j.test"]["priority"] == 12
    assert fake_nemo_flow.intercepts.registered["neo4j.test"]["callback"] is handle.intercept
    assert handle.uninstall() is True
    assert handle.active is False
    assert handle.uninstall() is False
    assert fake_nemo_flow.intercepts.deregistered == ["neo4j.test"]


@pytest.mark.asyncio
async def test_context_identity_recalls_and_captures_with_memory_client(
    fake_nemo_flow: Any,
) -> None:
    memory = FakeMemoryClient()
    nemo_flow.install(memory, name="neo4j.context")
    intercept = fake_nemo_flow.intercepts.registered["neo4j.context"]["callback"]
    seen_requests = []
    request = fake_nemo_flow.LLMRequest(
        {"x-trace": "abc"},
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "What should I drink?"}],
        },
    )

    async def next_call(next_request: Any) -> dict[str, Any]:
        seen_requests.append(next_request)
        return {"choices": [{"message": {"content": "Drink tea."}}]}

    with nemo_flow.memory_scope(user_id="user-1", thread_id="thread-1"):
        response = await intercept("gpt-test", request, next_call)

    assert response == {"choices": [{"message": {"content": "Drink tea."}}]}
    assert len(seen_requests) == 1
    assert isinstance(seen_requests[0], FakeLLMRequest)
    assert seen_requests[0] is not request
    assert seen_requests[0].headers == {"x-trace": "abc"}

    messages = seen_requests[0].content["messages"]
    assert messages[0]["role"] == "system"
    assert "Relevant memory context:" in messages[0]["content"]
    assert "Alex prefers tea in the afternoon." in messages[0]["content"]
    assert "Past memory: Alex also likes green tea." in messages[0]["content"]

    assert memory.short_term.get_conversation_calls == [
        {"session_id": "user-1:thread-1", "limit": 10}
    ]
    assert memory.short_term.search_messages_calls == [
        {
            "query": "What should I drink?",
            "kwargs": {
                "limit": 5,
                "metadata_filters": {
                    "user_id": "user-1",
                    "run_id": "thread-1",
                    "session_id": "user-1:thread-1",
                },
                "threshold": 0.7,
            },
        }
    ]
    assert memory.short_term.add_message_calls == [
        {
            "session_id": "user-1:thread-1",
            "role": "user",
            "content": "What should I drink?",
            "metadata": {
                "user_id": "user-1",
                "run_id": "thread-1",
                "session_id": "user-1:thread-1",
            },
            "extract_entities": True,
            "extract_relations": True,
            "generate_embedding": True,
        },
        {
            "session_id": "user-1:thread-1",
            "role": "assistant",
            "content": "Drink tea.",
            "metadata": {
                "user_id": "user-1",
                "run_id": "thread-1",
                "session_id": "user-1:thread-1",
            },
            "extract_entities": True,
            "extract_relations": True,
            "generate_embedding": True,
        },
    ]
    assert [item["name"] for item in fake_nemo_flow.scope.pushed] == [
        nemo_flow.NemoFlowScope.MEMORY.value,
        nemo_flow.NemoFlowScope.RECALL.value,
        nemo_flow.NemoFlowScope.CAPTURE.value,
    ]
    assert [item["name"] for item in fake_nemo_flow.scope.popped] == [
        nemo_flow.NemoFlowScope.RECALL.value,
        nemo_flow.NemoFlowScope.CAPTURE.value,
        nemo_flow.NemoFlowScope.MEMORY.value,
    ]
    assert [item["name"] for item in fake_nemo_flow.scope.events] == [
        nemo_flow.NemoFlowEvent.IDENTITY_RESOLVED.value,
        nemo_flow.NemoFlowEvent.RECALL_CONTEXT_BUILT.value,
        nemo_flow.NemoFlowEvent.RECALL_INJECTED.value,
        nemo_flow.NemoFlowEvent.CAPTURE_EXTRACTED.value,
        nemo_flow.NemoFlowEvent.CAPTURE_MESSAGE_STORED.value,
        nemo_flow.NemoFlowEvent.CAPTURE_MESSAGE_STORED.value,
        nemo_flow.NemoFlowEvent.CAPTURE_STORED.value,
    ]
    expected_context_chars = len(
        "## Recent Conversation\n"
        "**user**: Alex prefers tea in the afternoon.\n\n"
        "## Relevant Past Messages\n"
        "- [user] Past memory: Alex also likes green tea."
    )
    assert fake_nemo_flow.scope.events[0]["data"] == {
        "source": nemo_flow.NemoFlowIdentitySource.MEMORY_SCOPE.value,
        "has_scope": True,
        "filter_count": 3,
    }
    assert fake_nemo_flow.scope.events[1]["data"]["context_chars"] == expected_context_chars
    assert fake_nemo_flow.scope.events[1]["data"]["source_counts"] == {
        nemo_flow.NemoFlowContextSource.SESSION_HISTORY.value: 1,
        nemo_flow.NemoFlowContextSource.USER_MESSAGES.value: 1,
    }
    assert fake_nemo_flow.scope.events[2]["data"]["context_chars"] == expected_context_chars
    assert fake_nemo_flow.scope.events[2]["data"]["message_count_before"] == 1
    assert fake_nemo_flow.scope.events[2]["data"]["message_count_after"] == 2
    assert fake_nemo_flow.scope.events[3]["data"] == {
        "message_count": 2,
        "roles": ["user", "assistant"],
        "role_counts": {"user": 1, "assistant": 1},
        "content_chars": 30,
        "content_chars_by_role": {"user": 20, "assistant": 10},
    }
    assert fake_nemo_flow.scope.events[4]["data"]["role"] == "user"
    assert fake_nemo_flow.scope.events[4]["data"]["result_id"] == "message-1"
    assert fake_nemo_flow.scope.events[5]["data"]["role"] == "assistant"
    assert fake_nemo_flow.scope.events[5]["data"]["result_id"] == "message-2"
    assert fake_nemo_flow.scope.events[6]["data"] == {
        "message_count": 2,
        "roles": ["user", "assistant"],
        "role_counts": {"user": 1, "assistant": 1},
        "content_chars": 30,
        "content_chars_by_role": {"user": 20, "assistant": 10},
        "attempted_count": 2,
        "stored_count": 2,
        "skipped_empty_count": 0,
        "stored": True,
    }


@pytest.mark.asyncio
async def test_scope_metadata_can_supply_neo4j_identity(fake_nemo_flow: Any) -> None:
    memory = FakeMemoryClient()
    nemo_flow.install(memory, name="neo4j.scope", include_user_messages=False)
    intercept = fake_nemo_flow.intercepts.registered["neo4j.scope"]["callback"]
    fake_nemo_flow.scope.handle = SimpleNamespace(
        metadata={"neo4j_agent_memory": {"session_id": "scope-session", "user_id": "scope-user"}}
    )
    request = fake_nemo_flow.LLMRequest(
        {},
        {"messages": [{"role": "user", "content": "Remember this?"}]},
    )

    async def next_call(next_request: Any) -> dict[str, str]:
        return {"content": "I will remember."}

    await intercept("gpt-test", request, next_call)

    assert memory.short_term.get_conversation_calls == [
        {"session_id": "scope-session", "limit": 10}
    ]
    assert memory.short_term.add_message_calls[0]["session_id"] == "scope-session"
    assert memory.short_term.add_message_calls[0]["metadata"] == {
        "user_id": "scope-user",
        "session_id": "scope-session",
    }


@pytest.mark.asyncio
async def test_no_identity_skips_neo4j_and_calls_next_with_original_request(
    fake_nemo_flow: Any,
) -> None:
    memory = FakeMemoryClient()
    nemo_flow.install(memory, name="neo4j.no_identity")
    intercept = fake_nemo_flow.intercepts.registered["neo4j.no_identity"]["callback"]
    request = fake_nemo_flow.LLMRequest({}, {"messages": [{"role": "user", "content": "Hi"}]})
    seen_requests = []

    async def next_call(next_request: Any) -> dict[str, str]:
        seen_requests.append(next_request)
        return {"content": "Hello"}

    assert await intercept("gpt-test", request, next_call) == {"content": "Hello"}
    assert seen_requests == [request]
    assert memory.short_term.get_conversation_calls == []
    assert memory.short_term.search_messages_calls == []
    assert memory.short_term.add_message_calls == []
    assert fake_nemo_flow.scope.pushed == []
    assert fake_nemo_flow.scope.popped == []
    assert fake_nemo_flow.scope.events == []


@pytest.mark.asyncio
async def test_memory_integration_capture_uses_store_message(fake_nemo_flow: Any) -> None:
    integration = FakeMemoryIntegration()
    nemo_flow.install(
        integration,
        name="neo4j.integration",
        include_user_messages=False,
        extract_entities=False,
    )
    intercept = fake_nemo_flow.intercepts.registered["neo4j.integration"]["callback"]
    request = fake_nemo_flow.LLMRequest(
        {},
        {"messages": [{"role": "user", "content": "What do I drink?"}]},
    )

    async def next_call(next_request: Any) -> dict[str, str]:
        return {"content": "Tea."}

    with nemo_flow.memory_scope(session_id="explicit-session", user_id="alex"):
        await intercept("gpt-test", request, next_call)

    assert integration.store_message_calls == [
        {
            "role": "user",
            "content": "What do I drink?",
            "session_id": "explicit-session",
            "metadata": {"user_id": "alex", "session_id": "explicit-session"},
        },
        {
            "role": "assistant",
            "content": "Tea.",
            "session_id": "explicit-session",
            "metadata": {"user_id": "alex", "session_id": "explicit-session"},
        },
    ]


@pytest.mark.asyncio
async def test_long_term_recall_is_opt_in(fake_nemo_flow: Any) -> None:
    memory = FakeMemoryClient()
    nemo_flow.install(
        memory,
        name="neo4j.long_term",
        include_session_history=False,
        include_user_messages=False,
        include_long_term=True,
    )
    intercept = fake_nemo_flow.intercepts.registered["neo4j.long_term"]["callback"]
    seen_requests = []
    request = fake_nemo_flow.LLMRequest(
        {},
        {"messages": [{"role": "user", "content": "What tea do I like?"}]},
    )

    async def next_call(next_request: Any) -> dict[str, str]:
        seen_requests.append(next_request)
        return {"content": "Jasmine tea."}

    with nemo_flow.memory_scope(user_id="alex", run_id="run-1"):
        await intercept("gpt-test", request, next_call)

    assert memory.long_term.get_context_calls == [
        {"query": "What tea do I like?", "kwargs": {"max_items": 10}}
    ]
    assert "Alex prefers jasmine tea" in seen_requests[0].content["messages"][0]["content"]


def test_legacy_names_remain_available() -> None:
    assert nemo_flow.memory_context is nemo_flow.memory_scope
    assert nemo_flow.neo4j_memory_context is nemo_flow.memory_scope
    assert nemo_flow.install_neo4j is nemo_flow.install


def test_scope_and_event_registries_are_public() -> None:
    assert nemo_flow.NEMO_FLOW_SCOPE_REGISTRY[nemo_flow.NemoFlowScope.MEMORY].scope_type_name == (
        "Custom"
    )
    assert nemo_flow.NEMO_FLOW_SCOPE_REGISTRY[nemo_flow.NemoFlowScope.RECALL].scope_type_name == (
        "Retriever"
    )
    assert (
        nemo_flow.NEMO_FLOW_EVENT_REGISTRY[nemo_flow.NemoFlowEvent.RECALL_INJECTED].name.value
        == "neo4j_agent_memory.recall.injected"
    )
    assert (
        nemo_flow.NEMO_FLOW_EVENT_REGISTRY[nemo_flow.NemoFlowEvent.RECALL_CONTEXT_BUILT].description
        == "Recall built context from one or more memory sources."
    )
    assert (
        nemo_flow.NEMO_FLOW_EVENT_REGISTRY[nemo_flow.NemoFlowEvent.CAPTURE_STORED].name.value
        == "neo4j_agent_memory.capture.stored"
    )
    assert (
        nemo_flow.NEMO_FLOW_EVENT_REGISTRY[
            nemo_flow.NemoFlowEvent.CAPTURE_MESSAGE_STORED
        ].description
        == "Capture stored one message."
    )


def test_thread_id_conflict_is_rejected() -> None:
    with (
        pytest.raises(ValueError, match="run_id and thread_id"),
        nemo_flow.memory_scope(user_id="user-1", run_id="run-1", thread_id="thread-1"),
    ):
        pass
