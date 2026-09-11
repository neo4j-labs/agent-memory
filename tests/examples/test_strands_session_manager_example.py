"""Smoke tests for the strands-session-manager example.

The content class deliberately asserts only on things a grep *can* pin —
that the demo does not reach into private API. Everything behavioural is
asserted by running ``main()`` against Neo4j and reading its output, so a
demo that silently stopped persisting fails here instead of passing.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
STRANDS_SM_DIR = EXAMPLES_DIR / "strands-session-manager"
MODULE_NAME = "strands_sm_example"


def _load(module_name: str = MODULE_NAME) -> ModuleType:
    """Exec the example as a module (callers must pop it from sys.modules)."""
    spec = importlib.util.spec_from_file_location(module_name, STRANDS_SM_DIR / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.syntax
class TestStrandsSessionManagerStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "main.py", ".env.example"]:
            assert (STRANDS_SM_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse((STRANDS_SM_DIR / "main.py").read_text(encoding="utf-8"))

    def test_demo_drives_the_public_hook_path(self):
        """No private API: the demo must dispatch real hook events.

        Calling ``manager._inject_context(...)`` directly would work but
        teaches readers to depend on a private name, and the injection point
        is reached through ``MessageAddedEvent`` in any real agent.
        """
        source = (STRANDS_SM_DIR / "main.py").read_text(encoding="utf-8")
        assert "_inject_context" not in source
        assert "registry.add_hook(manager)" in source
        assert "MessageAddedEvent(" in source


@pytest.mark.imports
class TestStrandsSessionManagerImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemorySettings  # noqa: F401
        from neo4j_agent_memory.integrations.strands import (  # noqa: F401
            Neo4jRetrievalConfig,
            Neo4jSessionManager,
        )

    def test_example_module_imports(self):
        """The module must be importable and expose a callable main()."""
        try:
            module = _load()
            assert callable(module.main)
        finally:
            sys.modules.pop(MODULE_NAME, None)

    def test_build_settings_structure(self, monkeypatch):
        """No LLM, and the backend is pinned so MEMORY_API_KEY cannot hijack it."""
        pytest.importorskip("sentence_transformers")

        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")
        # A stray hosted key must not redirect the demo's writes.
        monkeypatch.setenv("MEMORY_API_KEY", "nams_not-a-real-key")

        try:
            module = _load(f"{MODULE_NAME}_settings")
            settings = module.build_settings()
            assert settings.llm is None
            assert settings.backend == "bolt"
        finally:
            sys.modules.pop(f"{MODULE_NAME}_settings", None)


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("NEO4J_URI"),
    reason="set NEO4J_URI to run the example end-to-end (the quick job has no database)",
)
class TestStrandsSessionManagerEndToEnd:
    """Runs the demo against Neo4j: persistence, restore, injection, reasoning."""

    @pytest.fixture
    def module(self, neo4j_env) -> Iterator[ModuleType]:
        pytest.importorskip("sentence_transformers")
        monkey = f"{MODULE_NAME}_e2e"
        try:
            yield _load(monkey)
        finally:
            sys.modules.pop(monkey, None)

    def test_main_persists_injects_and_restores(self, module, capsys):
        # main() owns its event loops (asyncio.run), so this test stays sync.
        module.main()
        out = capsys.readouterr().out

        # Persistence and restore across two manager instances.
        assert "Agent A persisted 2 messages" in out
        assert "Restored 2 messages for 'kyc-session'." in out

        # Retrieval injection: the shared brain reaches the other session.
        assert "<user_context>" in out
        assert "[entity] Acme Corp" in out
        assert "[preference] compliance:" in out
        # The original question survives injection.
        assert "Should we approve credit for Acme Corp?" in out

        # Long-term graph, :User identity, and reasoning memory.
        assert "Jane Doe" in out
        assert "Owner of 'kyc-session': analyst-1 (KYC analyst)" in out
        assert "tool call check_sanctions" in out

    def test_preferences_are_user_scoped(self, module, capsys):
        """analyst-1's preference must not leak into analyst-2's context block."""
        module.main()
        out = capsys.readouterr().out

        assert "[preference] compliance:" in out
        assert "Prefers bullet-point summaries" not in out

    def test_rerun_is_idempotent(self, module, capsys):
        """The demo clears its sessions, so counts do not drift across runs."""
        module.main()
        first = capsys.readouterr().out
        module.main()
        second = capsys.readouterr().out

        for out in (first, second):
            assert "Agent A persisted 2 messages" in out
            assert "Restored 2 messages for 'kyc-session'." in out
