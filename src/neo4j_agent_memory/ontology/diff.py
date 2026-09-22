"""Pure structural diff between two ontology documents.

Produces the same ``added`` / ``removed`` / ``renamed`` / ``modified`` shape
NAMS returns from ``GET /ontologies/{id}/diff``, so the bolt store and the
hosted service report changes identically.
"""

from __future__ import annotations

from typing import Any

from neo4j_agent_memory.ontology.models import (
    EntityTypeDef,
    OntologyDiff,
    OntologyDocument,
    RelationshipDef,
)

_ENTITY_FIELDS = (
    "pole_type",
    "subtype",
    "color",
    "icon",
    "description",
    "aliases",
    "threshold",
    "resolution_threshold",
    "review_threshold",
    "properties",
)

_RELATIONSHIP_FIELDS = (
    "description",
    "threshold",
    "inverse",
    "unique_source",
    "unique_target",
    "acyclic",
    "allow_self",
)


def diff_documents(a: OntologyDocument, b: OntologyDocument) -> OntologyDiff:
    """Diff two ontology documents (``a`` is the older revision).

    Entity types are keyed by label and relationships by their
    ``(type, source, target)`` triple, both case-insensitively. Renames cannot
    be inferred structurally, so ``renamed`` is always empty — a rename shows
    up as one removal plus one addition.

    Args:
        a: The "from" document.
        b: The "to" document.

    Returns:
        An :class:`OntologyDiff` with ``entity_types`` and ``relationships``
        populated and ``mode_change`` left unset (validation mode lives on the
        version row, not the document).
    """
    return OntologyDiff(
        entity_types=_diff_entity_types(a, b),
        relationships=_diff_relationships(a, b),
        mode_change=None,
    )


def _diff_entity_types(a: OntologyDocument, b: OntologyDocument) -> dict[str, Any]:
    old = {et.label.lower(): et for et in a.entity_types}
    new = {et.label.lower(): et for et in b.entity_types}

    added = [_dump(new[key]) for key in new if key not in old]
    removed = [_dump(old[key]) for key in old if key not in new]
    modified: list[dict[str, Any]] = []
    for key, after in new.items():
        before = old.get(key)
        if before is None:
            continue
        changes = _changes(before, after, _ENTITY_FIELDS)
        if changes:
            modified.append({"label": after.label, "changes": changes})

    return {"added": added, "removed": removed, "renamed": [], "modified": modified}


def _diff_relationships(a: OntologyDocument, b: OntologyDocument) -> dict[str, Any]:
    old = {_relationship_key(rel): rel for rel in a.relationships}
    new = {_relationship_key(rel): rel for rel in b.relationships}

    added = [_dump(new[key]) for key in new if key not in old]
    removed = [_dump(old[key]) for key in old if key not in new]
    modified: list[dict[str, Any]] = []
    for key, after in new.items():
        before = old.get(key)
        if before is None:
            continue
        changes = _changes(before, after, _RELATIONSHIP_FIELDS)
        if changes:
            modified.append(
                {
                    "type": after.type,
                    "source": after.source,
                    "target": after.target,
                    "changes": changes,
                }
            )

    return {"added": added, "removed": removed, "renamed": [], "modified": modified}


def _relationship_key(rel: RelationshipDef) -> tuple[str, str, str]:
    return (rel.type.lower(), rel.source.lower(), rel.target.lower())


def _dump(model: EntityTypeDef | RelationshipDef) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _changes(
    before: EntityTypeDef | RelationshipDef,
    after: EntityTypeDef | RelationshipDef,
    fields: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    old = before.model_dump(mode="json")
    new = after.model_dump(mode="json")
    for field in fields:
        if old.get(field) != new.get(field):
            changes[field] = {"from": old.get(field), "to": new.get(field)}
    return changes


__all__ = ["diff_documents"]
