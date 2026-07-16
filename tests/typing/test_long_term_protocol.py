"""Type-level test: both backend long-term impls satisfy LongTermProtocol."""

from __future__ import annotations

from neo4j_agent_memory.core.protocols import LongTermProtocol
from neo4j_agent_memory.memory.long_term import LongTermMemory
from neo4j_agent_memory.nams.long_term import NamsLongTermMemory


def _accepts(x: LongTermProtocol) -> LongTermProtocol:
    return x


def check(bolt: LongTermMemory, nams: NamsLongTermMemory) -> None:
    # Both must be assignable to the Protocol with NO cast and NO ignore.
    _accepts(bolt)
    _accepts(nams)
