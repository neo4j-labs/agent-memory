"""Scoped cleanup using the hosted API's supported conversation/entity routes."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import quote

# Start from retained message IDs, then inspect all sources and neighboring IDs.
PROVENANCE_QUERY = """
MATCH (m) WHERE m.id IN $message_ids
MATCH (m)-[:EXTRACTED_FROM]-(e:Entity)
WITH DISTINCT e
OPTIONAL MATCH (e)-[:EXTRACTED_FROM]-(source)
WITH e, collect(DISTINCT source.id) AS sources, count(DISTINCT source) AS source_count
OPTIONAL MATCH (e)--(neighbor)
RETURN e.id AS id, e.createdAt AS created_at, sources, source_count,
       collect(DISTINCT neighbor.id) AS neighbors, count(DISTINCT neighbor) AS neighbor_count
"""
RESIDUAL_QUERY = """
MATCH (n) WHERE n.id IN $ids OR n.conversationId = $conversation
RETURN n.id AS id, labels(n) AS labels
"""


async def request_json(http, method, route, **kwargs):
    response = await http.request(method, route, **kwargs)
    response.raise_for_status()
    return response.json()


async def query(http, cypher, params):
    result = await request_json(http, "POST", "query", json={"cypher": cypher, "params": params})
    rows = result.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("Query did not return rows; retained tutorial state")
    return rows


async def wait_for_extraction(http, conversation_id, count, *, timeout=60.0, interval=1.0):
    if timeout <= 0 or interval <= 0:
        raise ValueError("Polling timeout and interval must be positive")
    deadline = time.monotonic() + timeout
    route = f"conversations/{quote(conversation_id, safe='')}/extraction-status"
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Extraction did not settle; no cleanup was attempted")
        result = await asyncio.wait_for(request_json(http, "GET", route), remaining)
        summary = result.get("summary")
        if not isinstance(summary, dict) or any(
            type(value) is not int or value < 0 for value in summary.values()
        ):
            raise RuntimeError("Invalid extraction status; no cleanup was attempted")
        if any(summary.get(key, 0) for key in ("failed", "error", "cancelled")):
            raise RuntimeError("Extraction failed; retain state for inspection")
        terminal = {"done", "completed", "skipped"}
        pending = {"pending", "processing", "running", "in_progress", "queued"}
        if set(summary) - terminal - pending:
            raise RuntimeError("Unknown extraction status; retain state for inspection")
        if sum(summary.values()) == count and not any(summary.get(key, 0) for key in pending):
            return
        await asyncio.sleep(min(interval, max(0, deadline - time.monotonic())))


async def delete_and_verify(http, route):
    response = await http.delete(route)
    if response.status_code != 404:
        response.raise_for_status()
    readback = await http.get(route)
    if readback.status_code != 404:
        readback.raise_for_status()
        raise RuntimeError(f"Resource still exists after DELETE: {route}")


def exclusively_owned(row, message_ids, started_at):
    try:
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        started = datetime.fromisoformat(started_at)
        # collect(node.id) omits anonymous nodes. Compare node counts as well
        # so missing IDs cannot hide another source or adjacent resource.
        for field, count_field in (("sources", "source_count"), ("neighbors", "neighbor_count")):
            ids = row.get(field)
            count = row.get(count_field)
            if (
                not isinstance(ids, list)
                or any(not isinstance(value, str) or not value for value in ids)
                or type(count) is not int
                or count != len(set(ids))
            ):
                return False
        return (
            bool(row.get("sources"))
            and set(row["sources"]) <= message_ids
            and started <= created <= datetime.now(timezone.utc)
        )
    except (KeyError, ValueError, TypeError):
        return False


async def cleanup(http, state, *, timeout=60.0, interval=1.0) -> bool:
    """Retain shared/uncertain records; never use unsupported step/skill deletion."""
    if state.data.get("pending"):
        raise RuntimeError("Unresolved write; reconcile its outcome before cleanup")
    conversation_id = state.data.get("conversation_id")
    resources = state.data["resources"]
    if not conversation_id or conversation_id not in resources.get("conversation", {}):
        raise RuntimeError("No recorded conversation owned by this run")
    # A previous preflight cannot authorize a later deletion. Ownership and
    # neighboring sources may have changed while a failed run was interrupted.
    for entry in resources.get("entity", {}).values():
        if entry.get("status") != "deleted":
            entry["owned"] = False
    state.save()
    route = f"conversations/{quote(conversation_id, safe='')}"
    response = await http.get(route)
    owned_messages = set(resources.get("message", {}))
    conversation = resources["conversation"][conversation_id]
    if response.status_code != 404:
        response.raise_for_status()
        metadata = response.json()
        expected_metadata = conversation.get("metadata", {})
        if (
            expected_metadata.get("tutorialRun") != state.data["run_token"]
            or metadata.get("id") != conversation_id
            or metadata.get("metadata") != expected_metadata
        ):
            raise RuntimeError("Conversation ownership mismatch; no cleanup attempted")
        history = await request_json(http, "GET", route + "/messages")
        messages = history.get("messages") if isinstance(history, dict) else history
        if not isinstance(messages, list) or {m.get("id") for m in messages} != owned_messages:
            raise RuntimeError("Unrecorded or missing messages; no cleanup attempted")
        await wait_for_extraction(
            http, conversation_id, len(owned_messages), timeout=timeout, interval=interval
        )
        rows = await query(http, PROVENANCE_QUERY, {"message_ids": sorted(owned_messages)})
        candidates = {
            r["id"] for r in rows if exclusively_owned(r, owned_messages, state.data["started_at"])
        }
        while True:
            exclusive = {
                r["id"]
                for r in rows
                if r["id"] in candidates
                and isinstance(r.get("neighbors"), list)
                and set(r["neighbors"]) <= owned_messages | candidates
            }
            if exclusive == candidates:
                break
            candidates = exclusive
        for row in rows:
            # A graph edge to data outside this run prevents entity deletion too.
            owned = row["id"] in candidates
            state.record("entity", row["id"], status="retained", owned=owned, provenance=row)
        state.data["cleanup_preflight_complete"] = True
        state.save()
    elif not state.data.get("cleanup_preflight_complete"):
        raise RuntimeError(
            "Conversation disappeared before provenance capture; inspect retained IDs"
        )

    for entity_id, entry in resources.get("entity", {}).items():
        entity_route = "entities/" + quote(entity_id, safe="")
        if entry.get("status") == "deleted" or not entry.get("owned"):
            readback = await http.get(entity_route)
            if readback.status_code == 404:
                entry["status"] = "deleted"
                entry["disposition"] = "absence independently verified"
                state.save()
                continue
            readback.raise_for_status()
            entry["status"] = "retained"
            entry["owned"] = False
        if not entry.get("owned"):
            entry["disposition"] = "retained: shared or insufficient ownership evidence"
            state.save()
            continue
        await delete_and_verify(http, entity_route)
        entry["status"] = "deleted"
        state.save()
    await delete_and_verify(http, route)
    conversation["status"] = "deleted"
    state.save()
    ids = [
        conversation_id,
        *owned_messages,
        *(
            key
            for key, value in resources.get("entity", {}).items()
            if value["status"] == "deleted"
        ),
        *(
            key
            for kind, entries in resources.items()
            if kind not in {"conversation", "message", "entity"}
            for key in entries
        ),
    ]
    rows = await query(http, RESIDUAL_QUERY, {"ids": ids, "conversation": conversation_id})
    state.data["remaining_rows"] = rows
    remaining_ids = {row.get("id") for row in rows}
    for entries in resources.values():
        for resource_id, entry in entries.items():
            if resource_id in remaining_ids:
                entry["status"] = "retained"
                entry["disposition"] = "present in final exact-ID readback"
    for message_id, entry in resources.get("message", {}).items():
        if message_id not in remaining_ids:
            entry["status"] = "deleted"
    retained = {
        kind: [key for key, value in entries.items() if value["status"] != "deleted"]
        for kind, entries in resources.items()
    }
    state.data["retained_resources"] = {kind: ids for kind, ids in retained.items() if ids}
    state.save()
    # Steps/skills can remain attached to the deleted conversation. These are
    # recorded dispositions, not a successful assertion that every row vanished.
    unsupported = {
        key
        for kind, entries in resources.items()
        if kind not in {"conversation", "message", "entity"}
        for key in entries
    }
    if any(row.get("id") not in unsupported for row in rows):
        raise RuntimeError("Unexpected run-owned residuals remain; inspect state")
    print("Verified: owned conversation and proven-owned entities are absent")
    print(f"Retained resources: {state.data['retained_resources']}")
    return not state.data["retained_resources"]
