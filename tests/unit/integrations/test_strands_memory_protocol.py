"""The store depends on strands' LTM module, added in strands-agents 1.44.0."""

from __future__ import annotations

import pytest

pytest.importorskip("strands", reason="strands-agents not installed")


class TestMemoryProtocolAvailable:
    def test_memory_module_exports_the_protocol(self) -> None:
        from strands.memory import (
            AddMessagesContext,
            MemoryEntry,
            MemoryStore,
            MemoryStoreConfig,
            SearchOptions,
        )

        assert MemoryStore is not None
        assert MemoryStoreConfig is not None
        assert SearchOptions is not None
        assert AddMessagesContext is not None
        assert MemoryEntry(content="x").content == "x"

    def test_optional_methods_are_detected_by_type_not_instance(self) -> None:
        """_has_method inspects type(store); an inherited stub counts as absent."""
        from strands.memory.types import MemoryStore as Proto
        from strands.memory.types import _has_method

        class Bare(Proto):
            async def search(self, query: str, options: object = None) -> list[object]:
                return []

        assert _has_method(Bare(), "search") is True
        assert _has_method(Bare(), "add") is False
        assert _has_method(Bare(), "add_messages") is False
