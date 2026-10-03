"""Mapping from free-form extractor labels onto POLE+O types.

Zero-shot extractors emit whatever labels the schema asked for
(``company``, ``phone number``, ``tv_show``); the graph stores POLE+O
``(type, subtype)`` pairs. This module is the single lookup table both
sides agree on, kept free of any extractor import so an ontology can use
it without pulling a model runtime in.
"""

from __future__ import annotations

#: Mapping from extractor labels (lower-case) to ``(TYPE, SUBTYPE | None)``.
DEFAULT_LABEL_MAPPING: dict[str, tuple[str, str | None]] = {
    # Person types -> (TYPE, SUBTYPE)
    "person": ("PERSON", None),
    "individual": ("PERSON", "INDIVIDUAL"),
    "alias": ("PERSON", "ALIAS"),
    "author": ("PERSON", "AUTHOR"),
    "actor": ("PERSON", "ACTOR"),
    "director": ("PERSON", "DIRECTOR"),
    # Organization types
    "organization": ("ORGANIZATION", None),
    "company": ("ORGANIZATION", "COMPANY"),
    "nonprofit": ("ORGANIZATION", "NONPROFIT"),
    "government": ("ORGANIZATION", "GOVERNMENT"),
    "educational institution": ("ORGANIZATION", "EDUCATIONAL"),
    "institution": ("ORGANIZATION", "INSTITUTION"),
    "studio": ("ORGANIZATION", "STUDIO"),
    "court": ("ORGANIZATION", "COURT"),
    # Location types
    "location": ("LOCATION", None),
    "address": ("LOCATION", "ADDRESS"),
    "city": ("LOCATION", "CITY"),
    "country": ("LOCATION", "COUNTRY"),
    "region": ("LOCATION", "REGION"),
    "landmark": ("LOCATION", "LANDMARK"),
    # Event types
    "event": ("EVENT", None),
    "incident": ("EVENT", "INCIDENT"),
    "meeting": ("EVENT", "MEETING"),
    "transaction": ("EVENT", "TRANSACTION"),
    "communication": ("EVENT", "COMMUNICATION"),
    "case": ("EVENT", "CASE"),
    # Object types
    "object": ("OBJECT", None),
    "vehicle": ("OBJECT", "VEHICLE"),
    "phone number": ("OBJECT", "PHONE"),
    "phone": ("OBJECT", "PHONE"),
    "email address": ("OBJECT", "EMAIL"),
    "email": ("OBJECT", "EMAIL"),
    "document": ("OBJECT", "DOCUMENT"),
    "device": ("OBJECT", "DEVICE"),
    "weapon": ("OBJECT", "WEAPON"),
    "product": ("OBJECT", "PRODUCT"),
    "book": ("OBJECT", "BOOK"),
    "film": ("OBJECT", "FILM"),
    "tv_show": ("OBJECT", "TV_SHOW"),
    "dataset": ("OBJECT", "DATASET"),
    "tool": ("OBJECT", "TOOL"),
    "technology": ("OBJECT", "TECHNOLOGY"),
    "drug": ("OBJECT", "DRUG"),
    "law": ("OBJECT", "LAW"),
    # Concept types (map to OBJECT with CONCEPT subtype)
    "concept": ("OBJECT", "CONCEPT"),
    "method": ("OBJECT", "METHOD"),
    "metric": ("OBJECT", "METRIC"),
    "financial_metric": ("OBJECT", "FINANCIAL_METRIC"),
    "role": ("OBJECT", "ROLE"),
    "industry": ("OBJECT", "INDUSTRY"),
    "genre": ("OBJECT", "GENRE"),
    "symptom": ("OBJECT", "SYMPTOM"),
    "procedure": ("OBJECT", "PROCEDURE"),
    "body_part": ("OBJECT", "BODY_PART"),
    "gene": ("OBJECT", "GENE"),
    "organism": ("OBJECT", "ORGANISM"),
    "disease": ("OBJECT", "DISEASE"),
    "character": ("OBJECT", "CHARACTER"),
    "award": ("OBJECT", "AWARD"),
    "monetary_amount": ("OBJECT", "MONETARY_AMOUNT"),
    "date": ("EVENT", "DATE"),
}

#: POLE+O top-level type names, recognised directly whatever their casing.
POLEO_TYPE_NAMES: frozenset[str] = frozenset(
    {"PERSON", "ORGANIZATION", "LOCATION", "EVENT", "OBJECT"}
)


def map_label_to_poleo(
    label: str,
    mapping: dict[str, tuple[str, str | None]] | None = None,
) -> tuple[str, str | None]:
    """Map an extractor label onto a POLE+O ``(type, subtype)`` pair.

    Resolution order: the explicit mapping, then a POLE+O type name spelled
    out directly, then a fallback onto ``OBJECT`` with the label as subtype.

    Args:
        label: The label as the extractor emitted it (any casing).
        mapping: Lookup table to use; defaults to :data:`DEFAULT_LABEL_MAPPING`.

    Returns:
        ``(TYPE, SUBTYPE | None)`` — ``TYPE`` is always one of the five POLE+O
        types.
    """
    table = DEFAULT_LABEL_MAPPING if mapping is None else mapping

    label_lower = label.lower()
    if label_lower in table:
        return table[label_lower]

    label_upper = label.upper()
    if label_upper in POLEO_TYPE_NAMES:
        return (label_upper, None)

    return ("OBJECT", label_upper)


__all__ = ["DEFAULT_LABEL_MAPPING", "POLEO_TYPE_NAMES", "map_label_to_poleo"]
