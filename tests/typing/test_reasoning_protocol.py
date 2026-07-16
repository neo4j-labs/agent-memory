"""Type-level test: both backend reasoning impls satisfy ReasoningProtocol."""

from __future__ import annotations

from neo4j_agent_memory.core.protocols import ReasoningProtocol
from neo4j_agent_memory.memory.reasoning import ReasoningMemory
from neo4j_agent_memory.nams.reasoning import NamsReasoningMemory


def _accepts(x: ReasoningProtocol) -> ReasoningProtocol:
    return x


def check(bolt: ReasoningMemory, nams: NamsReasoningMemory) -> None:
    # Both must be assignable to the Protocol with NO cast and NO ignore.
    _accepts(bolt)
    _accepts(nams)
