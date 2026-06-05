"""Strands SessionManager backed by neo4j-agent-memory (bolt or NAMS).

Maps a Strands session onto one ``Conversation`` — no Strands-specific
node types are written to the graph. Persistence is memory-grade: text
turns are stored (and feed entity extraction / the shared brain);
tool-use blocks and ``agent.state`` are not round-tripped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

try:
    from strands.hooks import (  # used by Neo4jSessionManager.register_hooks
        AfterInvocationEvent,  # noqa: F401
        HookRegistry,  # noqa: F401
        MessageAddedEvent,  # noqa: F401
    )
    from strands.session.session_manager import SessionManager  # noqa: F401
    from strands.types.exceptions import SessionException  # noqa: F401
except ImportError as e:  # pragma: no cover - exercised via package __init__
    raise ImportError(
        "strands-agents is required for the Strands session manager. "
        "Install with: pip install neo4j-agent-memory[strands]"
    ) from e

if TYPE_CHECKING:
    from strands.types.content import Message as StrandsMessage  # noqa: F401

    from neo4j_agent_memory import MemoryClient, MemorySettings  # noqa: F401

logger = logging.getLogger(__name__)

#: Conversation-metadata key linking a Conversation to a Strands session id.
_SESSION_KEY = "strands_session_id"


@dataclass
class Neo4jRetrievalConfig:
    """Opt-in per-turn long-term memory injection settings.

    When passed to :class:`Neo4jSessionManager`, each user message
    triggers concurrent long-term searches and the results are prepended
    to the message in-memory inside a ``<context_tag>`` block. The stored
    message is always the user's original.
    """

    top_k: int = 10
    min_score: float = 0.2
    include_entities: bool = True
    include_preferences: bool = True
    include_facts: bool = False
    context_tag: str = "user_context"
