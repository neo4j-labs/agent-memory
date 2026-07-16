"""Type-level test: MemoryClient is generic over its three backend memory types.

Bare ``MemoryClient`` defaults (via PEP 696) to the base Protocols; the
backend-typed aliases (``BoltMemoryClient``, ``NamsMemoryClient``) parameterize
with the concrete backend classes directly, exposing backend-only methods.
"""

from __future__ import annotations

from typing_extensions import assert_type

from neo4j_agent_memory import BoltMemoryClient, MemoryClient, NamsMemoryClient
from neo4j_agent_memory.core.protocols import ShortTermProtocol
from neo4j_agent_memory.memory.long_term import Entity  # noqa: F401


def bare_defaults_to_base(c: MemoryClient) -> None:
    assert_type(c.short_term, ShortTermProtocol)  # bare == base protocol


async def bolt_exposes_bolt_only(c: BoltMemoryClient) -> None:
    await c.long_term.search_locations_near(latitude=0.0, longitude=0.0, radius_km=1.0)


async def nams_rejects_bolt_only(c: NamsMemoryClient) -> None:
    # NamsLongTermMemory has no search_locations_near — this must be an error.
    await c.long_term.search_locations_near(  # type: ignore[attr-defined]  # intentional: NAMS rejects bolt-only
        latitude=0.0, longitude=0.0, radius_km=1.0
    )
