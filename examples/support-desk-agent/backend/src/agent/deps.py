"""What every agent run is given."""

from __future__ import annotations

from dataclasses import dataclass

from neo4j_agent_memory import BoltMemoryClient


@dataclass
class SupportDeskDeps:
    """Dependencies for one chat turn.

    ``client`` is whichever client the :class:`~src.memory.MemoryService` held
    when the turn started; a reconnect mid-turn swaps the service's client, not
    this one, and the old client stays open for a grace period.
    """

    client: BoltMemoryClient
    session_id: str
    user_message_id: str | None = None
    trace_id: str | None = None
