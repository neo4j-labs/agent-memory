"""Type-level test: concrete bolt classes satisfy their Bolt sub-Protocols.

Each Bolt sub-Protocol adds the bolt-only methods on top of the shared
base Protocol. Passing a concrete bolt instance into a function typed
by the sub-Protocol (with no ``cast``/``# type: ignore``) proves nominal
conformance, and reaching a bolt-only method through the parameter
proves the extra methods are actually declared there (not just
inherited from the base Protocol).
"""

from __future__ import annotations

from typing_extensions import assert_type

from neo4j_agent_memory.core.protocols import (
    BoltLongTermProtocol,
    BoltReasoningProtocol,
    BoltShortTermProtocol,
)
from neo4j_agent_memory.memory.long_term import LongTermMemory
from neo4j_agent_memory.memory.reasoning import ReasoningMemory
from neo4j_agent_memory.memory.short_term import ShortTermMemory


def _accepts_short_term(x: BoltShortTermProtocol) -> BoltShortTermProtocol:
    assert_type(x, BoltShortTermProtocol)
    return x


def _accepts_long_term(x: BoltLongTermProtocol) -> BoltLongTermProtocol:
    assert_type(x, BoltLongTermProtocol)
    return x


def _accepts_reasoning(x: BoltReasoningProtocol) -> BoltReasoningProtocol:
    assert_type(x, BoltReasoningProtocol)
    return x


def check_short_term(bolt: ShortTermMemory) -> None:
    # Bolt satisfies the richer protocol with no cast/ignore.
    narrowed = _accepts_short_term(bolt)
    # Bolt-only method (absent from the base ShortTermProtocol) is reachable.
    _ = narrowed.migrate_message_links


def check_long_term(bolt: LongTermMemory) -> None:
    narrowed = _accepts_long_term(bolt)
    _ = narrowed.find_potential_duplicates


def check_reasoning(bolt: ReasoningMemory) -> None:
    narrowed = _accepts_reasoning(bolt)
    _ = narrowed.get_tool_stats
