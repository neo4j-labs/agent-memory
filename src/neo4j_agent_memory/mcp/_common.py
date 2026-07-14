"""Shared utilities for MCP tool, resource, and prompt modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastmcp import Context

if TYPE_CHECKING:
    from neo4j_agent_memory import MemoryClient
    from neo4j_agent_memory.integration import MemoryIntegration
    from neo4j_agent_memory.mcp._observer import MemoryObserver


def _lifespan_context(ctx: Context) -> dict[str, Any]:
    """Return the lifespan context dict, raising RuntimeError if unavailable."""
    rc = ctx.request_context
    if rc is None:
        raise RuntimeError("MCP request context is not available (no active session)")
    # rc.lifespan_context is typed as the generic LifespanContextT which resolves to
    # Any in the FastMCP Context property — cast to dict[str, Any] at this boundary.
    return cast(dict[str, Any], rc.lifespan_context)


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
