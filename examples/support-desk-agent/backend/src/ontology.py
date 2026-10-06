"""The customer-support ontology: loading it, renaming a type, label lookups.

``data/customer-support.ontology.yaml`` is shaped after the STATE-Bench
customer-support environment: customers, orders, order lines, products and
warranties. The seed stores it as revision 1 and activates it; the Ontology
panel's revision renames ``Warranty`` to ``WarrantyCoverage``.

Tools and routes never hard-code a label. They ask the active document for the
label of a ``(POLE_TYPE, SUBTYPE)`` role (:func:`role_label`), so the same code
works before and after the rename.

Policies are records too (``OBJECT:POLICY``), but the ontology does not declare
them: as an extraction label, "Policy" caught every "return" and "refund".
"""

from __future__ import annotations

from typing import Any

from neo4j_agent_memory.ontology import OntologyDocument, load_ontology
from src.config import DATA_DIR

ONTOLOGY_FILE = DATA_DIR / "customer-support.ontology.yaml"

#: The ontology's identity comes from the document's ``domain`` block.
DOMAIN_ID = "customer-support"
DOMAIN_NAME = "Customer Support (STATE-Bench)"

#: The POLE+O pair behind each role the tools look up. The *label* for each
#: pair comes from whichever revision is active.
CUSTOMER = ("PERSON", "CUSTOMER")
ORDER = ("EVENT", "ORDER")
ORDER_LINE = ("OBJECT", "ORDER_LINE")
PRODUCT = ("OBJECT", "PRODUCT")
WARRANTY = ("EVENT", "WARRANTY")
POLICY = ("OBJECT", "POLICY")

#: The label a role falls back to when nothing declares it (no active ontology).
_FALLBACK_LABELS = {
    CUSTOMER: "Customer",
    ORDER: "Order",
    ORDER_LINE: "OrderLine",
    PRODUCT: "Product",
    WARRANTY: "Warranty",
    POLICY: "Policy",
}

#: The revision the Ontology panel demonstrates: a terminology change (what a
#: warranty record holds is the coverage), migrated onto the existing nodes.
RENAME_FROM = "Warranty"
RENAME_TO = "WarrantyCoverage"

#: POLE+O labels. Every entity node carries one besides its ontology label.
POLE_LABELS = frozenset({"Person", "Object", "Location", "Event", "Organization"})


def load_document() -> OntologyDocument:
    """``data/customer-support.ontology.yaml`` as an :class:`OntologyDocument`."""
    document = load_ontology(ONTOLOGY_FILE)
    problems = document.validate_structure()
    if problems:
        raise ValueError(f"{ONTOLOGY_FILE.name} is not a valid ontology: {problems}")
    return document


def rename_entity_type(document: OntologyDocument, old: str, new: str) -> OntologyDocument:
    """Return a copy of ``document`` with one entity type relabelled.

    Relationship endpoints reference entity types by label, so they are
    rewritten too; otherwise the new revision declares relationships between a
    type that no longer exists and fails validation.
    """
    revised = document.model_copy(deep=True)
    for entity_type in revised.entity_types:
        if entity_type.label == old:
            entity_type.label = new
    for relationship in revised.relationships:
        if relationship.source == old:
            relationship.source = new
        if relationship.target == old:
            relationship.target = new
    return revised


def role_label(document: OntologyDocument | None, role: tuple[str, str]) -> str:
    """The label ``document`` declares for a ``(POLE_TYPE, SUBTYPE)`` role."""
    if document is not None:
        label = document.node_label(*role)
        if label:
            return label
    return _FALLBACK_LABELS[role]


def entity_labels(labels: list[str] | tuple[str, ...]) -> list[str]:
    """An entity's labels without ``Entity``, most specific first.

    ``[:Entity, :Object, :OrderLine]`` becomes ``["OrderLine", "Object"]``: the
    ontology label leads and the POLE+O label follows.
    """
    kept = [label for label in labels if label != "Entity"]
    return sorted(kept, key=lambda label: (label in POLE_LABELS, label))


def describe_document(document: OntologyDocument) -> dict[str, Any]:
    """The entity types and relationships of a document, JSON-ready."""
    return {
        "domain_id": document.domain.id,
        "entity_types": [
            {
                "label": et.label,
                "pole_type": et.pole_type,
                "subtype": et.subtype,
                "description": et.description,
            }
            for et in document.entity_types
        ],
        "relationships": [
            {"type": rel.type, "source": rel.source, "target": rel.target}
            for rel in document.relationships
        ],
    }
