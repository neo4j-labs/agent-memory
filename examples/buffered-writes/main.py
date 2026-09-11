"""Buffered writes: the agent answers the user before persistence completes.

Fire-and-forget writes, added in v0.2.0:

* ``MemorySettings.memory.write_mode = "buffered"`` starts a background drainer.
* ``client.buffered.submit(query, params)`` queues a write and returns.
* ``client.flush()`` drains the queue (``client.wait_for_pending()`` is an alias).
* ``client.buffered.pending`` is the live queue depth.
* ``client.write_errors`` collects background failures instead of raising them
  into the agent's hot path.

The script runs four sections, in order:

1. **Measured** — the same sequential 50-turn loop run twice, once with
   ``write_mode="sync"`` and once with ``"buffered"``, so the latency claim is a
   number you can reproduce rather than an assertion.
2. **Queue depth** — ``pending`` right after the loop (machine-dependent) and
   the ``pending == 0`` invariant after ``flush()``.
3. **Back-pressure** — a client with ``max_pending=4`` makes ``submit()`` block
   until the drainer catches up.
4. **Error channel** — one deliberately invalid write lands in
   ``client.write_errors`` and never raises.

**Bolt only.** ``client.buffered`` raises ``NotSupportedError`` on the hosted
NAMS backend, which commits writes server-side; ``client.write_errors`` is
always empty there.

Run from the repo root::

    uv run python examples/buffered-writes/main.py
"""

from __future__ import annotations

import asyncio
import os
import time

from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    ExtractionConfig,
    ExtractorType,
    MemoryConfig,
)
from neo4j_agent_memory.memory.buffered import BufferedWriteError

# One sequential agent-turn loop per write mode, so the two totals are
# comparable. Exported so the smoke test can assert the persisted shape.
TURNS = 50
SYNC_SESSION = "buffered-writes-sync"
BUFFERED_SESSION = "buffered-writes-buffered"
DEMO_SESSIONS = [SYNC_SESSION, BUFFERED_SESSION]

# Deliberately invalid Cypher — section 4 uses it to populate the error channel.
BROKEN_QUERY = "MERGE (t:AgentTurn {turn: $turn}) SET t.oops ="


def build_settings(
    *,
    write_mode: str = "buffered",
    max_pending: int = 200,
) -> MemorySettings:
    """Settings for the demo: no LLM, a local embedder, no extractor."""
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        llm=None,
        # v0.3+ provider-string shorthand for a local sentence-transformers
        # embedder — no external API calls.
        embedding="sentence-transformers/all-MiniLM-L6-v2",
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
        memory=MemoryConfig(
            # "sync" (the default) awaits every submit inline; "buffered"
            # enqueues and lets a background task drain to Neo4j.
            write_mode=write_mode,
            # 200 is the library default. Section 3 drops it to 4 to make the
            # back-pressure behaviour visible.
            max_pending=max_pending,
        ),
    )


async def reset_demo_data(client: MemoryClient) -> None:
    """Clear previous runs so the final counts mean something.

    Routed through ``submit`` + ``flush``: one API for writes you *do* want to
    await, executed in submission order (a single drainer task).
    """
    for query, params in (
        (
            "MATCH (t:AgentTurn) WHERE t.session IN $sessions DETACH DELETE t",
            {"sessions": DEMO_SESSIONS},
        ),
        (
            """
            MATCH (c:Conversation) WHERE c.session_id IN $sessions
            OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message)
            DETACH DELETE c, m
            """,
            {"sessions": DEMO_SESSIONS},
        ),
        ("MATCH (p:BackPressureProbe) DETACH DELETE p", {}),
    ):
        await client.buffered.submit(query, params)
    await client.flush()


async def agent_turn(client: MemoryClient, session_id: str, turn: int) -> str:
    """One agent turn: an inline memory write, two derived writes, a response.

    The boundary this example is about:

    * Value-returning APIs (``add_message`` hands back a ``Message``) commit
      inline — they cannot be deferred, because the caller reads the result.
    * Derived writes that nothing in this turn reads back are what the
      fire-and-forget channel is for.
    """
    message = await client.short_term.add_message(
        session_id,
        "user",
        f"turn {turn}: what did we decide?",
        extraction_mode="skip",  # no extractor needed for this demo
        generate_embedding=False,  # keeps the measurement about write latency
    )

    # Derived write 1: an audit node for the turn.
    await client.buffered.submit(
        """
        MERGE (t:AgentTurn {session: $session, turn: $turn})
        ON CREATE SET t.recorded_at = datetime()
        SET t.message_id = $message_id
        """,
        {"session": session_id, "turn": turn, "message_id": str(message.id)},
    )
    # Derived write 2: a session-level counter.
    await client.buffered.submit(
        """
        MATCH (c:Conversation {session_id: $session})
        SET c.turns_recorded = coalesce(c.turns_recorded, 0) + 1
        """,
        {"session": session_id},
    )

    # Returned without waiting for the derived writes to commit.
    return f"response to turn {turn}"


async def run_workload(
    mode: str,
    session_id: str,
    *,
    reset: bool = False,
) -> tuple[float, int]:
    """Run ``TURNS`` sequential turns in one write mode.

    Returns the user-visible wall time in ms and the queue depth measured the
    instant the last turn returned.
    """
    async with MemoryClient(build_settings(write_mode=mode)) as client:
        if reset:
            await reset_demo_data(client)

        start = time.perf_counter()
        for turn in range(TURNS):
            # An agent turn loop is sequential: turn N+1 starts after the user
            # has seen the response to turn N.
            await agent_turn(client, session_id, turn)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Machine-dependent: 0 means the drainer kept up with the loop.
        pending = client.buffered.pending
        await client.flush()
        # After flush the queue is empty — that is the invariant that matters.
        assert client.buffered.pending == 0
        # Leaving the ``async with`` block would drain it too: close() flushes
        # before disconnecting, so a clean shutdown never loses writes.
        return elapsed_ms, pending


async def demo_back_pressure(
    *,
    submissions: int = 50,
    max_pending: int = 4,
) -> tuple[float, int]:
    """Show ``submit()`` blocking once the bounded queue is full."""
    async with MemoryClient(build_settings(max_pending=max_pending)) as client:
        start = time.perf_counter()
        high_water = 0
        for i in range(submissions):
            await client.buffered.submit("MERGE (p:BackPressureProbe {i: $i})", {"i": i})
            high_water = max(high_water, client.buffered.pending)
        elapsed_ms = (time.perf_counter() - start) * 1000
        await client.flush()

        await client.buffered.submit("MATCH (p:BackPressureProbe) DETACH DELETE p")
        await client.flush()
        return elapsed_ms, high_water


async def demo_error_channel() -> list[BufferedWriteError]:
    """Submit one invalid write and read it back off ``client.write_errors``."""
    async with MemoryClient(build_settings()) as client:
        # Neither call raises: the drainer captures the failure so a bad
        # derived write can never break the agent's response path.
        await client.buffered.submit(BROKEN_QUERY, {"turn": -1})
        await client.flush()
        return client.write_errors


async def count_turns(session_id: str) -> int:
    """Verification read via the portable Cypher accessor."""
    async with MemoryClient(build_settings()) as client:
        rows = await client.query.cypher(
            "MATCH (t:AgentTurn {session: $session}) RETURN count(t) AS cnt",
            {"session": session_id},
        )
        return int(rows[0]["cnt"])


async def main() -> None:
    print("1. Sync vs buffered — same loop, same writes, measured")
    sync_ms, _ = await run_workload("sync", SYNC_SESSION, reset=True)
    buffered_ms, pending_after_loop = await run_workload("buffered", BUFFERED_SESSION)
    print(f"   sync:     {sync_ms:8.1f} ms for {TURNS} turns ({sync_ms / TURNS:.2f} ms/turn)")
    print(
        f"   buffered: {buffered_ms:8.1f} ms for {TURNS} turns ({buffered_ms / TURNS:.2f} ms/turn)"
    )
    print(
        f"   → {sync_ms / buffered_ms:.1f}x faster on the user-visible path, "
        f"{(sync_ms - buffered_ms) / TURNS:.2f} ms saved per turn"
    )
    print("   (each turn also does one inline add_message — identical in both modes)")

    print("\n2. Queue depth")
    print(
        f"   pending when the last turn returned: {pending_after_loop}  # varies: 0 = drainer kept up"
    )
    print("   pending after flush():               0  # the invariant that matters")

    print("\n3. Back-pressure (50 submits, roomy queue vs. max_pending=4)")
    roomy_ms, roomy_high_water = await demo_back_pressure(max_pending=200)
    bounded_ms, bounded_high_water = await demo_back_pressure(max_pending=4)
    print(f"   max_pending=200: {roomy_ms:7.2f} ms, queue peaked at {roomy_high_water}")
    print(f"   max_pending=4:   {bounded_ms:7.2f} ms, queue peaked at {bounded_high_water}")
    print("   submit() blocks once the queue is full — bounded memory, no dropped writes")

    print("\n4. Error channel (one deliberately invalid write)")
    errors = await demo_error_channel()
    if errors:
        for err in errors:
            print(f"   query:  {err.query.strip()[:40]!r}")
            print(f"   error:  {type(err.error).__name__}")
            print(f"   when:   {err.when.isoformat()}")
        print("   submit() and flush() both returned normally — nothing raised")
    else:
        print("   no errors recorded (unexpected — the invalid query should fail)")

    print("\nVerification")
    print(f"   AgentTurn rows for the buffered session: {await count_turns(BUFFERED_SESSION)}")
    print(f"   AgentTurn rows for the sync session:     {await count_turns(SYNC_SESSION)}")


if __name__ == "__main__":
    asyncio.run(main())
