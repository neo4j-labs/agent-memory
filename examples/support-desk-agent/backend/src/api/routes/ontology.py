"""The Ontology panel: revisions, diff, the Warranty -> WarrantyCoverage rename, activation.

Everything here goes through ``client.ontology`` (``BoltOntology``): revisions
are immutable ``:OntologyVersion`` nodes, exactly one is active per database,
and ``migrate()`` relabels the entities already in the graph synchronously.

After any activation the app reconnects (see :mod:`src.memory`): a bolt client
resolves its ontology at connect time, so until then it would keep extracting
against the previous revision. Every response reports what the app's client
resolved (``client``) so the UI can show the two side by side.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from neo4j_agent_memory import BoltMemoryClient
from neo4j_agent_memory.core.exceptions import NotFoundError, NotSupportedError
from neo4j_agent_memory.graph.query_builder import ontology_node_label
from neo4j_agent_memory.ontology.models import ActiveOntology, Ontology, OntologyDiff
from src.api import MemoryClientDep, memory_service
from src.api.schemas import (
    ActivateRequest,
    ActivateResult,
    ActiveOntologyView,
    ClientBinding,
    DiffSection,
    EntityTypeView,
    MigrationView,
    ModeChange,
    OntologyDiffView,
    OntologyOverview,
    RelationshipView,
    RenameRequest,
    RenameResult,
    RevisionView,
)
from src.memory import MemoryService
from src.ontology import DOMAIN_ID, DOMAIN_NAME, rename_entity_type

router = APIRouter()

#: How many ``:Entity`` nodes carry each label (labels passed as a parameter).
LABEL_COUNTS = """
MATCH (e:Entity)
UNWIND labels(e) AS label
WITH label WHERE label IN $labels
RETURN label, count(*) AS count
"""


async def _active(client: BoltMemoryClient) -> ActiveOntology | None:
    try:
        return await client.ontology.get_active()
    except NotSupportedError:
        return None


async def _ontology_in_view(
    client: BoltMemoryClient, active: ActiveOntology | None
) -> Ontology | None:
    """The active ontology, or the stored ``customer-support`` one when nothing is bound."""
    ontology_id = active.ontology_id if active is not None else None
    if ontology_id is None:
        stored = [s for s in await client.ontology.list() if not s.is_system]
        match = next((s for s in stored if s.name in (DOMAIN_ID, DOMAIN_NAME)), None)
        ontology_id = match.id if match is not None else None
    if ontology_id is None:
        return None
    try:
        return await client.ontology.get(ontology_id)
    except NotFoundError:
        return None


def _binding(service: MemoryService) -> ClientBinding:
    return ClientBinding(**service.resolved())


def diff_view(diff: OntologyDiff) -> OntologyDiffView:
    def section(raw: dict[str, Any]) -> DiffSection:
        return DiffSection(
            added=list(raw.get("added") or []),
            removed=list(raw.get("removed") or []),
            renamed=list(raw.get("renamed") or []),
            modified=list(raw.get("modified") or []),
        )

    return OntologyDiffView(
        from_revision=diff.from_revision,
        to_revision=diff.to_revision,
        entity_types=section(diff.entity_types),
        relationships=section(diff.relationships),
        mode_change=ModeChange.model_validate(diff.mode_change) if diff.mode_change else None,
    )


@router.get("/ontology", response_model=OntologyOverview, response_model_by_alias=True)
async def overview(client: MemoryClientDep, request: Request) -> OntologyOverview:
    """The active revision, the app client's binding, every revision, and label counts."""
    active = await _active(client)
    ontology = await _ontology_in_view(client, active)

    active_view = None
    if active is not None:
        document = active.document
        active_view = ActiveOntologyView(
            ontology_id=active.ontology_id,
            version_id=active.version_id,
            revision=active.revision,
            validation_mode=active.validation_mode,
            domain_id=document.domain.id,
            entity_types=[
                EntityTypeView(
                    label=et.label,
                    pole_type=et.pole_type,
                    subtype=et.subtype,
                    description=et.description,
                )
                for et in document.entity_types
            ],
            relationships=[
                RelationshipView(type=rel.type, source=rel.source, target=rel.target)
                for rel in document.relationships
            ],
        )

    revisions: list[RevisionView] = []
    declared: list[str] = []
    for version in ontology.versions if ontology is not None else []:
        labels = version.document.labels() if version.document is not None else []
        declared.extend(label for label in labels if label not in declared)
        revisions.append(
            RevisionView(
                version_id=version.id,
                revision=version.revision,
                validation_mode=version.validation_mode,
                is_active=active is not None and version.id == active.version_id,
                created_at=version.created_at,
                labels=labels,
            )
        )
    if active is not None:
        declared.extend(label for label in active.document.labels() if label not in declared)

    # Count under the node label each declared label is written as.
    node_labels = {label: ontology_node_label(label) or label for label in declared}
    rows = await client.query.cypher(LABEL_COUNTS, {"labels": list(set(node_labels.values()))})
    counts = {row["label"]: int(row["count"]) for row in rows}
    return OntologyOverview(
        active=active_view,
        client=_binding(memory_service(request)),
        revisions=revisions,
        label_counts={
            label: counts.get(node_label, 0) for label, node_label in node_labels.items()
        },
    )


@router.get("/ontology/diff", response_model=OntologyDiffView, response_model_by_alias=True)
async def diff(
    client: MemoryClientDep, from_revision: int = Query(ge=1), to_revision: int = Query(ge=1)
) -> OntologyDiffView:
    """What changed between two revisions of the ontology in view."""
    ontology = await _ontology_in_view(client, await _active(client))
    if ontology is None:
        raise HTTPException(status_code=404, detail="No customer-support ontology is stored")
    try:
        result = await client.ontology.diff(ontology.record.id, from_revision, to_revision)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return diff_view(result)


@router.post("/ontology/rename", response_model=RenameResult, response_model_by_alias=True)
async def rename(client: MemoryClientDep, body: RenameRequest, request: Request) -> RenameResult:
    """Rename one entity type and migrate the graph onto the new revision.

    ``update`` (a new revision) -> ``diff`` -> ``migrate(dry_run=True)`` ->
    ``migrate`` -> ``activate`` -> reconnect. Activation is per database: every
    client that connects with ``use_active_ontology=True`` follows it.
    """
    active = await _active(client)
    if active is None or active.ontology_id is None or active.version_id is None:
        raise HTTPException(status_code=409, detail="No ontology is active in this database")
    labels = active.document.labels()
    if body.old not in labels:
        raise HTTPException(
            status_code=409,
            detail=f"{body.old!r} is not declared by the active revision ({', '.join(labels)})",
        )
    if body.new in labels:
        raise HTTPException(
            status_code=409, detail=f"{body.new!r} is already declared by the active revision"
        )

    revised = rename_entity_type(active.document, body.old, body.new)
    try:
        version = await client.ontology.update(
            active.ontology_id, revised, validation_mode=body.validation_mode
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    changes = await client.ontology.diff(
        active.ontology_id, active.revision or version.revision - 1, version.revision
    )
    mapping = [(body.old, body.new)]
    preview = await client.ontology.migrate(
        active.ontology_id,
        from_version_id=active.version_id,
        to_version_id=version.id,
        type_mappings=mapping,
        dry_run=True,
    )
    job = await client.ontology.migrate(
        active.ontology_id,
        from_version_id=active.version_id,
        to_version_id=version.id,
        type_mappings=mapping,
    )
    await client.ontology.activate(version.id)
    service = memory_service(request)
    await service.reconnect()
    return RenameResult(
        revision=version.revision,
        version_id=version.id,
        diff=diff_view(changes),
        dry_run_total=int(preview.total or 0),
        migration=MigrationView(
            id=job.id,
            status=job.status,
            total=job.total,
            processed=job.processed,
            errored=job.errored,
        ),
        client=_binding(service),
    )


@router.post("/ontology/activate", response_model=ActivateResult)
async def activate(
    client: MemoryClientDep, body: ActivateRequest, request: Request
) -> ActivateResult:
    """Bind one revision for the whole database, then reconnect the app's client."""
    try:
        version = await client.ontology.activate(body.version_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    service = memory_service(request)
    await service.reconnect()
    return ActivateResult(
        revision=version.revision,
        validation_mode=version.validation_mode,
        client=_binding(service),
    )
