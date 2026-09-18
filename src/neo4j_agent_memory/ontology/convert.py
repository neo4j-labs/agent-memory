"""Converters into and out of :class:`OntologyDocument`.

The ontology document is the one schema shape the runtime consumes. Every
other schema the library accepts — the legacy
:class:`~neo4j_agent_memory.schema.models.EntitySchemaConfig`, the GLiNER
domain catalog, an arrows.app export, a JSON/YAML file on disk — enters
through here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from neo4j_agent_memory.ontology.models import (
    DomainInfo,
    EntityTypeDef,
    ImportWarning,
    OntologyDocument,
    PropertyDef,
    RelationshipDef,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.schema.models import EntitySchemaConfig


class _DomainSchemaLike(Protocol):
    """The GLiNER domain-catalog shape, duck-typed to avoid the import."""

    name: str
    entity_types: dict[str, str]
    relation_types: dict[str, str]


# -----------------------------------------------------------------------------
# EntitySchemaConfig <-> OntologyDocument
# -----------------------------------------------------------------------------


def _subtype_label(type_name: str, subtype: str, declared: set[str]) -> str:
    """Pick a label for ``subtype`` that no other declared label already uses.

    Subtype names are only unique *within* their entity type in the legacy
    schema, but ontology labels are the document's primary key. Two types
    sharing a subtype name (``PERSON: [CUSTOMER, OTHER]`` and
    ``ORGANIZATION: [SUPPLIER, OTHER]``) would otherwise declare ``OTHER``
    twice, which :meth:`OntologyDocument.validate_structure` rejects — and
    since ``schema_config.custom_schema_path`` is loaded through this path,
    that made an existing deployment unable to ``connect()`` at all.

    The first claimant keeps the bare name (so the common, collision-free case
    is unchanged); later ones are qualified as ``TYPE_SUBTYPE``.

    Args:
        type_name: The owning entity type's name.
        subtype: The subtype name.
        declared: Lower-cased labels already spoken for.

    Returns:
        A label not present in ``declared``.
    """
    if subtype.lower() not in declared:
        return subtype
    qualified = f"{type_name}_{subtype}"
    if qualified.lower() not in declared:
        return qualified
    suffix = 2
    while f"{qualified}_{suffix}".lower() in declared:
        suffix += 1
    return f"{qualified}_{suffix}"


def from_entity_schema(schema: EntitySchemaConfig) -> OntologyDocument:
    """Convert a legacy :class:`EntitySchemaConfig` into an ontology document.

    Each ``EntityTypeConfig`` contributes a label for the type itself plus,
    when subtypes are enabled and declared, one label per subtype. The base
    label is what relationship endpoints refer to, which keeps the round trip
    through :func:`to_entity_schema` exact.

    A subtype name that collides with another declared label is qualified as
    ``TYPE_SUBTYPE`` by :func:`_subtype_label`; its ``pole_type``/``subtype``
    are untouched, so the Neo4j labels the storage path derives from those
    two properties do not change.

    ``RelationTypeConfig`` is expanded over ``source_types x target_types``;
    an empty list on either side means "every declared base type".

    An endpoint naming a type the schema does not declare is **dropped**, not
    emitted. Every ``EntitySchemaConfig`` carries the full sixteen-strong
    POLE+O relation catalogue by default, so a schema that declares a subset
    of the five types (``PERSON`` and ``ORGANIZATION``, say) would otherwise
    convert into a document whose relationships point at undeclared labels —
    which :meth:`OntologyDocument.validate_structure` rejects, taking
    ``compile_joint_schema`` and ``BoltOntology.create`` down with it.

    Args:
        schema: The legacy schema configuration.

    Returns:
        An equivalent :class:`OntologyDocument`, sound by construction.
    """
    entity_types: list[EntityTypeDef] = []
    # Type name (upper) -> the labels that stand in for it in relationships.
    endpoint_labels: dict[str, str] = {}
    # Every label spoken for, lower-cased. Seeded with the base type names so
    # a subtype shadowing a type declared *later* is qualified too.
    declared: set[str] = {et.name.lower() for et in schema.entity_types}

    for et in schema.entity_types:
        pole_type = et.name.upper()
        endpoint_labels[pole_type] = et.name
        entity_types.append(
            EntityTypeDef(
                label=et.name,
                pole_type=pole_type,
                description=et.description,
                color=et.color,
                properties=[PropertyDef(name=attr, type="string") for attr in et.attributes],
            )
        )
        if not (schema.enable_subtypes and et.subtypes):
            continue
        for subtype in et.subtypes:
            label = _subtype_label(et.name, subtype, declared)
            declared.add(label.lower())
            entity_types.append(
                EntityTypeDef(
                    label=label,
                    pole_type=pole_type,
                    subtype=subtype.upper(),
                    description=et.description,
                    color=et.color,
                )
            )

    # Every label the document declares, keyed by its upper-cased form: a
    # relation endpoint may name a base type ("PERSON") or a subtype label
    # ("VEHICLE"), and both are declared entity types by now. Built from the
    # emitted labels, so an endpoint resolves to the *qualified* spelling when
    # ``_subtype_label`` had to rename a colliding subtype.
    labels_by_key: dict[str, str] = {et.label.upper(): et.label for et in entity_types}
    # A wildcard endpoint ("every declared type") expands over the base labels
    # only — expanding it over subtype labels too would multiply one catch-all
    # relation into hundreds of patterns.
    base_labels = list(endpoint_labels)

    relationships: list[RelationshipDef] = []
    for rt in schema.relation_types:
        sources = [s.upper() for s in rt.source_types] or base_labels
        targets = [t.upper() for t in rt.target_types] or base_labels
        for source in sources:
            source_label = labels_by_key.get(source)
            if source_label is None:
                continue  # endpoint names a type this schema does not declare
            for target in targets:
                target_label = labels_by_key.get(target)
                if target_label is None:
                    continue
                relationships.append(
                    RelationshipDef(
                        type=rt.name,
                        source=source_label,
                        target=target_label,
                        description=rt.description,
                    )
                )

    return OntologyDocument(
        domain=DomainInfo(
            id=schema.name,
            name=schema.name,
            description=schema.description,
        ),
        entity_types=entity_types,
        relationships=relationships,
    )


def to_entity_schema(doc: OntologyDocument) -> EntitySchemaConfig:
    """Project an ontology document back onto a legacy :class:`EntitySchemaConfig`.

    Labels collapse onto their ``pole_type``: each POLE+O type becomes one
    ``EntityTypeConfig`` whose ``subtypes`` are the fine-grained labels under
    it. Relationship endpoints likewise collapse onto pole types, so
    ``graph/query_builder.py`` validation and the CLI ``schemas`` commands
    keep working against an ontology-driven deployment.

    Args:
        doc: The ontology document.

    Returns:
        The equivalent legacy schema configuration.
    """
    from neo4j_agent_memory.schema.models import (
        EntitySchemaConfig,
        EntityTypeConfig,
        RelationTypeConfig,
    )

    grouped: dict[str, list[EntityTypeDef]] = {}
    for et in doc.entity_types:
        grouped.setdefault(et.pole_type.upper(), []).append(et)

    entity_types: list[EntityTypeConfig] = []
    for pole_type, defs in grouped.items():
        subtypes: list[str] = []
        for et in defs:
            subtype = et.subtype.upper() if et.subtype else None
            if subtype is None and et.label.upper() != pole_type:
                subtype = et.label.upper()
            if subtype and subtype not in subtypes:
                subtypes.append(subtype)
        description = next((et.description for et in defs if et.description), None)
        color = next((et.color for et in defs if et.color), None)
        attributes: list[str] = []
        for et in defs:
            for prop in et.properties:
                if prop.name not in attributes:
                    attributes.append(prop.name)
        entity_types.append(
            EntityTypeConfig(
                name=pole_type,
                description=description,
                subtypes=subtypes,
                attributes=attributes,
                color=color,
            )
        )

    def _pole_type_of(label: str) -> str:
        et = doc.entity_type(label)
        return et.pole_type.upper() if et is not None else label.upper()

    relation_types: list[RelationTypeConfig] = []
    for rel_type in doc.relationship_types():
        rel_defs = [rel for rel in doc.relationships if rel.type == rel_type]
        source_types: list[str] = []
        target_types: list[str] = []
        for rel in rel_defs:
            source = _pole_type_of(rel.source)
            target = _pole_type_of(rel.target)
            if source not in source_types:
                source_types.append(source)
            if target not in target_types:
                target_types.append(target)
        relation_types.append(
            RelationTypeConfig(
                name=rel_type,
                description=next((rel.description for rel in rel_defs if rel.description), None),
                source_types=source_types,
                target_types=target_types,
            )
        )

    return EntitySchemaConfig(
        name=doc.domain.name or doc.domain.id,
        description=doc.domain.description,
        entity_types=entity_types,
        relation_types=relation_types,
    )


# -----------------------------------------------------------------------------
# DomainSchema -> OntologyDocument
# -----------------------------------------------------------------------------


def from_domain_schema(
    schema: _DomainSchemaLike,
    *,
    relationships: list[RelationshipDef] | None = None,
) -> OntologyDocument:
    """Convert a GLiNER domain-catalog schema into an ontology document.

    The catalog carries labels with descriptions but no endpoint typing, so
    relationships are supplied by the caller. Any ``relationships`` entry
    without a description picks one up from the schema's ``relation_types``
    when it names the same type.

    Args:
        schema: Anything with ``name``, ``entity_types`` and ``relation_types``
            (the catalog's ``DomainSchema``); duck-typed so this module stays
            free of extractor imports.
        relationships: Typed relationships to attach, with source/target
            naming labels declared by ``schema.entity_types``.

    Returns:
        An :class:`OntologyDocument` with one entity type per catalog label.
    """
    from neo4j_agent_memory.extraction.label_mapping import map_label_to_poleo

    entity_types: list[EntityTypeDef] = []
    for label, description in schema.entity_types.items():
        pole_type, subtype = map_label_to_poleo(label)
        entity_types.append(
            EntityTypeDef(
                label=label,
                pole_type=pole_type,
                subtype=subtype,
                description=description,
            )
        )

    relation_descriptions = dict(getattr(schema, "relation_types", {}) or {})
    resolved: list[RelationshipDef] = []
    for rel in relationships or []:
        if rel.description is None and rel.type in relation_descriptions:
            rel = rel.model_copy(update={"description": relation_descriptions[rel.type]})
        resolved.append(rel)

    return OntologyDocument(
        domain=DomainInfo(id=schema.name, name=schema.name),
        entity_types=entity_types,
        relationships=resolved,
    )


# -----------------------------------------------------------------------------
# arrows.app -> OntologyDocument
# -----------------------------------------------------------------------------


def from_arrows(content: str | dict[str, Any]) -> tuple[OntologyDocument, list[ImportWarning]]:
    """Convert an `arrows.app <https://arrows.app>`_ JSON export.

    Node labels become entity types (mapped onto POLE+O), node properties
    become typed properties, and relationships become typed relationships.
    Conversions that had to guess are reported rather than raised.

    Args:
        content: The export, as a JSON string or an already-parsed mapping.

    Returns:
        ``(document, warnings)`` — the draft ontology and any non-fatal
        conversion warnings.

    Raises:
        ValueError: If ``content`` is not valid arrows JSON.
    """
    if isinstance(content, str):
        try:
            data: Any = json.loads(content)
        except ValueError as exc:
            raise ValueError(f"Not valid arrows.app JSON: {exc}") from exc
    else:
        data = content
    if not isinstance(data, dict):
        raise ValueError("Not valid arrows.app JSON: expected a top-level object.")

    from neo4j_agent_memory.extraction.label_mapping import (
        DEFAULT_LABEL_MAPPING,
        POLEO_TYPE_NAMES,
        map_label_to_poleo,
    )

    warnings: list[ImportWarning] = []
    entity_types: list[EntityTypeDef] = []
    label_by_node_id: dict[str, str] = {}
    seen_labels: set[str] = set()

    nodes = data.get("nodes") or []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", f"n{index}"))
        labels = [str(label) for label in (node.get("labels") or []) if label]
        label = labels[0] if labels else str(node.get("caption") or "").strip()
        if not label:
            warnings.append(
                ImportWarning(
                    code="node_without_label",
                    message="Node has neither labels nor a caption; skipped.",
                    path=f"nodes[{index}]",
                )
            )
            continue
        label_by_node_id[node_id] = label
        if len(labels) > 1:
            warnings.append(
                ImportWarning(
                    code="multi_label_node",
                    message=(
                        f"Node {node_id} carries {len(labels)} labels; "
                        f"only {label!r} became an entity type."
                    ),
                    path=f"nodes[{index}].labels",
                )
            )
        if label.lower() in seen_labels:
            continue
        seen_labels.add(label.lower())

        pole_type, subtype = map_label_to_poleo(label)
        if label.lower() not in DEFAULT_LABEL_MAPPING and label.upper() not in POLEO_TYPE_NAMES:
            warnings.append(
                ImportWarning(
                    code="unmapped_label",
                    message=(
                        f"Label {label!r} has no POLE+O mapping; it fell back to "
                        f"OBJECT:{subtype}. Set pole_type explicitly if that is wrong."
                    ),
                    path=f"nodes[{index}].labels",
                )
            )

        properties = [
            PropertyDef(name=str(name), type=_arrows_property_type(value))
            for name, value in (node.get("properties") or {}).items()
        ]
        entity_types.append(
            EntityTypeDef(
                label=label,
                pole_type=pole_type,
                subtype=subtype,
                properties=properties,
            )
        )

    relationships: list[RelationshipDef] = []
    seen_triples: set[tuple[str, str, str]] = set()
    for index, rel in enumerate(data.get("relationships") or []):
        if not isinstance(rel, dict):
            continue
        rel_type = str(rel.get("type") or "").strip()
        source = label_by_node_id.get(str(rel.get("fromId")))
        target = label_by_node_id.get(str(rel.get("toId")))
        if not rel_type or source is None or target is None:
            warnings.append(
                ImportWarning(
                    code="dangling_relationship",
                    message=(
                        "Relationship has no type or an endpoint that is not a "
                        "converted node; skipped."
                    ),
                    path=f"relationships[{index}]",
                )
            )
            continue
        triple = (rel_type, source, target)
        if triple in seen_triples:
            continue
        seen_triples.add(triple)
        relationships.append(RelationshipDef(type=rel_type, source=source, target=target))

    name = str(data.get("name") or data.get("title") or "imported")
    document = OntologyDocument(
        domain=DomainInfo(id=name, name=name, description=data.get("_comment")),
        entity_types=entity_types,
        relationships=relationships,
    )
    return document, warnings


def _arrows_property_type(value: Any) -> str:
    """Arrows stores a property's *type name* as its value; normalise it."""
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "string"


# -----------------------------------------------------------------------------
# Files
# -----------------------------------------------------------------------------


def load_ontology(path: str | Path) -> OntologyDocument:
    """Load an ontology from a ``.json``, ``.yaml`` or ``.yml`` file.

    A file shaped like an :class:`EntitySchemaConfig` (its ``entity_types``
    entries carry ``name`` rather than ``label``) is converted on the way in,
    so ``schema_config.custom_schema_path`` and ``ontology_path`` can point at
    either shape.

    Args:
        path: Path to the ontology or legacy schema file.

    Returns:
        The parsed :class:`OntologyDocument`.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the suffix is unsupported, PyYAML is missing for a YAML
            file, or the contents are not a mapping.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Ontology file not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        with open(path) as f:
            data = json.load(f)
    elif suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise ValueError(
                "PyYAML is required to load YAML ontology files. Install with: pip install pyyaml"
            ) from exc
        with open(path) as f:
            data = yaml.safe_load(f)
    else:
        raise ValueError(f"Unsupported ontology file format: {suffix}. Use .json, .yaml or .yml")

    if not isinstance(data, dict):
        raise ValueError(f"Ontology file {path} does not contain a mapping.")

    if is_entity_schema_shape(data):
        from neo4j_agent_memory.schema.models import EntitySchemaConfig

        return from_entity_schema(EntitySchemaConfig(**data))

    return OntologyDocument.model_validate(data)


def is_entity_schema_shape(data: dict[str, Any]) -> bool:
    """Whether a parsed mapping looks like a legacy ``EntitySchemaConfig``.

    The tell is the entity type key: ontology documents use ``label`` (plus a
    top-level ``domain``), legacy schemas use ``name``.
    """
    if "domain" in data:
        return False
    entity_types = data.get("entity_types")
    if not isinstance(entity_types, list) or not entity_types:
        # No entity types to look at: treat "has relation_types" as legacy.
        return "relation_types" in data
    first = entity_types[0]
    return isinstance(first, dict) and "label" not in first and "name" in first


__all__ = [
    "from_arrows",
    "from_domain_schema",
    "from_entity_schema",
    "is_entity_schema_shape",
    "load_ontology",
    "to_entity_schema",
]
