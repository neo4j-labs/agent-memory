"""Tests for nams/_unsupported.py — the _NamsUnsupported sentinel shim.

Used in Phase 5 by ``MemoryClient`` to stand in for bolt-only accessors
(``client.users``, ``client.buffered``, ``client.consolidation``) when
``backend="nams"``. Property access on the sentinel is harmless;
attempting to call a method raises :class:`NotSupportedError` with a
structured message naming the accessor and method.
"""

from __future__ import annotations

import pytest

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.nams._unsupported import _NamsUnsupported


def test_legacy_module_reexports_the_shared_sentinel():
    from neo4j_agent_memory.core._unsupported import _NamsUnsupported as SharedSentinel

    assert _NamsUnsupported is SharedSentinel


class TestAttributeAccess:
    def test_method_call_raises(self):
        shim = _NamsUnsupported("users", "User memory is bolt-only.")
        with pytest.raises(NotSupportedError) as exc_info:
            shim.upsert_user(identifier="alice")
        err = exc_info.value
        assert err.backend == "nams"
        assert err.method == "users.upsert_user"
        assert "bolt-only" in str(err)

    def test_async_method_pattern_raises(self):
        """Even before await, attribute access on the shim raises.

        The shim doesn't return a coroutine — it raises synchronously
        when the would-be method name is looked up.
        """
        shim = _NamsUnsupported("buffered", "Buffered writes are bolt-only.")
        with pytest.raises(NotSupportedError):
            _ = shim.submit  # attribute lookup itself raises

    def test_different_methods_yield_distinct_messages(self):
        shim = _NamsUnsupported("consolidation", "Hygiene jobs are bolt-only.")
        with pytest.raises(NotSupportedError) as e1:
            shim.dedupe_entities()
        assert e1.value.method == "consolidation.dedupe_entities"

        with pytest.raises(NotSupportedError) as e2:
            shim.archive_expired_conversations()
        assert e2.value.method == "consolidation.archive_expired_conversations"

    def test_workaround_propagated(self):
        shim = _NamsUnsupported(
            "graph",
            "Direct Neo4j driver access is bolt-only.",
            workaround="Use client.query.cypher() for portable read-only queries.",
        )
        with pytest.raises(NotSupportedError) as exc_info:
            shim.execute_read("MATCH (n) RETURN n")
        err = exc_info.value
        assert err.workaround is not None
        assert "client.query.cypher" in err.workaround
        assert "Workaround:" in str(err)


class TestSentinelSemantics:
    def test_repr_shows_accessor(self):
        shim = _NamsUnsupported("users", "foo")
        assert repr(shim) == "_NamsUnsupported('users')"

    def test_truthy(self):
        """Truthy so ``if client.users:`` doesn't lie about availability."""
        shim = _NamsUnsupported("users", "foo")
        assert bool(shim) is True
        assert shim  # used in conditional

    def test_property_access_is_safe(self):
        """The shim object itself can be assigned + introspected without error.

        Only attribute lookup ON the shim raises. The Phase 5 wiring
        ``client._users = _NamsUnsupported(...)`` shouldn't trigger any
        side effects.
        """
        shim = _NamsUnsupported("users", "foo")
        # We can examine the instance via dunders — these don't go through
        # __getattr__ (they're dunder slots).
        assert isinstance(shim, _NamsUnsupported)
        assert repr(shim)  # __repr__ works
        assert bool(shim)  # __bool__ works
