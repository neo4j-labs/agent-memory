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
                loop.call_soon_threadsafe(
                    lambda: [t.cancel() for t in asyncio.all_tasks(loop)]
                )
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

        from neo4j_agent_memory.integrations.strands.session_manager import (
            _format_entity,
            _format_fact,
            _format_preference,
        )

        entity = SimpleNamespace(
            display_name="Acme Corp", type="ORGANIZATION", description="customer"
        )
        assert _format_entity(entity) == "[entity] Acme Corp (ORGANIZATION) — customer"
        entity_no_desc = SimpleNamespace(display_name="X", type="PERSON", description=None)
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

    @pytest.mark.xfail(reason="append_message lands in the next commit", strict=True)
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
