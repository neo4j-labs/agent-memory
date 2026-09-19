"""Dependency-free unsupported-accessor sentinel shared by backend wiring.

MemoryClient uses this sentinel for features unavailable on the selected
backend. Keeping it in core lets Bolt wire hosted-only accessors without
importing the optional NAMS package or its HTTP dependencies. The historical
private class name and structured error behavior remain unchanged.
"""

from __future__ import annotations

from typing import NoReturn

from neo4j_agent_memory.core.exceptions import NotSupportedError


class _NamsUnsupported:
    """Sentinel — every attribute access on the resulting object raises.

    The exception message names the accessor and the attempted method so
    callers (and users reading tracebacks) see exactly which API isn't
    supported on NAMS.

    The shim is intentionally a regular instance attribute (not a
    descriptor), so writing ``client._users = _NamsUnsupported(...)``
    works during ``connect()``. Property access on the shim itself
    (e.g. ``client.users`` returning the shim) does NOT raise — only
    attempts to call methods on it do. This matters because some
    introspection / health checks read the attribute to see what's
    available.
    """

    __slots__ = ("_accessor", "_message", "_workaround")

    def __init__(
        self,
        accessor: str,
        message: str,
        *,
        workaround: str | None = None,
    ) -> None:
        self._accessor = accessor
        self._message = message
        self._workaround = workaround

    def __getattr__(self, name: str) -> NoReturn:
        # Called only when normal attribute lookup fails — every method
        # call on a fresh _NamsUnsupported hits this path.
        raise NotSupportedError(
            backend="nams",
            method=f"{self._accessor}.{name}",
            message=self._message,
            workaround=self._workaround,
        )

    def __repr__(self) -> str:
        return f"_NamsUnsupported({self._accessor!r})"

    def __bool__(self) -> bool:
        # Truthy — so ``if client.users:`` doesn't silently behave as
        # "no users layer". Callers should check the backend instead.
        return True


__all__ = ["_NamsUnsupported"]
