"""Query builder utilities for dynamic Cypher generation with validated labels.

This module provides functions to build Cypher queries with dynamic entity labels.
It supports both the built-in POLE+O model (Person, Object, Location, Event, Organization)
and custom entity types defined by users.

Since Neo4j doesn't support parameterized labels in Cypher, we use query string
construction with sanitization to prevent Cypher injection while allowing flexibility.

Labels are converted to PascalCase following Neo4j naming conventions.
"""

import re

# Valid POLE+O entity types (stored uppercase internally, converted to PascalCase for labels)
VALID_ENTITY_TYPES: set[str] = {"PERSON", "OBJECT", "LOCATION", "EVENT", "ORGANIZATION"}

# Valid subtypes by entity type (from schema/models.py)
# Stored uppercase internally, converted to PascalCase for labels
VALID_SUBTYPES: dict[str, set[str]] = {
    "PERSON": {"INDIVIDUAL", "ALIAS", "PERSONA", "SUSPECT", "WITNESS", "VICTIM"},
    "OBJECT": {
        "VEHICLE",
        "PHONE",
        "EMAIL",
        "DOCUMENT",
        "DEVICE",
        "WEAPON",
        "MONEY",
        "DRUG",
        "EVIDENCE",
        "SOFTWARE",
        "PRODUCT",
    },
    "LOCATION": {
        "ADDRESS",
        "CITY",
        "REGION",
        "COUNTRY",
        "LANDMARK",
        "FACILITY",
        "COORDINATES",
        "GEOPOLITICAL",
        "GEOGRAPHIC",
    },
    "EVENT": {
        "INCIDENT",
        "MEETING",
        "TRANSACTION",
        "COMMUNICATION",
        "CRIME",
        "TRAVEL",
        "EMPLOYMENT",
        "OBSERVATION",
        "DATE",
        "TIME",
    },
    "ORGANIZATION": {
        "COMPANY",
        "NONPROFIT",
        "GOVERNMENT",
        "EDUCATIONAL",
        "CRIMINAL",
        "POLITICAL",
        "RELIGIOUS",
        "MILITARY",
        "GROUP",
    },
}

# Pattern for valid Neo4j labels: must start with letter, can contain letters, numbers, underscores
# This prevents Cypher injection while allowing flexible custom types
VALID_LABEL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def to_pascal_case(s: str) -> str:
    """Convert a string to PascalCase for Neo4j label naming convention.

    Handles various input formats:
    - UPPERCASE -> Uppercase (e.g., PERSON -> Person)
    - snake_case -> SnakeCase (e.g., my_type -> MyType)
    - already PascalCase -> unchanged
    - mixedCase -> MixedCase

    Args:
        s: The string to convert

    Returns:
        PascalCase version of the string
    """
    if not s:
        return s

    # Split on underscores and capitalize each part
    parts = s.split("_")
    result_parts = []

    for part in parts:
        if not part:
            continue
        # Capitalize first letter, lowercase the rest
        result_parts.append(part[0].upper() + part[1:].lower())

    return "".join(result_parts)


def sanitize_label(label: str) -> str | None:
    """Sanitize and normalize a string for use as a Neo4j label.

    Labels must start with a letter and contain only letters, numbers, and underscores.
    This prevents Cypher injection while allowing custom entity types.
    Labels are converted to PascalCase following Neo4j naming conventions.

    Args:
        label: The label string to sanitize

    Returns:
        PascalCase label if valid, None if invalid
    """
    if not label or not isinstance(label, str):
        return None

    # Strip whitespace
    stripped = label.strip()

    # Validate against pattern (before case conversion)
    if not VALID_LABEL_PATTERN.match(stripped):
        return None

    # Convert to PascalCase for Neo4j convention
    return to_pascal_case(stripped)


def is_poleo_type(entity_type: str) -> bool:
    """Check if an entity type is a valid POLE+O type.

    Args:
        entity_type: The entity type to check

    Returns:
        True if the type is a valid POLE+O type, False otherwise
    """
    return entity_type.upper() in VALID_ENTITY_TYPES


def is_poleo_subtype(entity_type: str, subtype: str) -> bool:
    """Check if a subtype is valid for a given POLE+O entity type.

    Args:
        entity_type: The parent entity type
        subtype: The subtype to check

    Returns:
        True if the subtype is valid for the entity type, False otherwise
    """
    type_upper = entity_type.upper()
    subtype_upper = subtype.upper()
    valid_subtypes = VALID_SUBTYPES.get(type_upper, set())
    return subtype_upper in valid_subtypes


def validate_entity_type(entity_type: str) -> str | None:
    """Validate and normalize entity type for use as a label.

    Accepts both POLE+O types and custom types. Custom types must be valid
    Neo4j label identifiers (start with letter, alphanumeric + underscore).

    Args:
        entity_type: The entity type to validate

    Returns:
        Normalized (uppercase) entity type if valid, None if invalid
    """
    return sanitize_label(entity_type)


def validate_subtype(entity_type: str, subtype: str) -> str | None:
    """Validate and normalize subtype for use as a label.

    For POLE+O types, validates against known subtypes.
    For custom types, accepts any valid Neo4j label identifier.
    Returns PascalCase label.

    Args:
        entity_type: The parent entity type
        subtype: The subtype to validate

    Returns:
        PascalCase subtype if valid, None if invalid
    """
    type_upper = entity_type.upper() if entity_type else ""
    subtype_upper = subtype.upper() if subtype else ""

    # For POLE+O types, validate against known subtypes
    if is_poleo_type(type_upper):
        valid_subtypes = VALID_SUBTYPES.get(type_upper, set())
        if subtype_upper in valid_subtypes:
            # Convert to PascalCase for label
            return to_pascal_case(subtype)
        # For POLE+O types, only allow known subtypes
        return None

    # For custom types, allow any valid label identifier as subtype
    return sanitize_label(subtype)


def build_label_set_clause(entity_type: str, subtype: str | None, node_var: str = "e") -> str:
    """Build SET clause to add type/subtype labels to a node.

    Args:
        entity_type: The entity type (e.g., "PERSON", "OBJECT")
        subtype: Optional subtype (e.g., "VEHICLE", "ADDRESS")
        node_var: The Cypher node variable name (default: "e")

    Returns:
        SET clause string (e.g., "SET e:PERSON, e:INDIVIDUAL") or empty string if no valid labels
    """
    labels_to_add = []

    validated_type = validate_entity_type(entity_type)
    if validated_type:
        labels_to_add.append(validated_type)

    if subtype and validated_type:
        validated_subtype = validate_subtype(entity_type, subtype)
        if validated_subtype:
            labels_to_add.append(validated_subtype)

    if not labels_to_add:
        return ""

    # Build: SET e:PERSON, e:INDIVIDUAL
    label_additions = ", ".join([f"{node_var}:{label}" for label in labels_to_add])
    return f"SET {label_additions}"


def build_create_entity_query(
    entity_type: str,
    subtype: str | None,
    *,
    include_location: bool = False,
    include_aliases: bool = False,
) -> str:
    """Build the CREATE_ENTITY query with dynamic type/subtype labels.

    The query MERGEs on :Entity with name+type properties for uniqueness,
    then adds type and subtype as additional labels.

    Args:
        entity_type: The entity type (e.g., "PERSON", "OBJECT")
        subtype: Optional subtype (e.g., "VEHICLE", "ADDRESS")
        include_location: If True, include location Point property (for LOCATION entities)
        include_aliases: If True, include the top-level ``aliases`` list property.
            Callers that set this must pass an ``aliases`` parameter when
            running the query — Cypher errors on a referenced-but-absent
            parameter. Off by default so that queries built by hand with their
            own parameter dict keep working unchanged; ``LongTermMemory.add_entity``
            opts in.

    Returns:
        Complete Cypher query string with dynamic labels

    Example:
        >>> query = build_create_entity_query("OBJECT", "VEHICLE")
        >>> # Returns query that creates (:Entity:OBJECT:VEHICLE {...})

        >>> query = build_create_entity_query("LOCATION", "CITY", include_location=True)
        >>> # Returns query that also sets e.location = $location

        >>> query = build_create_entity_query("PERSON", None, include_aliases=True)
        >>> # Returns query that also sets e.aliases = $aliases
    """
    label_set_clause = build_label_set_clause(entity_type, subtype)

    # Build location clause for LOCATION entities
    location_on_create = ""
    location_on_match = ""
    if include_location or entity_type.upper() == "LOCATION":
        # Use CASE to only set location if $location is not null
        location_on_create = ",\n    e.location = CASE WHEN $location IS NOT NULL THEN point({latitude: $location.latitude, longitude: $location.longitude}) ELSE null END"
        location_on_match = ",\n    e.location = CASE WHEN $location IS NOT NULL THEN point({latitude: $location.latitude, longitude: $location.longitude}) ELSE e.location END"

    # Build aliases clause. ``aliases`` is a top-level list property (not
    # buried in the JSON ``metadata`` string) so that name lookups —
    # ``GET_ENTITY_BY_NAME`` reads ``$name IN e.aliases`` — and
    # ``MERGE_ENTITIES``, which appends the merged-away name to
    # ``target.aliases``, all agree on one location. ON MATCH appends only
    # aliases not already present, atomically, so concurrent writers cannot
    # clobber each other's list.
    aliases_on_create = ""
    aliases_on_match = ""
    if include_aliases:
        aliases_on_create = ",\n    e.aliases = coalesce($aliases, [])"
        aliases_on_match = (
            ",\n    e.aliases = coalesce(e.aliases, [])"
            " + [a IN coalesce($aliases, []) WHERE NOT a IN coalesce(e.aliases, [])]"
        )

    query = f"""MERGE (e:Entity {{name: $name, type: $type}})
ON CREATE SET
    e.id = $id,
    e.subtype = $subtype,
    e.canonical_name = $canonical_name,
    e.description = $description,
    e.embedding = $embedding,
    e.confidence = $confidence,
    e.created_at = datetime(),
    e.metadata = $metadata{aliases_on_create}{location_on_create}
ON MATCH SET
    e.subtype = COALESCE($subtype, e.subtype),
    e.canonical_name = COALESCE($canonical_name, e.canonical_name),
    e.description = COALESCE($description, e.description),
    e.embedding = COALESCE($embedding, e.embedding),
    e.updated_at = datetime(){aliases_on_match}{location_on_match}"""

    # Add label SET clause if we have valid labels
    if label_set_clause:
        query += f"\n{label_set_clause}"

    query += "\nRETURN e"

    return query
