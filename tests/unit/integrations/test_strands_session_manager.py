"""Unit tests for the Strands SessionManager integration."""

from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")


class TestRetrievalConfig:
    def test_defaults(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jRetrievalConfig,
        )

        cfg = Neo4jRetrievalConfig()
        assert cfg.top_k == 10
        assert cfg.min_score == 0.2
        assert cfg.include_entities is True
        assert cfg.include_preferences is True
        assert cfg.include_facts is False
        assert cfg.context_tag == "user_context"


class TestAsyncBridge:
    def test_run_returns_coroutine_result(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import _AsyncBridge

        bridge = _AsyncBridge()

        async def coro() -> int:
            return 42

        try:
            assert bridge.run(coro()) == 42
        finally:
            bridge.close()

    def test_reuses_the_same_loop_across_calls(self) -> None:
        import asyncio

        from neo4j_agent_memory.integrations.strands.session_manager import _AsyncBridge

        bridge = _AsyncBridge()

        async def which_loop() -> int:
            return id(asyncio.get_running_loop())

        try:
            assert bridge.run(which_loop()) == bridge.run(which_loop())
        finally:
            bridge.close()

    def test_timeout_raises(self) -> None:
        import asyncio
        from concurrent.futures import TimeoutError as FutureTimeoutError

        from neo4j_agent_memory.integrations.strands.session_manager import _AsyncBridge

        bridge = _AsyncBridge(timeout=0.05)

        async def slow() -> None:
            await asyncio.sleep(5)

        try:
            with pytest.raises(FutureTimeoutError):
                bridge.run(slow())
        finally:
            # Cancel the orphaned task to avoid "Task was destroyed but it
            # is pending!" stderr noise when the loop stops.
            loop = bridge._loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(lambda: [t.cancel() for t in asyncio.all_tasks(loop)])
            bridge.close()

    def test_close_is_idempotent_and_stops_thread(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import _AsyncBridge

        bridge = _AsyncBridge()

        async def noop() -> None:
            return None

        bridge.run(noop())
        thread = bridge._thread
        assert thread is not None and thread.is_alive()
        bridge.close()
        bridge.close()  # second close must not raise
        assert not thread.is_alive()


class TestMappingHelpers:
    def test_message_text_concatenates_text_blocks(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import _message_text

        msg = {"role": "user", "content": [{"text": "hello"}, {"text": "world"}]}
        assert _message_text(msg) == "hello\nworld"

    def test_message_text_ignores_tool_blocks(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import _message_text

        msg = {
            "role": "assistant",
            "content": [
                {"text": "let me check"},
                {"toolUse": {"toolUseId": "1", "name": "search", "input": {"q": "x"}}},
            ],
        }
        assert _message_text(msg) == "let me check"

    def test_message_text_empty_for_pure_tool_message(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import _message_text

        msg = {
            "role": "user",
            "content": [{"toolResult": {"toolUseId": "1", "content": [{"text": "ok"}]}}],
        }
        assert _message_text(msg) == ""

    def test_to_strands_message_roundtrip_roles(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import (
            _to_strands_message,
        )
        from neo4j_agent_memory.memory.short_term import Message, MessageRole

        stored = Message(role=MessageRole.USER, content="hi")
        assert _to_strands_message(stored) == {
            "role": "user",
            "content": [{"text": "hi"}],
        }
        # Roles Strands cannot represent fall back to assistant.
        stored_sys = Message(role=MessageRole.SYSTEM, content="sys")
        assert _to_strands_message(stored_sys)["role"] == "assistant"

    def test_formatters(self) -> None:
        from types import SimpleNamespace

        from neo4j_agent_memory.integrations.strands._retrieval import (
            _format_entity,
            _format_fact,
            _format_preference,
        )

        entity = SimpleNamespace(
            display_name="Acme Corp",
            type="ORGANIZATION",
            full_type="ORGANIZATION",
            description="customer",
        )
        assert _format_entity(entity) == "[entity] Acme Corp (ORGANIZATION) — customer"
        entity_no_desc = SimpleNamespace(
            display_name="X", type="PERSON", full_type="PERSON", description=None
        )
        assert _format_entity(entity_no_desc) == "[entity] X (PERSON)"
        pref = SimpleNamespace(category="food", preference="loves Italian")
        assert _format_preference(pref) == "[preference] food: loves Italian"
        fact = SimpleNamespace(subject="Jane", predicate="works_at", object="Acme")
        assert _format_fact(fact) == "[fact] Jane works_at Acme"


from types import SimpleNamespace


def _make_manager(nams_mode: bool = False, **kwargs):
    """Build a manager wired to a FakeMemoryClient. Caller must close()."""
    from neo4j_agent_memory.integrations.strands.session_manager import (
        Neo4jSessionManager,
    )
    from tests.unit.integrations.strands_fakes import FakeMemoryClient

    client = FakeMemoryClient(nams_mode=nams_mode)
    manager = Neo4jSessionManager("sess-1", memory_client=client, **kwargs)
    return manager, client


def _fake_agent():
    return SimpleNamespace(messages=[], agent_id="agent-1")


class TestConstructor:
    def test_requires_exactly_one_of_client_or_settings(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jSessionManager,
        )

        with pytest.raises(ValueError):
            Neo4jSessionManager("s1")
        with pytest.raises(ValueError):
            Neo4jSessionManager("s1", memory_client=object(), settings=object())


class TestInitialize:
    def test_bolt_uses_session_id_directly_and_restores_history(self) -> None:
        manager, client = _make_manager(nams_mode=False)
        try:
            # Pre-seed stored history.
            import asyncio

            asyncio.run(client.short_term.create_conversation(session_id="sess-1"))
            asyncio.run(client.short_term.add_message("sess-1", "user", "hello"))
            asyncio.run(client.short_term.add_message("sess-1", "assistant", "hi there"))

            agent = _fake_agent()
            manager.initialize(agent)

            assert manager._conversation_key == "sess-1"
            assert agent.messages == [
                {"role": "user", "content": [{"text": "hello"}]},
                {"role": "assistant", "content": [{"text": "hi there"}]},
            ]
            assert client.connect_calls == 1
        finally:
            manager.close()

    def test_nams_resolves_existing_conversation_by_metadata(self) -> None:
        import asyncio

        manager, client = _make_manager(nams_mode=True)
        try:
            existing = asyncio.run(
                client.short_term.create_conversation(
                    session_id="sess-1",
                    metadata={"strands_session_id": "sess-1"},
                )
            )
            agent = _fake_agent()
            manager.initialize(agent)
            assert manager._conversation_key == str(existing.id)
            # No second conversation was created.
            assert len(client.short_term.conversations) == 1
        finally:
            manager.close()

    def test_nams_creates_conversation_when_absent(self) -> None:
        manager, client = _make_manager(nams_mode=True)
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            assert len(client.short_term.conversations) == 1
            conv = next(iter(client.short_term.conversations.values()))
            assert conv.metadata["strands_session_id"] == "sess-1"
            assert manager._conversation_key == str(conv.id)
        finally:
            manager.close()

    def test_seeds_preexisting_agent_messages_into_empty_session(self) -> None:
        manager, client = _make_manager(nams_mode=False)
        try:
            agent = _fake_agent()
            agent.messages.append({"role": "user", "content": [{"text": "seeded"}]})
            manager.initialize(agent)
            stored = client.short_term.conversations["sess-1"].messages
            assert [m.content for m in stored] == ["seeded"]
        finally:
            manager.close()

    def test_initialize_wraps_backend_errors_in_session_exception(self) -> None:
        from strands.types.exceptions import SessionException

        manager, client = _make_manager(nams_mode=False)

        async def boom(*args, **kwargs):
            raise RuntimeError("no database")

        client.short_term.get_conversation = boom  # type: ignore[method-assign]
        try:
            with pytest.raises(SessionException):
                manager.initialize(_fake_agent())
        finally:
            manager.close()


class TestAppendAndBuffer:
    def test_append_buffers_then_flushes_on_next_append(self) -> None:
        manager, client = _make_manager()
        try:
            agent = _fake_agent()
            manager.initialize(agent)

            manager.append_message({"role": "user", "content": [{"text": "one"}]}, agent)
            assert client.short_term.add_message_calls == []  # buffered, not stored

            manager.append_message({"role": "assistant", "content": [{"text": "two"}]}, agent)
            assert len(client.short_term.add_message_calls) == 1
            assert client.short_term.add_message_calls[0]["content"] == "one"
        finally:
            manager.close()

    def test_close_flushes_pending_message(self) -> None:
        manager, client = _make_manager()
        agent = _fake_agent()
        manager.initialize(agent)
        manager.append_message({"role": "user", "content": [{"text": "last"}]}, agent)
        manager.close()
        assert [c["content"] for c in client.short_term.add_message_calls] == ["last"]

    def test_pure_tool_message_is_not_persisted(self) -> None:
        manager, client = _make_manager()
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message(
                {"role": "user", "content": [{"toolResult": {"toolUseId": "1"}}]}, agent
            )
            manager._flush_buffer()
            assert client.short_term.add_message_calls == []
        finally:
            manager.close()

    def test_flush_passes_extraction_and_tenant_kwargs(self) -> None:
        manager, client = _make_manager(user_id="alice", extract_entities=False)
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message({"role": "user", "content": [{"text": "hi"}]}, agent)
            manager._flush_buffer()
            call = client.short_term.add_message_calls[0]
            assert call["extract_entities"] is False
            assert call["user_identifier"] == "alice"
            assert call["metadata"] == {"strands_session_id": "sess-1"}
        finally:
            manager.close()

    def test_buffer_holds_a_deep_copy(self) -> None:
        manager, client = _make_manager()
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            message = {"role": "user", "content": [{"text": "original"}]}
            manager.append_message(message, agent)
            # Mutation after append (e.g. context injection) must not leak.
            message["content"][0]["text"] = "<user_context>...</user_context>\noriginal"
            manager._flush_buffer()
            assert client.short_term.add_message_calls[0]["content"] == "original"
        finally:
            manager.close()

    def test_flush_failure_raises_session_exception(self) -> None:
        from strands.types.exceptions import SessionException

        manager, client = _make_manager()
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message({"role": "user", "content": [{"text": "x"}]}, agent)
            client.short_term.fail_next_add = True
            with pytest.raises(SessionException):
                manager._flush_buffer()
        finally:
            manager.close()

    def test_sync_agent_is_a_noop(self) -> None:
        manager, client = _make_manager()
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message({"role": "user", "content": [{"text": "x"}]}, agent)
            manager.sync_agent(agent)  # must NOT flush (fires on MessageAddedEvent too)
            assert client.short_term.add_message_calls == []
        finally:
            manager.close()


class TestToolCallMirroring:
    def test_tool_use_blocks_recorded_to_reasoning(self) -> None:
        manager, client = _make_manager(record_tool_calls=True)
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message(
                {
                    "role": "assistant",
                    "content": [
                        {"text": "checking"},
                        {"toolUse": {"toolUseId": "1", "name": "search", "input": {"q": "x"}}},
                    ],
                },
                agent,
            )
            manager._flush_buffer()
            assert len(client.reasoning.traces) == 1
            assert client.reasoning.tool_calls[0]["tool_name"] == "search"
            assert client.reasoning.tool_calls[0]["arguments"] == {"q": "x"}
            # The text part is still persisted as a normal message.
            assert client.short_term.add_message_calls[0]["content"] == "checking"
        finally:
            manager.close()

    def test_mirroring_disabled_by_default(self) -> None:
        manager, client = _make_manager()
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message(
                {
                    "role": "assistant",
                    "content": [{"toolUse": {"toolUseId": "1", "name": "t", "input": {}}}],
                },
                agent,
            )
            manager._flush_buffer()
            assert client.reasoning.traces == []
        finally:
            manager.close()

    def test_pure_tool_message_mirrors_without_persisting(self) -> None:
        manager, client = _make_manager(record_tool_calls=True)
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message(
                {
                    "role": "assistant",
                    "content": [{"toolUse": {"toolUseId": "1", "name": "lookup", "input": {}}}],
                },
                agent,
            )
            manager._flush_buffer()
            assert len(client.reasoning.tool_calls) == 1
            assert client.short_term.add_message_calls == []
        finally:
            manager.close()


class TestCloseSemantics:
    def test_close_closes_client_we_connected(self) -> None:
        manager, client = _make_manager()
        manager.initialize(_fake_agent())  # connects -> _we_connected
        manager.close()
        assert client.close_calls == 1

    def test_close_leaves_preconnected_injected_client_open(self) -> None:
        import asyncio

        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jSessionManager,
        )
        from tests.unit.integrations.strands_fakes import FakeMemoryClient

        client = FakeMemoryClient()
        asyncio.run(client.connect())  # user connected it; we must not close it
        manager = Neo4jSessionManager("sess-1", memory_client=client)
        manager.initialize(_fake_agent())
        manager.close()
        assert client.close_calls == 0

    def test_context_manager_calls_close(self) -> None:
        manager, client = _make_manager()
        with manager:
            manager.initialize(_fake_agent())
        assert client.close_calls == 1


class TestRedaction:
    def test_redact_rewrites_buffer_before_persistence(self) -> None:
        manager, client = _make_manager()
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message(
                {"role": "user", "content": [{"text": "my SSN is 123-45-6789"}]}, agent
            )
            manager.redact_latest_message(
                {"role": "user", "content": [{"text": "[REDACTED]"}]}, agent
            )
            manager._flush_buffer()
            contents = [c["content"] for c in client.short_term.add_message_calls]
            assert contents == ["[REDACTED]"]  # original never reached the backend
        finally:
            manager.close()

    def test_late_redaction_on_bolt_deletes_and_rewrites(self) -> None:
        manager, client = _make_manager(nams_mode=False)
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message({"role": "user", "content": [{"text": "secret"}]}, agent)
            manager._flush_buffer()  # already persisted -> late path
            manager.redact_latest_message(
                {"role": "user", "content": [{"text": "[REDACTED]"}]}, agent
            )
            assert len(client.short_term.deleted_message_ids) == 1
            assert client.short_term.add_message_calls[-1]["content"] == "[REDACTED]"
            assert client.short_term.add_message_calls[-1].get("extract_entities") is False
        finally:
            manager.close()

    def test_late_redaction_on_nams_warns_and_does_not_raise(self, caplog) -> None:
        import logging

        manager, client = _make_manager(nams_mode=True)
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message({"role": "user", "content": [{"text": "secret"}]}, agent)
            manager._flush_buffer()
            with caplog.at_level(logging.WARNING):
                manager.redact_latest_message(
                    {"role": "user", "content": [{"text": "[REDACTED]"}]}, agent
                )
            assert any("redact" in r.message.lower() for r in caplog.records)
        finally:
            manager.close()

    def test_redact_with_nothing_stored_warns_and_returns(self, caplog) -> None:
        import logging

        manager, client = _make_manager()
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            with caplog.at_level(logging.WARNING):
                manager.redact_latest_message(
                    {"role": "user", "content": [{"text": "[REDACTED]"}]}, agent
                )
            assert any("nothing to redact" in r.message.lower() for r in caplog.records)
            assert client.short_term.add_message_calls == []
        finally:
            manager.close()


class FakeRegistry:
    """Records (event_type, callback) registrations in order."""

    def __init__(self) -> None:
        self.callbacks: list[tuple[type, object]] = []

    def add_callback(self, event_type: type, callback: object) -> None:
        self.callbacks.append((event_type, callback))


class TestRetrievalInjection:
    def _manager_with_memories(self):
        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jRetrievalConfig,
        )

        manager, client = _make_manager(retrieval_config=Neo4jRetrievalConfig(include_facts=True))
        client.long_term.entities = [
            SimpleNamespace(
                display_name="Acme",
                type="ORGANIZATION",
                full_type="ORGANIZATION",
                description="customer",
            )
        ]
        client.long_term.preferences = [
            SimpleNamespace(category="style", preference="concise answers")
        ]
        client.long_term.facts = [
            SimpleNamespace(subject="Jane", predicate="works_at", object="Acme")
        ]
        return manager, client

    def test_injects_context_block_into_user_message(self) -> None:
        manager, client = self._manager_with_memories()
        try:
            manager.initialize(_fake_agent())
            message = {"role": "user", "content": [{"text": "tell me about Acme"}]}
            manager._inject_context(message)
            text = message["content"][0]["text"]
            assert text.startswith("<user_context>")
            assert "[entity] Acme (ORGANIZATION) — customer" in text
            assert "[preference] style: concise answers" in text
            assert "[fact] Jane works_at Acme" in text
            assert text.endswith("tell me about Acme")
        finally:
            manager.close()

    def test_skips_assistant_messages(self) -> None:
        manager, client = self._manager_with_memories()
        try:
            manager.initialize(_fake_agent())
            message = {"role": "assistant", "content": [{"text": "answer"}]}
            manager._inject_context(message)
            assert message["content"][0]["text"] == "answer"
        finally:
            manager.close()

    def test_no_results_means_no_tags(self) -> None:
        manager, client = self._manager_with_memories()
        client.long_term.entities = []
        client.long_term.preferences = []
        client.long_term.facts = []
        try:
            manager.initialize(_fake_agent())
            message = {"role": "user", "content": [{"text": "hello"}]}
            manager._inject_context(message)
            assert message["content"][0]["text"] == "hello"
        finally:
            manager.close()

    def test_search_failure_degrades_gracefully(self, caplog) -> None:
        import logging

        manager, client = self._manager_with_memories()
        client.long_term.fail_searches = True
        try:
            manager.initialize(_fake_agent())
            message = {"role": "user", "content": [{"text": "hello"}]}
            with caplog.at_level(logging.WARNING):
                manager._inject_context(message)
            assert message["content"][0]["text"] == "hello"  # turn not broken
        finally:
            manager.close()

    def test_injection_happens_after_persistence_so_original_is_stored(self) -> None:
        manager, client = self._manager_with_memories()
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            message = {"role": "user", "content": [{"text": "tell me about Acme"}]}
            # Simulate the hook firing order: append (base) then inject (ours).
            manager.append_message(message, agent)
            manager._inject_context(message)
            manager._flush_buffer()
            stored = client.short_term.add_message_calls[0]["content"]
            assert stored == "tell me about Acme"  # original, not augmented
        finally:
            manager.close()

    def test_hook_order_persists_original_before_injection(self) -> None:
        """End-to-end through the registry: MessageAddedEvent callbacks must
        run append (base) before inject (ours), so the stored message is the
        original even though the in-memory message gets augmented."""
        from strands.hooks import MessageAddedEvent

        manager, client = self._manager_with_memories()
        try:
            registry = FakeRegistry()
            manager.register_hooks(registry)
            agent = _fake_agent()
            manager.initialize(agent)
            message = {"role": "user", "content": [{"text": "tell me about Acme"}]}
            # Fire the MessageAddedEvent callbacks in REGISTRATION order,
            # exactly as Strands dispatches this (non-reversed) event.
            event = SimpleNamespace(message=message, agent=agent)
            for event_type, callback in registry.callbacks:
                if event_type is MessageAddedEvent:
                    callback(event)
            manager._flush_buffer()
            stored = client.short_term.add_message_calls[0]["content"]
            assert stored == "tell me about Acme"  # persisted original
            assert message["content"][0]["text"].startswith("<user_context>")  # in-memory augmented
        finally:
            manager.close()

    def test_injection_is_idempotent_for_same_message(self) -> None:
        manager, client = self._manager_with_memories()
        try:
            manager.initialize(_fake_agent())
            message = {"role": "user", "content": [{"text": "tell me about Acme"}]}
            manager._inject_context(message)
            once = message["content"][0]["text"]
            manager._inject_context(message)
            assert message["content"][0]["text"] == once  # no double block
        finally:
            manager.close()

    def test_nams_skips_unsupported_preference_and_fact_searches(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jRetrievalConfig,
        )

        manager, client = _make_manager(
            nams_mode=True, retrieval_config=Neo4jRetrievalConfig(include_facts=True)
        )
        client.long_term.entities = [
            SimpleNamespace(
                display_name="Acme",
                type="ORGANIZATION",
                full_type="ORGANIZATION",
                description=None,
            )
        ]

        # Make preference/fact searches behave like real NAMS: raise if called.
        async def boom(query, **kwargs):
            raise AssertionError("must not be called on NAMS")

        client.long_term.search_preferences = boom
        client.long_term.search_facts = boom
        try:
            manager.initialize(_fake_agent())
            message = {"role": "user", "content": [{"text": "Acme?"}]}
            manager._inject_context(message)
            text = message["content"][0]["text"]
            assert "[entity] Acme (ORGANIZATION)" in text  # entity search still works
        finally:
            manager.close()


class TestRegisterHooks:
    def test_registers_flush_after_base_hooks(self) -> None:
        from strands.hooks import AfterInvocationEvent

        manager, client = _make_manager()
        try:
            registry = FakeRegistry()
            manager.register_hooks(registry)
            event_types = [et for et, _ in registry.callbacks]
            assert AfterInvocationEvent in event_types
            # Our flush callback is the LAST AfterInvocationEvent registration.
            flush_cb = [cb for et, cb in registry.callbacks if et is AfterInvocationEvent][-1]
            agent = _fake_agent()
            manager.initialize(agent)
            manager.append_message({"role": "user", "content": [{"text": "x"}]}, agent)
            flush_cb(SimpleNamespace(agent=agent))
            assert [c["content"] for c in client.short_term.add_message_calls] == ["x"]
        finally:
            manager.close()

    def test_retrieval_callback_only_registered_with_config(self) -> None:
        from strands.hooks import MessageAddedEvent

        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jRetrievalConfig,
        )

        manager_plain, _ = _make_manager()
        manager_inject, _ = _make_manager(retrieval_config=Neo4jRetrievalConfig())
        try:
            plain, inject = FakeRegistry(), FakeRegistry()
            manager_plain.register_hooks(plain)
            manager_inject.register_hooks(inject)

            def count(reg: FakeRegistry) -> int:
                return sum(1 for et, _ in reg.callbacks if et is MessageAddedEvent)

            assert count(inject) == count(plain) + 1
        finally:
            manager_plain.close()
            manager_inject.close()


class TestForNams:
    def test_for_nams_requires_api_key(self, monkeypatch) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jSessionManager,
        )

        monkeypatch.delenv("MEMORY_API_KEY", raising=False)
        with pytest.raises(ValueError):
            Neo4jSessionManager.for_nams("sess-1")

    def test_for_nams_builds_owned_nams_client(self, monkeypatch) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jSessionManager,
        )

        monkeypatch.setenv("MEMORY_API_KEY", "test-key")
        manager = Neo4jSessionManager.for_nams("sess-1")
        try:
            assert manager._should_close_client is True
            settings = manager._client._settings
            assert settings.backend == "nams"
            assert settings.nams.validate_on_connect is False
        finally:
            manager._bridge.close()  # never connected; just stop the thread


class TestPublicExports:
    def test_session_manager_exported_from_package(self) -> None:
        from neo4j_agent_memory.integrations.strands import (
            Neo4jRetrievalConfig,
            Neo4jSessionManager,
        )

        assert Neo4jSessionManager is not None
        assert Neo4jRetrievalConfig is not None


# ---------------------------------------------------------------------------
# New tests added by review-fix pass
# ---------------------------------------------------------------------------


class TestConstructorExtended:
    """Fix 3: unknown kwargs now raise TypeError instead of being silently swallowed."""

    def test_unknown_kwarg_raises_type_error(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jSessionManager,
        )
        from tests.unit.integrations.strands_fakes import FakeMemoryClient

        with pytest.raises(TypeError):
            Neo4jSessionManager(
                "s1", memory_client=FakeMemoryClient(), extract_entites=False
            )  # typo: extract_entites


class TestInitializeExtended:
    """Fix 1: list_conversations passes user scoping + explicit limit for NAMS."""

    def test_nams_list_conversations_passes_user_identifier_and_limit(self) -> None:
        manager, client = _make_manager(nams_mode=True, user_id="alice")
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            # _aresolve_conversation calls list_conversations with scoping kwargs.
            calls = client.short_term.list_conversations_calls
            assert len(calls) == 1
            assert calls[0].get("user_identifier") == "alice"
            assert calls[0].get("limit") == 1000
        finally:
            manager.close()


class TestRetrievalInjectionExtended:
    """Fix 4: idempotency guard fires BEFORE the retrieval call."""

    def _manager_with_memories(self):
        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jRetrievalConfig,
        )

        manager, client = _make_manager(retrieval_config=Neo4jRetrievalConfig())
        client.long_term.entities = [
            SimpleNamespace(
                display_name="Acme",
                type="ORGANIZATION",
                full_type="ORGANIZATION",
                description=None,
            )
        ]
        return manager, client

    def test_second_injection_does_not_trigger_search(self) -> None:
        """The idempotency guard must short-circuit before the retrieval coroutine runs."""
        manager, client = self._manager_with_memories()
        try:
            manager.initialize(_fake_agent())
            message = {"role": "user", "content": [{"text": "tell me about Acme"}]}
            manager._inject_context(message)
            count_after_first = client.long_term.search_calls
            assert count_after_first > 0  # sanity: first injection did search
            manager._inject_context(message)  # second call on already-injected message
            assert client.long_term.search_calls == count_after_first  # no extra search
        finally:
            manager.close()


class TestFormattersExtended:
    """Fix 6: _format_entity prefers full_type when present."""

    def test_full_type_preferred_over_type(self) -> None:
        from neo4j_agent_memory.integrations.strands._retrieval import _format_entity

        entity_sub = SimpleNamespace(
            display_name="Acme",
            type="ORGANIZATION",
            full_type="ORGANIZATION:COMPANY",
            description=None,
        )
        assert _format_entity(entity_sub) == "[entity] Acme (ORGANIZATION:COMPANY)"

    def test_falls_back_to_type_when_full_type_falsy(self) -> None:
        # Defensive guard: a real Entity.full_type is always set, but the
        # formatter falls back to .type if full_type is ever falsy.
        from neo4j_agent_memory.integrations.strands._retrieval import _format_entity

        entity = SimpleNamespace(display_name="X", type="PERSON", full_type=None, description=None)
        assert _format_entity(entity) == "[entity] X (PERSON)"


class TestRestoreLimit:
    """Fix 7: restore_limit forwarded to get_conversation; key resolved without loading history."""

    def test_restore_limit_passed_to_get_conversation(self) -> None:
        manager, client = _make_manager(restore_limit=2)
        try:
            agent = _fake_agent()
            manager.initialize(agent)
            kwargs = client.short_term.get_conversation_kwargs
            assert len(kwargs) == 1
            assert kwargs[0].get("limit") == 2
        finally:
            manager.close()

    def test_lazy_flush_resolves_key_without_loading_history(self) -> None:
        manager, client = _make_manager()
        try:
            agent = _fake_agent()
            # No initialize() — append directly (lazy path).
            manager.append_message({"role": "user", "content": [{"text": "x"}]}, agent)
            manager._flush_buffer()
            assert client.short_term.get_conversation_calls == 0
            assert client.short_term.add_message_calls[0]["content"] == "x"
        finally:
            manager.close()
