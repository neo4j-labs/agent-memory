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
