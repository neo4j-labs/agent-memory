"""Shared utilities for MCP tool, resource, and prompt modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastmcp import Context

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.integration import MemoryIntegration
    from neo4j_agent_memory.mcp._observer import MemoryObserver


def _lifespan_context(ctx: Context) -> dict[str, Any]:
    """Return the lifespan context dict, raising RuntimeError if unavailable.

    FastMCP 4 exposes the lifespan result directly as ``Context.lifespan_context``
    (it reads the owning server's cached lifespan result rather than the MCP
    session's, which matters once this server is mounted under another one).
    It returns an empty dict when no lifespan ran, which for this server means
    the tools have nothing to talk to — surface that as a RuntimeError rather
    than a KeyError deeper in the call.
    """
    context: dict[str, Any] = ctx.lifespan_context
    if not context:
        raise RuntimeError(
            "MCP lifespan context is not available (the server was created "
            "without settings, or no session is active)"
        )
    return context


def get_client(ctx: Context) -> MemoryClient:
    """Get MemoryClient from lifespan context.

    Args:
        ctx: FastMCP context with lifespan data.

    Returns:
        The MemoryClient instance.
    """
    from neo4j_agent_memory import MemoryClient as _MemoryClient

    return cast(_MemoryClient, _lifespan_context(ctx)["client"])


def get_integration(ctx: Context) -> MemoryIntegration:
    """Get MemoryIntegration from lifespan context.

    Args:
        ctx: FastMCP context with lifespan data.

    Returns:
        The MemoryIntegration instance.
    """
    from neo4j_agent_memory.integration import MemoryIntegration as _MemoryIntegration

    return cast(_MemoryIntegration, _lifespan_context(ctx)["integration"])


def get_observer(ctx: Context) -> MemoryObserver | None:
    """Get MemoryObserver from lifespan context, if available.

    Args:
        ctx: FastMCP context with lifespan data.

    Returns:
        The MemoryObserver instance, or None if not configured.
    """
    from neo4j_agent_memory.mcp._observer import MemoryObserver as _MemoryObserver

    raw = _lifespan_context(ctx).get("observer")
    return cast(_MemoryObserver, raw) if raw is not None else None
