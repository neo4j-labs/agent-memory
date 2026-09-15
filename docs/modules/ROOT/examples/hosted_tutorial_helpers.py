"""Bounded control flow shared by the hosted documentation exercises.

These helpers know only the documented run outcomes and SDK ontology methods.
They do not contact a service at import time.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any


async def poll_skill_run(
    fetch: Callable[[], Awaitable[dict[str, Any]]],
    *,
    timeout: float = 120.0,
    interval: float = 2.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Wait through queued/running; return a documented outcome or fail closed."""
    if timeout <= 0 or interval <= 0:
        raise ValueError("timeout and interval must be positive")
    deadline = clock() + timeout
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("Skill run did not settle before the tutorial deadline")
        run = await asyncio.wait_for(fetch(), timeout=remaining)
        outcome = run.get("outcome")
        if outcome in {"Created", "Withheld", "Failed"}:
            if outcome == "Created" and not run.get("skillId"):
                raise RuntimeError("Created run is missing its returned skillId")
            return run
        if outcome is not None or run.get("status") not in {"queued", "running"}:
            raise RuntimeError(f"Unrecognized skill run state: {run!r}")
        await sleep(min(interval, max(0.0, deadline - clock())))


def ontology_binding(active):
    """Capture authoritative IDs, mode and schema; never infer a latest revision."""
    values = {
        "version_id": active.version_id,
        "ontology_id": active.ontology_id,
        "revision": active.revision,
        "validation_mode": active.validation_mode,
    }
    if (
        not values["version_id"]
        or not values["ontology_id"]
        or type(values["revision"]) is not int
        or values["revision"] < 1
        or values["validation_mode"] not in {"permissive", "strict"}
    ):
        raise RuntimeError("No restorable authoritative active binding; no ontology was changed")
    values["document"] = active.document.model_dump(mode="json")
    return values


async def read_active_binding(http):
    """Read a restorable binding directly from the public REST response.

    SDK 0.6.0 get_active() infers version metadata from the latest revision.
    This tutorial reader uses the actual binding returned by /ontologies/active
    and the released public document model; it never substitutes a list lookup.
    """
    from neo4j_agent_memory.nams import OntologyDocument

    response = await http.get("ontologies/active")
    response.raise_for_status()
    payload = response.json()
    version = payload.get("version") if isinstance(payload, dict) else None
    if (
        not isinstance(version, dict)
        or any(
            not isinstance(version.get(key), str) or not version[key].strip()
            for key in ("id", "ontology_id")
        )
        or type(version.get("revision")) is not int
        or version["revision"] < 1
        or version.get("validation_mode") not in {"permissive", "strict"}
    ):
        raise RuntimeError("No restorable authoritative active binding; no ontology was changed")

    def document(value):
        if isinstance(value, str):
            value = json.loads(value)
        return OntologyDocument.model_validate(value).model_dump(mode="json")

    try:
        active_document = document(payload.get("ontology"))
        version_document = version.get("schema_json")
        if version_document is not None and document(version_document) != active_document:
            raise ValueError("Active and version schemas differ")
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid or conflicting active ontology schema; retained state") from exc
    return {
        "version_id": version["id"],
        "ontology_id": version["ontology_id"],
        "revision": version["revision"],
        "validation_mode": version["validation_mode"],
        "document": active_document,
    }


def _version_binding(version):
    from types import SimpleNamespace

    if version.document is None:
        raise RuntimeError("Returned version has no inspectable schema; retained clone")
    return ontology_binding(
        SimpleNamespace(
            version_id=version.id,
            ontology_id=version.ontology_id,
            revision=version.revision,
            validation_mode=version.validation_mode,
            document=version.document,
        )
    )


async def _require_binding(read_active, expected):
    actual = await read_active()
    if actual != expected:
        raise RuntimeError("Active binding changed or readback differs; retained state and clone")
    return actual


async def _clone_absent(ontology, clone_id):
    from neo4j_agent_memory.core.exceptions import NotFoundError

    try:
        await ontology.get(clone_id)
    except NotFoundError:
        return True
    return False


async def verify_ontology_restoration(ontology, state, *, read_active):
    """Fresh, read-only verification of the saved binding and exact clone absence."""
    run = state.data.get("ontology", {})
    if not run.get("previous"):
        raise RuntimeError("State has no captured prior binding")
    await _require_binding(read_active, run["previous"])
    clone_id = run.get("clone_id")
    if not clone_id or not await _clone_absent(ontology, clone_id):
        raise RuntimeError("Clone absence is not verified; inspect or recover this run")
    if state.data.get("pending"):
        raise RuntimeError(
            "An operation remains unresolved; run recover before claiming completion"
        )
    print(f"Verified: original version {run['previous']['version_id']} and mode restored")
    print(f"Verified: exercise clone {clone_id} is absent")


async def recover_ontology(ontology, state, *, read_active):
    """Restore only a recognized binding, then delete the proven-owned clone.

    An interrupted clone with no returned ID cannot be reconciled automatically.
    A concurrent editor's binding is never replaced. There is no server-side
    compare-and-swap here, so the exercise requires exclusive ontology editing.
    """
    from neo4j_agent_memory.core.exceptions import NotFoundError

    run = state.data.get("ontology", {})
    previous = run.get("previous")
    if not previous:
        raise RuntimeError("State has no captured prior binding; no recovery writes were made")
    current = await read_active()
    if current != previous and current != run.get("strict"):
        raise RuntimeError("Active binding changed unexpectedly; retained state and clone")
    clone_id = run.get("clone_id")
    pending = state.data.get("pending")
    if not clone_id:
        if pending:
            raise RuntimeError(
                "Clone outcome is uncertain with no returned ID; inspect retained state"
            )
        # A prerequisite failed before the first write.
        print("Verified: original binding unchanged; no clone was created")
        return
    owned = state.data["resources"].get("ontology", {}).get(clone_id, {})
    if owned.get("created_by_run") is not True:
        raise RuntimeError("Clone ownership is not established; no recovery writes were made")
    if pending:
        if not isinstance(pending, dict) or pending.get("operation") not in {
            "clone",
            "update",
            "activate",
            "restore",
            "delete",
        }:
            raise RuntimeError("Unrecognized pending operation; inspect retained state")
        # Keep uncertainty recorded until deleting the known clone resolves all
        # its revisions. Never replay a clone or an update with an unknown result.
        run.setdefault("interrupted_operations", []).append(pending)
        state.finish()
    if current != previous:
        state.begin({"operation": "restore", "version_id": previous["version_id"]})
        await ontology.activate(previous["version_id"])
        await _require_binding(read_active, previous)
        state.finish()
    else:
        await _require_binding(read_active, previous)
    run["restored"] = True
    state.save()
    print(f"Restored active version: {previous['version_id']} ({previous['validation_mode']})")

    # Recheck immediately before deletion. This detects observed concurrent
    # changes; it cannot make an unversioned service operation atomic.
    await _require_binding(read_active, previous)
    if not await _clone_absent(ontology, clone_id):
        state.begin({"operation": "delete", "ontology_id": clone_id})
        try:
            await ontology.delete(clone_id)
        except NotFoundError:
            pass  # A retry still requires the independent GET below.
        if not await _clone_absent(ontology, clone_id):
            raise RuntimeError(f"Deletion not verified; retained clone {clone_id}")
        state.finish()
    state.record("ontology", clone_id, status="deleted")
    for version_id, entry in state.data["resources"].get("ontology_version", {}).items():
        if entry.get("ontology_id") == clone_id:
            entry["status"] = "deleted_with_ontology"
    run["deleted"] = True
    run["status"] = "complete"
    state.save()
    print(f"Deleted exercise clone: {clone_id} (absence verified)")


@asynccontextmanager
async def temporary_strict_ontology(ontology: Any, template: str, state, *, read_active):
    """Persist the restoration target before mutation and retain failed recovery."""
    if state.data.get("ontology") or state.data.get("pending"):
        raise RuntimeError("Ontology exercise already started; inspect or recover its state")
    previous = await read_active()
    catalog = await ontology.list()
    if not any(item.name == template and item.is_system for item in catalog):
        raise RuntimeError("Required system template is absent; no ontology was changed")
    existing_ids = {item.id for item in catalog}
    state.data["ontology"] = {"previous": previous, "template": template, "status": "started"}
    state.save()
    print(f"Previous active version: {previous['version_id']} ({previous['validation_mode']})")
    try:
        state.begin({"operation": "clone", "template": template})
        clone = await ontology.clone(template)
        run = state.data["ontology"]
        run["clone_id"] = clone.ontology_id
        state.record(
            "ontology", clone.ontology_id, created_by_run=clone.ontology_id not in existing_ids
        )
        state.record("ontology_version", clone.id, ontology_id=clone.ontology_id)
        state.finish()
        if clone.ontology_id in existing_ids:
            raise RuntimeError(
                "Clone response identifies a preexisting ontology; ownership is unknown"
            )
        if clone.document is None:
            raise RuntimeError("Clone has no inspectable schema")
        print(f"Exercise clone: {clone.ontology_id}")
        state.begin({"operation": "update", "ontology_id": clone.ontology_id})
        strict = await ontology.update(clone.ontology_id, clone.document, validation_mode="strict")
        state.record("ontology_version", strict.id, ontology_id=strict.ontology_id)
        run["strict"] = _version_binding(strict)
        state.finish()
        if strict.ontology_id != clone.ontology_id or strict.validation_mode != "strict":
            raise RuntimeError("Strict revision response differs from the requested clone and mode")
        await _require_binding(read_active, previous)
        state.begin({"operation": "activate", "version_id": strict.id})
        await ontology.activate(strict.id)
        await _require_binding(read_active, run["strict"])
        state.finish()
        print(f"Activated strict version: {strict.id}")
        yield strict
    except BaseException as exercise_error:
        state.data["ontology"]["exercise_error"] = type(exercise_error).__name__
        state.save()
        try:
            await recover_ontology(ontology, state, read_active=read_active)
        except BaseException as recovery_error:
            raise recovery_error from exercise_error
        raise
    else:
        await recover_ontology(ontology, state, read_active=read_active)
