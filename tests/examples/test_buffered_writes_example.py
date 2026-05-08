"""Smoke tests for the buffered-writes example."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
BUFFERED_DIR = EXAMPLES_DIR / "buffered-writes"


@pytest.mark.syntax
class TestBufferedWritesStructure:
    def test_required_files_exist(self):
        for filename in ["README.md", "main.py"]:
            assert (BUFFERED_DIR / filename).exists(), f"Missing: {filename}"

    def test_main_compiles(self):
        ast.parse((BUFFERED_DIR / "main.py").read_text())


@pytest.mark.imports
class TestBufferedWritesImports:
    def test_required_imports_resolve(self):
        from neo4j_agent_memory import MemoryClient, MemorySettings  # noqa: F401
        from neo4j_agent_memory.config.settings import (  # noqa: F401
            EmbeddingConfig,
            ExtractionConfig,
        )
        from neo4j_agent_memory.memory.buffered import BufferedWriter  # noqa: F401

    def test_build_settings_uses_buffered_mode(self, monkeypatch):
        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")

        spec = importlib.util.spec_from_file_location(
            "buffered_writes_main", BUFFERED_DIR / "main.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            settings = module.build_settings()
            assert settings.memory.write_mode == "buffered"
            assert settings.memory.max_pending == 200
        finally:
            sys.modules.pop("buffered_writes_main", None)


@pytest.mark.syntax
class TestBufferedWritesContent:
    def test_main_uses_buffered_submit(self):
        source = (BUFFERED_DIR / "main.py").read_text()
        assert "client.buffered.submit" in source

    def test_main_calls_flush(self):
        source = (BUFFERED_DIR / "main.py").read_text()
        assert "client.flush()" in source
