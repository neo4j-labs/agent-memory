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
