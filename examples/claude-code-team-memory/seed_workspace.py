"""Seed the shared workspace with the team decisions the editors will recall.

This is the only script in the example that writes memory, and it exists so the
first question anyone asks their editor — "why did we pick Neo4j 5.26 LTS?" —
has an answer on day one instead of after a week of usage.

What it does
------------
1. ``create_conversation`` — NAMS mints the conversation id server-side.
2. ``bulk_add_messages`` — the whole decision log in one round-trip (100 max).
3. ``wait_for_extraction`` — NAMS extracts entities in a background pipeline, so
   a read straight after the write can legitimately come back empty. Await the
   pipeline instead of sleeping.
4. ``search_entities`` / ``query.cypher`` — read back what the server extracted,
   so a silent write failure is visible here rather than in someone's editor.

The decisions, people and components below are synthetic and generated in this
file. Nothing resembles a real organisation.

Usage
-----
::

    uv run python seed_workspace.py
    uv run python seed_workspace.py --dry-run     # print the transcript, write nothing
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from _shared import CONVERSATION_NAME, connect_nams, load_env

from neo4j_agent_memory.core.exceptions import (
    AuthenticationError,
    NotSupportedError,
    RateLimitError,
    TransportError,
)

#: Four (fictional) people and the decisions they recorded. Written as a
#: conversation rather than as entities on purpose: the point of the example is
#: that NAMS extracts the entity graph from ordinary prose, so whoever asks
#: later does not have to know the schema.
DECISIONS: list[dict[str, str]] = [
    {
        "role": "user",
        "content": (
            "Alice Nakamura: ADR-001 — we standardise on Neo4j 5.26 LTS for the "
            "Atlas service. 5.26 is the long-term-support line, so we get "
            "security patches without tracking monthly releases."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Recorded ADR-001: Atlas uses Neo4j 5.26 LTS. Rationale: LTS patch "
            "cadence. Owner: Alice Nakamura."
        ),
    },
    {
        "role": "user",
        "content": (
            "Bob Okonkwo: ADR-002 — the Atlas ingest worker talks to Neo4j over "
            "Bolt with the async driver. We rejected the HTTP API because batch "
            "writes were three times slower in our load test."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Recorded ADR-002: Bolt + async driver for the Atlas ingest worker. "
            "Rejected alternative: HTTP API (3x slower on batch writes)."
        ),
    },
    {
        "role": "user",
        "content": (
            "Priya Raman: ADR-003 — agent memory for the Helios assistant goes "
            "through the hosted Neo4j Agent Memory Service rather than a "
            "self-managed database. We do not want to be on call for a graph."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Recorded ADR-003: Helios uses the hosted Neo4j Agent Memory "
            "Service. Rationale: no on-call burden. Owner: Priya Raman."
        ),
    },
    {
        "role": "user",
        "content": (
            "Diego Ferreira: ADR-004 — embeddings are text-embedding-3-small. "
            "We benchmarked gemini-embedding-001 too; recall was comparable and "
            "the cost difference did not justify a second provider."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Recorded ADR-004: text-embedding-3-small for Helios embeddings. "
            "Evaluated: gemini-embedding-001. Owner: Diego Ferreira."
        ),
    },
    {
        "role": "user",
        "content": (
            "Alice Nakamura: ADR-005 — the Atlas API is deployed to Frankfurt "
            "only, because the customer data residency clause names the EU."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Recorded ADR-005: Atlas API deploys to Frankfurt (EU data "
            "residency). Owner: Alice Nakamura."
        ),
    },
    {
        "role": "user",
        "content": (
            "Bob Okonkwo: ADR-006 — we retired the Orion batch exporter in "
            "March. Anything that needs its output reads from the Atlas API now."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Recorded ADR-006: Orion batch exporter retired; consumers migrate "
            "to the Atlas API. Owner: Bob Okonkwo."
        ),
    },
    {
        "role": "user",
        "content": (
            "Priya Raman: ADR-007 — reasoning traces are on in staging and off "
            "in production until we have a retention policy. Revisit in Q4."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Recorded ADR-007: reasoning traces staging-only pending a "
            "retention policy. Revisit Q4. Owner: Priya Raman."
        ),
    },
    {
        "role": "user",
        "content": (
            "Diego Ferreira: ADR-008 — every new service gets a doctor script "
            "before it gets a dashboard. Atlas and Helios both have one."
        ),
    },
]

#: Entities the extraction pipeline should find in the transcript above. Used
#: as the readiness assertion for ``wait_for_extraction`` — a specific name is a
#: far stronger signal than "at least one entity exists", because NAMS entity
#: search is nearest-neighbour and returns top-k regardless of relevance.
EXPECTED_ENTITIES = ["Alice Nakamura", "Atlas"]

#: Written explicitly (not left to extraction) because the editors query them by
#: name: these are the services the decisions are about.
SERVICES = [
    ("Atlas", "ORGANIZATION", "Internal data platform service. Neo4j 5.26 LTS over Bolt."),
    ("Helios", "ORGANIZATION", "Customer-facing assistant. Memory via hosted NAMS."),
]


async def seed(client: Any, *, timeout: float) -> int:
    # 1. Conversation ------------------------------------------------------
    conversation = await client.short_term.create_conversation(CONVERSATION_NAME)
    conversation_id = str(conversation.id)
    print(f"Conversation {CONVERSATION_NAME!r} → {conversation_id}")

    # 2. The whole decision log in one round-trip --------------------------
    stored = await client.short_term.bulk_add_messages(conversation_id, DECISIONS)
    print(f"Stored {len(stored)} decision messages in one request.")

    # 3. A couple of explicit entities ------------------------------------
    # NAMS resolves before it creates, so re-running this script merges onto the
    # existing nodes instead of duplicating them.
    for name, entity_type, description in SERVICES:
        entity = await client.long_term.add_entity(name, entity_type, description=description)
        print(f"Entity: {entity.display_name} ({entity.full_type})")

    # Preferences and facts are bolt-only. Showing the failure rather than
    # describing it: on NAMS this raises, and the workaround is to carry the
    # same information as entity descriptions (above) or to run the self-hosted
    # MCP server against your own Neo4j, where `memory_add_fact` works.
    try:
        await client.long_term.add_fact("Atlas", "uses", "Neo4j 5.26 LTS")
    except NotSupportedError as exc:
        print(f"Facts are bolt-only, as expected: {exc}")

    # 4. Await the asynchronous extraction pipeline ------------------------
    status = await client.short_term.get_extraction_status(conversation_id)
    print(f"Extraction right after the write: {status.pending_count} message(s) pending")
    settled = await client.long_term.wait_for_extraction(
        session_id=conversation_id,
        expected_names=EXPECTED_ENTITIES,
        timeout=timeout,
    )
    print(f"Extraction settled: {settled}")

    # 5. Read it back ------------------------------------------------------
    found = await client.long_term.search_entities("Atlas architecture decisions", limit=10)
    print(f"Searchable entities: {len(found)}")
    for entity in found[:10]:
        print(f"   {entity.display_name} ({entity.full_type})")

    try:
        rows = await client.query.cypher(
            "MATCH (e:Entity) RETURN count(e) AS entities",
        )
    except NotSupportedError as exc:
        # The Cypher endpoint is a Platinum-tier feature.
        print(f"Cypher is not available on this deployment: {exc}")
    else:
        print(f"Cypher round-trip: {rows}")

    print("\nSeeded. Point your editor at the workspace and ask it:")
    print('   "Why did we choose Neo4j 5.26 LTS, and who decided?"')
    print("\nExport the conversation id so doctor.py checks the same one:")
    print(f"   export TEAM_MEMORY_CONVERSATION_ID={conversation_id}")
    return 0 if settled else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed a NAMS workspace with team decisions.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the transcript and exit without writing anything.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for server-side extraction (default: 60).",
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    load_env()
    args = build_parser().parse_args(argv)

    if args.dry_run:
        print(f"Dry run: {len(DECISIONS)} message(s) would go to {CONVERSATION_NAME!r}.\n")
        for message in DECISIONS:
            print(f"   {message['role']:>9}: {message['content'][:88]}…")
        print(f"\nPlus {len(SERVICES)} entity write(s): {', '.join(s[0] for s in SERVICES)}.")
        print("Nothing was written. Re-run without --dry-run to seed.")
        return 0

    client = await connect_nams()
    try:
        return await seed(client, timeout=args.timeout)
    except AuthenticationError as exc:
        print(f"Rejected by NAMS: {exc}\nCheck MEMORY_API_KEY and MEMORY_WORKSPACE_ID.")
        return 1
    except (RateLimitError, TransportError) as exc:
        print(f"Request failed: {exc}")
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
