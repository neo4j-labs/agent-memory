"""The support-desk ontology: import, repair, rename, and label lookups.

``REPAIRS``, :func:`repair_draft` and :func:`rename_entity_type` are copied
from ``examples/ontology-lifecycle-bolt/main.py`` (a parity test keeps
``REPAIRS`` identical): the local Arrows import maps every label onto
``OBJECT``, so the draft is repaired with the POLE+O types and descriptions
GLiNER2.5 reads as annotation guidelines.

Tools and routes never hard-code ``:Ticket``. They ask the active document for
the label of ``EVENT:TICKET`` (:func:`ticket_label`), so the same code works
before and after the ``Ticket`` -> ``SupportCase`` rename.
"""

from __future__ import annotations

from typing import Any

from neo4j_agent_memory import BoltMemoryClient
from neo4j_agent_memory.ontology import OntologyDocument
from src.config import DATA_DIR

ARROWS_FILE = DATA_DIR / "support-desk.arrows.json"

#: The ontology's identity comes from the document's ``domain`` block.
DOMAIN_ID = "support-desk"
DOMAIN_NAME = "Support Desk"

#: What the Arrows import cannot know. The hosted service infers these POLE+O
#: types itself; the local converter falls back to OBJECT and says so. Each
#: description ends in the negative case, because GLiNER2.5 reads descriptions
#: as annotation guidelines.
REPAIRS: dict[str, tuple[str, str, str]] = {
    "Customer": (
        "PERSON",
        "CUSTOMER",
        "A shopper who contacts support, named in full (Priya Raman). "
        "Never a product, an order or a ticket.",
    ),
    "Order": (
        "EVENT",
        "ORDER",
        "A purchase, identified by an order number that starts with SO- (SO-4417). "
        "Never the product it contains.",
    ),
    "Product": (
        "OBJECT",
        "PRODUCT",
        "An item the store sells, named by its product name (Aurora Desk Lamp). "
        "Never an order number or a ticket number.",
    ),
    "Ticket": (
        "EVENT",
        "TICKET",
        "A support ticket, identified by a reference that starts with TK- (TK-2210). "
        "Never the order or the product it is about.",
    ),
}

#: The POLE+O pair behind each role the tools look up. The *label* for each
#: pair comes from whichever revision is active.
CUSTOMER = ("PERSON", "CUSTOMER")
ORDER = ("EVENT", "ORDER")
PRODUCT = ("OBJECT", "PRODUCT")
TICKET = ("EVENT", "TICKET")

#: The label a role falls back to when nothing declares it (no active ontology).
_FALLBACK_LABELS = {CUSTOMER: "Customer", ORDER: "Order", PRODUCT: "Product", TICKET: "Ticket"}

#: POLE+O labels. Every entity node carries one besides its ontology label.
POLE_LABELS = frozenset({"Person", "Object", "Location", "Event", "Organization"})


def repair_draft(document: OntologyDocument) -> OntologyDocument:
    """Return the imported draft with POLE+O types, subtypes and descriptions set."""
    repaired = document.model_copy(deep=True)
    repaired.domain.id = DOMAIN_ID
    repaired.domain.name = DOMAIN_NAME
    repaired.domain.description = "E-commerce support desk: customers, orders, products, tickets"
    for entity_type in repaired.entity_types:
        if entity_type.label in REPAIRS:
            pole_type, subtype, description = REPAIRS[entity_type.label]
            entity_type.pole_type = pole_type
            entity_type.subtype = subtype
            entity_type.description = description
    return repaired


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


async def import_support_desk(client: BoltMemoryClient) -> OntologyDocument:
    """Convert ``data/support-desk.arrows.json`` locally and repair the draft."""
    draft = await client.ontology.import_(
        content=ARROWS_FILE.read_text(encoding="utf-8"), format="arrows"
    )
    if draft.document is None:
        raise RuntimeError(f"Could not convert {ARROWS_FILE.name}: {draft.warnings}")
    return repair_draft(draft.document)


def role_label(document: OntologyDocument | None, role: tuple[str, str]) -> str:
    """The label ``document`` declares for a ``(POLE_TYPE, SUBTYPE)`` role."""
    if document is not None:
        label = document.node_label(*role)
        if label:
            return label
    return _FALLBACK_LABELS[role]


def ticket_label(document: OntologyDocument | None) -> str:
    """``Ticket`` under revision 1, ``SupportCase`` after the rename."""
    return role_label(document, TICKET)


def entity_labels(labels: list[str] | tuple[str, ...]) -> list[str]:
    """An entity's labels without ``Entity``, most specific first.

    ``[:Entity, :Event, :Ticket]`` becomes ``["Ticket", "Event"]``: the
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
