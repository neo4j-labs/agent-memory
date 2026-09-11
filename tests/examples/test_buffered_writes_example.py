"""Smoke tests for the buffered-writes example."""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
BUFFERED_DIR = EXAMPLES_DIR / "buffered-writes"
MODULE_NAME = "buffered_writes_main"


def _load_module() -> ModuleType:
    """Exec ``main.py`` as a module (callers must pop it from sys.modules)."""
    spec = importlib.util.spec_from_file_location(MODULE_NAME, BUFFERED_DIR / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.syntax
class TestBufferedWritesStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "main.py"]:
            assert (BUFFERED_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse((BUFFERED_DIR / "main.py").read_text(encoding="utf-8"))


@pytest.mark.imports
class TestBufferedWritesImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemoryClient, MemorySettings  # noqa: F401
        from neo4j_agent_memory.config.settings import (  # noqa: F401
            ExtractionConfig,
            MemoryConfig,
        )
        from neo4j_agent_memory.memory.buffered import (  # noqa: F401
            BufferedWriteError,
            BufferedWriter,
        )

    def test_build_settings_uses_buffered_mode(self, monkeypatch):
        # v0.3+: the example resolves a SentenceTransformersProvider via
        # from_provider; skip when the extra is not installed.
        pytest.importorskip("sentence_transformers")

        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")

        try:
            module = _load_module()
            settings = module.build_settings()
            assert settings.memory.write_mode == "buffered"
            # The library default, stated explicitly so the back-pressure
            # section below is visibly a departure from it.
            assert settings.memory.max_pending == 200

            # Both knobs are parameterised: section 1 flips the mode, section 3
            # shrinks the queue.
            sync_settings = module.build_settings(write_mode="sync", max_pending=4)
            assert sync_settings.memory.write_mode == "sync"
            assert sync_settings.memory.max_pending == 4
        finally:
            sys.modules.pop(MODULE_NAME, None)


@pytest.mark.syntax
class TestBufferedWritesContent:
    @pytest.fixture
    def source(self) -> str:
        return (BUFFERED_DIR / "main.py").read_text(encoding="utf-8")

    def test_main_uses_buffered_submit(self, source):
        assert "client.buffered.submit" in source

    def test_main_calls_flush(self, source):
        assert "client.flush()" in source

    def test_main_inspects_pending_and_write_errors(self, source):
        assert "client.buffered.pending" in source
        assert "client.write_errors" in source

    def test_main_measures_both_write_modes(self, source):
        assert 'run_workload("sync"' in source
        assert 'run_workload("buffered"' in source

    def test_main_pairs_buffered_write_with_inline_add_message(self, source):
        assert "add_message" in source
        assert 'extraction_mode="skip"' in source

    def test_main_reads_through_the_portable_cypher_accessor(self, source):
        assert "client.query.cypher" in source
        # client.graph.execute_read is deprecated (removed in v0.6.0).
        assert "client.graph.execute_read" not in source


@pytest.mark.requires_neo4j
@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="set NEO4J_URI to run the example end-to-end (the quick job has no database)",
)
class TestBufferedWritesEndToEnd:
    """Runs the example against Neo4j — catches a broken buffered write path."""

    @pytest.fixture
    def module(self, neo4j_env):
        pytest.importorskip("sentence_transformers")
        try:
            yield _load_module()
        finally:
            sys.modules.pop(MODULE_NAME, None)

    async def _cleanup(self, module) -> None:
        from neo4j_agent_memory import MemoryClient

        async with MemoryClient(module.build_settings()) as client:
            await client.buffered.submit(
                "MATCH (t:AgentTurn) WHERE t.session IN $sessions DETACH DELETE t",
                {"sessions": module.DEMO_SESSIONS},
            )
            await client.buffered.submit(
                """
                MATCH (c:Conversation) WHERE c.session_id IN $sessions
                OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
                DETACH DELETE c, m
                """,
                {"sessions": module.DEMO_SESSIONS},
            )
            await client.flush()

    async def test_main_persists_all_turns(self, module):
        from neo4j_agent_memory import MemoryClient

        try:
            await module.main()

            async with MemoryClient(module.build_settings()) as client:
                for session in module.DEMO_SESSIONS:
                    rows = await client.query.cypher(
                        "MATCH (t:AgentTurn {session: $session}) RETURN count(t) AS cnt",
                        {"session": session},
                    )
                    assert rows[0]["cnt"] == module.TURNS, session

                    # The buffered per-session counter landed too.
                    rows = await client.query.cypher(
                        """
                        MATCH (c:Conversation {session_id: $session})
                        RETURN c.turns_recorded AS turns
                        """,
                        {"session": session},
                    )
                    assert rows[0]["turns"] == module.TURNS, session

                # The back-pressure probe nodes are cleaned up by the demo.
                rows = await client.query.cypher(
                    "MATCH (p:BackPressureProbe) RETURN count(p) AS cnt"
                )
                assert rows[0]["cnt"] == 0
        finally:
            await self._cleanup(module)

    async def test_workload_records_no_write_errors(self, module):
        from neo4j_agent_memory import MemoryClient

        try:
            async with MemoryClient(module.build_settings()) as client:
                await module.reset_demo_data(client)
                await module.agent_turn(client, module.BUFFERED_SESSION, 0)
                await client.flush()
                assert client.write_errors == []
        finally:
            await self._cleanup(module)

    async def test_error_channel_captures_the_failed_write(self, module):
        from neo4j_agent_memory.memory.buffered import BufferedWriteError

        errors = await module.demo_error_channel()

        assert len(errors) == 1
        err = errors[0]
        assert isinstance(err, BufferedWriteError)
        assert err.query.strip().startswith("MERGE (t:AgentTurn")
        assert err.parameters == {"turn": -1}
        assert isinstance(err.error, BaseException)
        assert err.when is not None

    async def test_back_pressure_bounds_the_queue(self, module):
        elapsed_ms, high_water = await module.demo_back_pressure(submissions=20, max_pending=4)
        assert elapsed_ms > 0
        # submit() blocks rather than letting the queue grow past max_pending.
        assert 0 < high_water <= 4
