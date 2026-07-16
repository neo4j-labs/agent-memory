"""Type-level test: both backend short-term impls satisfy ShortTermProtocol."""

from __future__ import annotations

from neo4j_agent_memory.core.protocols import ShortTermProtocol
from neo4j_agent_memory.memory.short_term import ShortTermMemory
from neo4j_agent_memory.nams.short_term import NamsShortTermMemory


def _accepts(x: ShortTermProtocol) -> ShortTermProtocol:
    return x


def check(bolt: ShortTermMemory, nams: NamsShortTermMemory) -> None:
    # Both must be assignable to the Protocol with NO cast and NO ignore.
    _accepts(bolt)
    _accepts(nams)
