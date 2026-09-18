"""Backend-neutral ontology document model.

An *ontology* is a versioned, validated, typed schema for the knowledge
graph: entity types (each mapped onto a POLE+O ``pole_type``) with typed
properties (``required`` / ``unique`` / ``enum`` constraints) and typed
relationships with explicit source/target labels.

These models were promoted out of :mod:`neo4j_agent_memory.nams.ontology`
so both backends can share one document shape: NAMS parses them off the
wire, the bolt backend stores them as ``:OntologyVersion`` nodes, and
:mod:`neo4j_agent_memory.ontology.compile` turns them into a GLiNER2.5
``JointSchema``. Class names and field names are unchanged for wire
compatibility, and every extension field is defaulted so any payload that
parsed before still parses.

Parsing stays deliberately lenient (``extra="ignore"``): a structurally
broken document loads without raising, and :meth:`OntologyDocument.validate_structure`
is the single place that reports problems.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

#: The five POLE+O top-level types an ``EntityTypeDef.pole_type`` may name.
POLEO_TYPES: frozenset[str] = frozenset({"PERSON", "ORGANIZATION", "LOCATION", "EVENT", "OBJECT"})

#: The spelling a relationship type name must use: UPPER_SNAKE, letter-initial.
_UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _none_to_list(v: Any) -> Any:
    """Coerce a ``null`` collection to ``[]`` (the service emits null for empty)."""
    return [] if v is None else v


def _upper_snake(name: str) -> str:
    """The UPPER_SNAKE spelling of ``name``, for a "did you mean" suggestion.

    Deliberately a local three-liner rather than an import of
    :func:`neo4j_agent_memory.extraction.base.normalize_relation_type`: this
    module is the one schema shape both backends parse, and it stays free of
    the extraction package (which imports back into it).
    """
    collapsed = re.sub(r"[\s\-./]+", "_", name.strip())
    return re.sub(r"_+", "_", collapsed).strip("_").upper()


def _to_alias_map(v: Any) -> Any:
    """Coerce an ``aliases`` payload to ``{canonical: [surface form, ...]}``.

    The gazetteer is an optimisation, never load-bearing, so an unreadable
    payload is dropped rather than raised on: a ``null`` or a non-mapping
    (a list, say — the field's wire shape is only documented, not pinned)
    becomes ``{}``, and a scalar value becomes a one-element list.
    """
    if not isinstance(v, dict):
        return {}
    coerced: dict[str, list[str]] = {}
    for key, value in v.items():  # pyright: ignore[reportUnknownVariableType]
        if value is None:
            coerced[str(key)] = []
        elif isinstance(value, str):
            coerced[str(key)] = [value]
        elif isinstance(value, (list, tuple)):
            coerced[str(key)] = [str(item) for item in value]  # pyright: ignore
        else:
            coerced[str(key)] = [str(value)]
    return coerced


# -----------------------------------------------------------------------------
# Models — lenient (extra="ignore") so server additions don't break parsing.
# -----------------------------------------------------------------------------


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PropertyDef(_Lenient):
    """A typed property on an entity type."""

    name: str
    type: str  # string | datetime | date | float | integer
    required: bool = False
    unique: bool = False
    enum: list[str] | None = None
    description: str | None = None


class EntityTypeDef(_Lenient):
    """A typed entity in the ontology, mapped onto a POLE+O ``pole_type``.

    ``description`` is not documentation. GLiNER2.5 serialises it into the
    encoder input, so it behaves as a zero-shot annotation guideline: write
    it the way you would write instructions for a human annotator, negative
    cases included ("Not a job title.").
    """

    label: str
    pole_type: str  # PERSON | ORGANIZATION | LOCATION | EVENT | OBJECT
    subtype: str | None = None
    color: str | None = None
    icon: str | None = None
    properties: Annotated[list[PropertyDef], BeforeValidator(_none_to_list)] = Field(
        default_factory=list
    )
    description: str | None = None
    aliases: Annotated[dict[str, list[str]], BeforeValidator(_to_alias_map)] = Field(
        default_factory=dict,
        description="Canonical name -> known surface forms (resolution gazetteer)",
    )
    threshold: float | None = None
    resolution_threshold: float | None = None
    review_threshold: float | None = None


class RelationshipDef(_Lenient):
    """A typed relationship between two entity labels.

    ``source`` / ``target`` stay single labels (the NAMS wire shape). Several
    defs may share one ``type``; :func:`~neo4j_agent_memory.ontology.compile.compile_joint_schema`
    groups them into a single JointIE relation with head/tail lists.
    """

    type: str  # UPPER_SNAKE
    source: str
    target: str
    description: str | None = None
    threshold: float | None = None
    inverse: str | None = None
    unique_source: bool = False
    unique_target: bool = False
    acyclic: bool = False
    allow_self: bool = False


class DomainInfo(_Lenient):
    """Display + identity metadata for an ontology."""

    id: str
    name: str
    description: str | None = None
    tagline: str | None = None
    emoji: str | None = None


class OntologyDocument(_Lenient):
    """The parsed schema body — domain + entity types + relationships."""

    domain: DomainInfo
    entity_types: Annotated[list[EntityTypeDef], BeforeValidator(_none_to_list)] = Field(
        default_factory=list
    )
    relationships: Annotated[list[RelationshipDef], BeforeValidator(_none_to_list)] = Field(
        default_factory=list
    )
    no_self_loops: bool = True

    # -- lookups --------------------------------------------------------------

    def labels(self) -> list[str]:
        """Entity labels, in declaration order."""
        return [et.label for et in self.entity_types]

    def label_map(self) -> dict[str, tuple[str, str | None]]:
        """Lower-cased label -> ``(POLE_TYPE, SUBTYPE | None)``."""
        return {
            et.label.lower(): (
                et.pole_type.upper(),
                et.subtype.upper() if et.subtype else None,
            )
            for et in self.entity_types
        }

    def entity_type(self, label: str) -> EntityTypeDef | None:
        """Look one entity type up by label (case-insensitive)."""
        wanted = label.lower()
        for et in self.entity_types:
            if et.label.lower() == wanted:
                return et
        return None

    def label_for(self, pole_type: str, subtype: str | None = None) -> str | None:
        """Reverse the label map: ``(POLE_TYPE, SUBTYPE)`` -> declared label.

        This is the lookup the storage and validation paths need: entities are
        persisted with a POLE+O ``type``/``subtype`` pair, while the ontology's
        relationship endpoints are declared in terms of *labels*.

        An exact ``(pole_type, subtype)`` match wins. Failing that, a base
        declaration for the same ``pole_type`` (one with ``subtype=None``) is
        used, so an entity carrying a subtype the ontology does not declare
        still resolves to its base label.

        A **bare** ``pole_type`` — no subtype asked for — falls back once more,
        to the first label declared for it. Plenty of ontologies declare only
        subtyped labels (the built-in ``scientific`` template's sole PERSON is
        ``author -> PERSON:AUTHOR``); without this, ``add_entity("Alice",
        "PERSON")`` looked undeclared there, so strict mode rejected it and
        permissive mode dropped every relation touching a bare-typed endpoint.
        A *stated* subtype gets no such fallback: mapping ``PERSON:ALIAS`` onto
        a ``Customer = PERSON:INDIVIDUAL`` label would assert something the
        caller did not, so it stays undeclared and strict mode still rejects it.

        Args:
            pole_type: One of the five POLE+O types (any casing).
            subtype: Optional subtype (any casing).

        Returns:
            The declared label, or ``None`` when the ontology declares nothing
            usable for this ``pole_type``/``subtype`` pair.
        """
        wanted_type = pole_type.upper()
        wanted_subtype = subtype.upper() if subtype else None

        base: str | None = None
        first: str | None = None
        for et in self.entity_types:
            if et.pole_type.upper() != wanted_type:
                continue
            et_subtype = et.subtype.upper() if et.subtype else None
            if et_subtype == wanted_subtype:
                return et.label
            if et_subtype is None and base is None:
                base = et.label
            if first is None:
                first = et.label
        if base is not None:
            return base
        return first if wanted_subtype is None else None

    def declares(self, pole_type: str, subtype: str | None = None) -> bool:
        """Whether the ontology declares an entity type for this POLE+O pair.

        Deliberately defined in terms of :meth:`label_for`, so the two answers
        can never disagree: anything that resolves to a label is declared.

        Args:
            pole_type: One of the five POLE+O types (any casing).
            subtype: Optional subtype (any casing).

        Returns:
            ``True`` when :meth:`label_for` resolves to a declared label.
        """
        return self.label_for(pole_type, subtype) is not None

    def relationship_types(self) -> list[str]:
        """Distinct relationship type names, in declaration order."""
        seen: dict[str, None] = {}
        for rel in self.relationships:
            seen.setdefault(rel.type, None)
        return list(seen)

    def patterns(self) -> set[tuple[str, str, str]]:
        """Every legal ``(source_label, relation_type, target_label)`` triple."""
        return {(rel.source, rel.type, rel.target) for rel in self.relationships}

    def permits(self, source_label: str, rel_type: str, target_label: str) -> bool:
        """Whether the ontology declares this endpoint-typed triple.

        Comparison is case-insensitive on all three components.
        """
        wanted = (source_label.lower(), rel_type.lower(), target_label.lower())
        return any(
            (rel.source.lower(), rel.type.lower(), rel.target.lower()) == wanted
            for rel in self.relationships
        )

    # -- validation -----------------------------------------------------------

    def validate_structure(self) -> list[str]:
        """Return human-readable structural problems (empty list when sound).

        Parsing never raises on these; callers that need a sound document
        (``compile_joint_schema``, the bolt ontology store) check here and
        raise :class:`ValueError` themselves.
        """
        problems: list[str] = []
        known: dict[str, EntityTypeDef] = {}

        for et in self.entity_types:
            key = et.label.lower()
            if key in known:
                problems.append(f"duplicate entity label: {et.label!r}")
            else:
                known[key] = et
            if et.pole_type.upper() not in POLEO_TYPES:
                problems.append(
                    f"entity {et.label!r} has pole_type {et.pole_type!r}; "
                    f"expected one of {sorted(POLEO_TYPES)}"
                )
            for field, value in (
                ("threshold", et.threshold),
                ("resolution_threshold", et.resolution_threshold),
                ("review_threshold", et.review_threshold),
            ):
                if value is not None and not 0.0 <= value <= 1.0:
                    problems.append(
                        f"entity {et.label!r} has {field} {value!r}; expected a "
                        "confidence in [0, 1]"
                    )

        by_type: dict[str, list[RelationshipDef]] = {}
        seen_triples: set[tuple[str, str, str]] = set()
        for rel in self.relationships:
            if not _UPPER_SNAKE.match(rel.type):
                problems.append(
                    f"relationship type {rel.type!r} is not UPPER_SNAKE; expected "
                    "letters, digits and underscores only, starting with a letter "
                    f"(e.g. {_upper_snake(rel.type)!r})"
                )
            if rel.threshold is not None and not 0.0 <= rel.threshold <= 1.0:
                problems.append(
                    f"relationship {rel.type!r} has threshold {rel.threshold!r}; "
                    "expected a confidence in [0, 1]"
                )
            triple = (rel.type.lower(), rel.source.lower(), rel.target.lower())
            if triple in seen_triples:
                problems.append(f"duplicate relationship: {rel.type} {rel.source} -> {rel.target}")
            seen_triples.add(triple)
            by_type.setdefault(rel.type, []).append(rel)

            if rel.source.lower() not in known:
                problems.append(
                    f"relationship {rel.type!r} source {rel.source!r} is not a declared label"
                )
            if rel.target.lower() not in known:
                problems.append(
                    f"relationship {rel.type!r} target {rel.target!r} is not a declared label"
                )
            if rel.allow_self and rel.source.lower() != rel.target.lower():
                problems.append(
                    f"relationship {rel.type!r} sets allow_self but its endpoints differ "
                    f"({rel.source} -> {rel.target})"
                )

        for rel_type, defs in by_type.items():
            # Several defs may share one ``type`` (one per endpoint pair) and
            # the compiler folds them into a single JointIE relation. Only
            # ``allow_self``/``threshold``/``description`` have a defensible
            # merge (OR / min / first non-empty); the rest change the relation's
            # meaning, so a disagreement is an authoring error, not something
            # to resolve by taking whichever def happened to be declared first.
            for field in ("inverse", "unique_source", "unique_target", "acyclic"):
                values = {getattr(d, field) for d in defs}
                if len(values) > 1:
                    rendered = ", ".join(sorted(repr(value) for value in values))
                    problems.append(
                        f"relationship {rel_type!r} definitions disagree on "
                        f"{field} ({rendered}); declare the same value on every "
                        "source/target pair"
                    )

            inverses = {d.inverse for d in defs if d.inverse}
            for inverse in sorted(inverses):
                mirror = by_type.get(inverse)
                if mirror is None:
                    problems.append(f"relationship {rel_type!r} names unknown inverse {inverse!r}")
                    continue
                forward = {(d.source.lower(), d.target.lower()) for d in defs}
                backward = {(d.target.lower(), d.source.lower()) for d in mirror}
                if forward != backward:
                    problems.append(
                        f"relationship {rel_type!r} and its inverse {inverse!r} do not "
                        "mirror each other's source/target"
                    )

        return problems

    # -- identity -------------------------------------------------------------

    def content_key(self) -> str:
        """A deterministic, content-addressed key (not the ontology's name).

        Two documents that describe the same schema produce the same key even
        if they were built in a different order or loaded from different
        files, which makes it a safe cache key for compiled schemas — and a
        stable ``schema_hash``. Declaration order is *not* content: the
        ``entity_types`` and ``relationships`` lists are sorted by their own
        canonical JSON before hashing, so re-ordering a document does not
        invalidate a cached compile or look like a new revision.
        """
        data = self.model_dump(mode="json")
        for field in ("entity_types", "relationships"):
            values = data.get(field)
            if isinstance(values, list):
                data[field] = sorted(
                    values,  # pyright: ignore[reportUnknownArgumentType]
                    key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
                )
        return json.dumps(data, sort_keys=True, separators=(",", ":"))


class OntologySummary(_Lenient):
    """One row from ``list()`` — system templates + workspace-owned."""

    id: str
    name: str
    display_name: str | None = None
    description: str | None = None
    emoji: str | None = None
    tagline: str | None = None
    is_system: bool = False
    current_revision: int | None = None
    is_active: bool = False


class OntologyVersion(_Lenient):
    """An immutable ontology revision. ``document`` is the parsed schema.

    The field is named ``document`` (not ``schema``) to avoid shadowing
    Pydantic's reserved ``BaseModel.schema``.
    """

    id: str
    ontology_id: str
    revision: int
    validation_mode: str  # permissive | strict
    document: OntologyDocument | None = None
    schema_hash: str | None = None
    created_at: str | None = None
    message: str | None = None


class OntologyRecord(_Lenient):
    """Identity row for a workspace-owned or system ontology."""

    id: str
    name: str
    description: str | None = None
    workspace_id: str | None = None
    is_system: bool = False
    created_at: str | None = None


class Ontology(_Lenient):
    """A single ontology with its full revision history."""

    record: OntologyRecord
    versions: list[OntologyVersion] = Field(default_factory=list)


class ActiveOntology(_Lenient):
    """The currently-bound ontology, with version metadata composed in.

    ``validation_mode`` / ``revision`` / ``version_id`` are populated by a
    second lookup (the ``/ontologies/active`` response itself carries no
    version metadata).
    """

    document: OntologyDocument
    validation_mode: str | None = None
    revision: int | None = None
    ontology_id: str | None = None
    version_id: str | None = None


class _AllowExtra(BaseModel):
    """Base for shapes we pass through faithfully (server-defined, deep)."""

    model_config = ConfigDict(extra="allow")


class ImportWarning(_Lenient):
    """A non-fatal conversion warning from an ontology import."""

    code: str | None = None
    message: str | None = None
    path: str | None = None


class OntologyImportResult(_Lenient):
    """A *non-persisted* ontology draft converted from an external format.

    The converter produces an ontology body but does **not** save it —
    persist via ``client.ontology.create(...)`` (pass ``result.document``).
    """

    document: OntologyDocument | None = None
    warnings: list[ImportWarning] = Field(default_factory=list)
    detected_format: str | None = None
    suggested_name: str | None = None


class OntologyDiff(_AllowExtra):
    """Structural diff between two ontology revisions.

    ``entity_types`` and ``relationships`` carry ``added`` / ``removed`` /
    ``renamed`` / ``modified`` lists; they are passed through as dicts since
    their leaf shapes mirror the full ontology type system.
    """

    from_revision: int | None = None
    to_revision: int | None = None
    entity_types: dict[str, Any] = Field(default_factory=dict)
    relationships: dict[str, Any] = Field(default_factory=dict)
    mode_change: dict[str, Any] | None = None


class MigrationJob(_Lenient):
    """An asynchronous label-rename migration job.

    Enqueued by ``client.ontology.migrate(...)``; poll ``get_migration``
    for ``status`` / ``processed`` / ``total`` until it completes.
    """

    id: str
    ontology_id: str | None = None
    workspace_id: str | None = None
    status: str | None = None  # pending | running | completed | failed | paused
    total: int | None = None
    processed: int | None = None
    errored: int | None = None
    error_message: str | None = None
    spec: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_by: str | None = None


# -----------------------------------------------------------------------------
# Parsing helpers
# -----------------------------------------------------------------------------


def _parse_document(raw: Any) -> OntologyDocument | None:
    """Parse a schema body that may arrive as a JSON string or a mapping.

    Every failure mode returns ``None`` — unparseable JSON *and* a mapping
    that pydantic rejects (a stored ``:OntologyVersion.document`` missing
    ``domain``, say, or carrying a non-list ``entity_types``). Callers turn
    that into their own error: :meth:`BoltOntology.get_active
    <neo4j_agent_memory.ontology.store.BoltOntology.get_active>` raises
    ``SchemaError``. Letting a raw ``ValidationError`` escape would make one
    corrupt row fail ``connect()`` outright — including the
    ``client.ontology.delete()`` call needed to recover from it.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if isinstance(raw, dict):
        try:
            return OntologyDocument.model_validate(raw)
        except ValidationError:
            return None
    return None


def _parse_version(raw: dict[str, Any]) -> OntologyVersion:
    """Parse a version row, unwrapping the double-encoded ``schema_json``."""
    data = dict(raw)
    schema_json = data.pop("schema_json", None)
    doc = _parse_document(schema_json) if schema_json is not None else None
    return OntologyVersion.model_validate({**data, "document": doc})


#: Fields added in v0.7 that a pre-0.7 payload never carried, mapped to the
#: value that means "the author did not set this". ``exclude_none=True``
#: already drops the ``None`` defaults; these are the ones that would
#: otherwise appear as ``aliases: {}`` / ``acyclic: false`` / ``no_self_loops:
#: true`` in an outbound body. Keyed by the shape they belong to.
_EXTENSION_DEFAULTS: dict[str, dict[str, Any]] = {
    "document": {"no_self_loops": True},
    # ``description``/``threshold``/``resolution_threshold``/``review_threshold``
    # all default to None, so ``exclude_none`` has already dropped them.
    "entity_type": {"aliases": {}},
    # ``PropertyDef``'s only extension (``description``) is None-defaulted;
    # the entry exists so a future non-None default has an obvious home.
    "property": {},
    "relationship": {
        "unique_source": False,
        "unique_target": False,
        "acyclic": False,
        "allow_self": False,
    },
}


#: Sentinel for "this key is not an extension field", so a key whose value
#: happens to equal ``None`` is still distinguishable from an absent default.
_MISSING = object()


def _prune_defaults(data: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Drop the keys in ``data`` that sit at their "unset" default."""
    return {key: value for key, value in data.items() if defaults.get(key, _MISSING) != value}


def _as_document_dict(schema: OntologyDocument | dict[str, Any]) -> dict[str, Any]:
    """Normalise an ``OntologyDocument`` or raw mapping to a JSON-ready dict.

    This is the **outbound** shape — what ``NamsOntology.create``/``update``
    put on the wire — not the storage shape (the bolt store serialises the
    whole model, extension fields included). v0.7 added fields to every level
    of the document, and NAMS support for them is **unverified**: the service
    may reject a body carrying keys it does not know. So a field still sitting
    at its default is omitted, which makes a v0.6-shaped document round-trip
    byte-compatible; a field the caller deliberately set is always sent,
    because dropping it would silently change the ontology being stored.

    Args:
        schema: The document, or an already-shaped mapping (passed through).

    Returns:
        A JSON-ready mapping with ``None`` values and default-valued
        extension fields omitted.
    """
    if not isinstance(schema, OntologyDocument):
        return schema

    data = _prune_defaults(schema.model_dump(exclude_none=True), _EXTENSION_DEFAULTS["document"])
    entity_types = data.get("entity_types")
    if isinstance(entity_types, list):
        pruned_types: list[dict[str, Any]] = []
        for entity_type in entity_types:  # pyright: ignore[reportUnknownVariableType]
            pruned = _prune_defaults(entity_type, _EXTENSION_DEFAULTS["entity_type"])
            properties = pruned.get("properties")
            if isinstance(properties, list):
                pruned["properties"] = [
                    _prune_defaults(prop, _EXTENSION_DEFAULTS["property"])
                    for prop in properties  # pyright: ignore[reportUnknownVariableType]
                ]
            pruned_types.append(pruned)
        data["entity_types"] = pruned_types
    relationships = data.get("relationships")
    if isinstance(relationships, list):
        data["relationships"] = [
            _prune_defaults(rel, _EXTENSION_DEFAULTS["relationship"])
            for rel in relationships  # pyright: ignore[reportUnknownVariableType]
        ]
    return data


__all__ = [
    "POLEO_TYPES",
    "ActiveOntology",
    "DomainInfo",
    "EntityTypeDef",
    "ImportWarning",
    "MigrationJob",
    "Ontology",
    "OntologyDiff",
    "OntologyDocument",
    "OntologyImportResult",
    "OntologyRecord",
    "OntologySummary",
    "OntologyVersion",
    "PropertyDef",
    "RelationshipDef",
]
