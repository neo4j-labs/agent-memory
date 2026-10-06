"""NAMS ontology surface — typed domain schemas extending POLE+O.

An *ontology* is a versioned, validated, typed schema for the knowledge
graph: entity types (each mapped onto a POLE+O ``pole_type``) with typed
properties (``required`` / ``unique`` / ``enum`` constraints) and typed
relationships. NAMS ships ~28 system templates and supports
workspace-owned ontologies with immutable revisions, activation, and a
per-version ``validation_mode`` (``permissive`` or ``strict``). Enforcement
depends on the service operation; activation alone does not verify a write
constraint.

This module exposes :class:`NamsOntology` — the ``client.ontology``
accessor — plus the Pydantic models that hide the wire shape (notably the
double-encoded ``schema_json`` string the service returns, which we parse
into :class:`OntologyDocument`).

Contract note
=============

The routes below use the service’s snake_case ontology contract. The
active response carries its bound version, which can be older than the
latest revision:

* ``GET    /ontologies``                          → list (summaries)
* ``GET    /ontologies/{id}``                      → ``{record, versions[]}``
* ``GET    /ontologies/active``                    → ``{ontology, version}``
* ``POST   /ontologies/{name}/clone``              → version (template name in path)
* ``POST   /ontologies``  body ``{ontology, validation_mode?}`` → version
* ``PUT    /ontologies/{id}``  body ``{ontology, validation_mode?}`` → new revision
* ``POST   /ontologies/active``  body ``{version_id}``          → version
* ``DELETE /ontologies/{id}``                       → 204
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from neo4j_agent_memory.core.exceptions import NotSupportedError
from neo4j_agent_memory.nams.endpoints import EndpointSpec

# The document model moved to ``neo4j_agent_memory.ontology.models`` in 0.7 so
# both backends share one shape. Everything is re-exported here unchanged —
# ``from neo4j_agent_memory.nams.ontology import OntologyDocument`` still works,
# and the wire shapes and class names are identical.
from neo4j_agent_memory.ontology.models import (
    ActiveOntology,
    DomainInfo,
    EntityTypeDef,
    ImportWarning,
    MigrationJob,
    Ontology,
    OntologyDiff,
    OntologyDocument,
    OntologyImportResult,
    OntologyRecord,
    OntologySummary,
    OntologyVersion,
    PropertyDef,
    RelationshipDef,
    _as_document_dict,
    _parse_document_strict,
    _parse_version,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.nams.transport import HttpTransport


def _parse_active_version(raw: Any) -> OntologyVersion:
    """Validate a supplied binding without changing other version parsers."""
    if not isinstance(raw, dict):
        raise ValueError("Invalid active ontology version metadata: expected an object.")
    for key in ("id", "ontology_id"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"Invalid active ontology version metadata: {key} is required.")
    revision = raw.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError(
            "Invalid active ontology version metadata: revision must be a positive integer."
        )
    if raw.get("validation_mode") not in ("permissive", "strict"):
        raise ValueError("Invalid active ontology version metadata: unknown validation_mode.")
    if raw.get("schema_json") is not None and not isinstance(raw["schema_json"], str):
        raise ValueError("Invalid active ontology version metadata: schema_json must be a string.")
    version = _parse_version(raw)
    if raw.get("schema_json") is not None and version.document is None:
        raise ValueError("Invalid active ontology version metadata: schema_json is not a document.")
    return version


# -----------------------------------------------------------------------------
# Endpoint specs (REST-only — ontology is a hosted-NAMS capability).
# -----------------------------------------------------------------------------

_SPEC_LIST = EndpointSpec(
    rest_method="GET", rest_path="/ontologies", bridge_method="list_ontologies"
)
_SPEC_GET = EndpointSpec(
    rest_method="GET", rest_path="/ontologies/{id}", bridge_method="get_ontology"
)
_SPEC_GET_ACTIVE = EndpointSpec(
    rest_method="GET", rest_path="/ontologies/active", bridge_method="get_active_ontology"
)
_SPEC_CLONE = EndpointSpec(
    rest_method="POST", rest_path="/ontologies/{name}/clone", bridge_method="clone_ontology"
)
_SPEC_CREATE = EndpointSpec(
    rest_method="POST", rest_path="/ontologies", bridge_method="create_ontology"
)
_SPEC_UPDATE = EndpointSpec(
    rest_method="PUT", rest_path="/ontologies/{id}", bridge_method="update_ontology"
)
_SPEC_ACTIVATE = EndpointSpec(
    rest_method="POST", rest_path="/ontologies/active", bridge_method="activate_ontology"
)
_SPEC_DELETE = EndpointSpec(
    rest_method="DELETE", rest_path="/ontologies/{id}", bridge_method="delete_ontology"
)
_SPEC_IMPORT = EndpointSpec(
    rest_method="POST", rest_path="/ontologies/import", bridge_method="import_ontology"
)
_SPEC_DIFF = EndpointSpec(
    rest_method="GET", rest_path="/ontologies/{id}/diff", bridge_method="diff_ontology"
)
_SPEC_MIGRATE = EndpointSpec(
    rest_method="POST", rest_path="/ontologies/{id}/migrate", bridge_method="migrate_ontology"
)
_SPEC_MIGRATION_STATUS = EndpointSpec(
    rest_method="GET",
    rest_path="/ontologies/migrations/{job_id}",
    bridge_method="get_ontology_migration",
)


# -----------------------------------------------------------------------------
# Accessor
# -----------------------------------------------------------------------------

# ``NamsOntology`` defines a method named ``list`` which shadows the builtin
# ``list`` inside class-scope annotation resolution for some type checkers.
# We expose the builtin under an alias so the affected annotations can refer
# to it unambiguously.
_List = list


class NamsOntology:
    """``client.ontology`` — the NAMS ontology lifecycle.

    Lifecycle::

        v = await client.ontology.clone("healthcare")   # editable copy, rev 1
        # v = await client.ontology.update(v.ontology_id, schema=doc)  # -> rev 2
        await client.ontology.activate(v.id)             # bind the version
        active = await client.ontology.get_active()      # parsed body + mode

    Activation selects the workspace schema and validation mode. The service
    applies validation according to the write path; reading the active binding
    does not prove that a particular entity constraint was enforced.
    """

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def _guard_rest(self) -> None:
        if self._transport.protocol != "rest":
            raise NotSupportedError(
                backend="nams",
                method="ontology",
                message="The ontology surface requires the REST transport (a /vN "
                "endpoint); it is not available over the TCK bridge.",
            )

    async def list(self) -> _List[OntologySummary]:
        """List system templates + workspace-owned ontologies (active flagged)."""
        self._guard_rest()
        payload = await self._transport.request(_SPEC_LIST)
        items = payload.get("ontologies", []) if isinstance(payload, dict) else (payload or [])
        return [OntologySummary.model_validate(i) for i in items if isinstance(i, dict)]

    async def get(self, ontology_id: str) -> Ontology:
        """Return one ontology with its full revision history."""
        self._guard_rest()
        payload = await self._transport.request(_SPEC_GET, path_params={"id": ontology_id})
        payload = payload or {}
        record = payload.get("record") or {}
        versions = [
            _parse_version(v) for v in (payload.get("versions") or []) if isinstance(v, dict)
        ]
        return Ontology(record=OntologyRecord.model_validate(record), versions=versions)

    async def get_active(self) -> ActiveOntology:
        """Return the bound document and the response's exact version metadata.

        Missing/null legacy version records leave the binding unknown. Supplied
        malformed metadata, a malformed active document or conflicting schema
        documents raise ``ValueError``; only an absent document means nothing
        is bound.
        """
        self._guard_rest()
        payload = await self._transport.request(_SPEC_GET_ACTIVE)
        payload = payload or {}
        body = payload.get("ontology") if "ontology" in payload else payload
        document = _parse_document_strict(body)
        if document is None:
            raise NotSupportedError(
                backend="nams",
                method="ontology.get_active",
                message="No active ontology bound for this workspace.",
            )

        raw_version = payload.get("version")
        if raw_version is None:
            return ActiveOntology(document=document)
        version = _parse_active_version(raw_version)
        if version.document is not None and version.document != document:
            raise ValueError("Active ontology version schema conflicts with the active document.")
        return ActiveOntology(
            document=document,
            validation_mode=version.validation_mode,
            revision=version.revision,
            ontology_id=version.ontology_id,
            version_id=version.id,
            schema_hash=version.schema_hash,
        )

    async def clone(self, template_name: str) -> OntologyVersion:
        """Clone a system template into an editable workspace copy (revision 1)."""
        self._guard_rest()
        payload = await self._transport.request(_SPEC_CLONE, path_params={"name": template_name})
        return _parse_version(payload or {})

    async def create(
        self,
        name: str,  # noqa: ARG002 — identity comes from schema.domain; kept for ergonomics/docs
        schema: OntologyDocument | dict[str, Any],
        *,
        validation_mode: str | None = None,
    ) -> OntologyVersion:
        """Create a new workspace ontology from a schema body (revision 1).

        ``name`` is accepted for API symmetry but the ontology's identity
        comes from ``schema.domain`` (NAMS reads ``domain.id`` / ``domain.name``).
        """
        self._guard_rest()
        doc = _as_document_dict(schema)
        body: dict[str, Any] = {"ontology": doc}
        if validation_mode is not None:
            body["validation_mode"] = validation_mode
        payload = await self._transport.request(_SPEC_CREATE, json=body)
        return _parse_version(payload or {})

    async def update(
        self,
        ontology_id: str,
        schema: OntologyDocument | dict[str, Any],
        *,
        validation_mode: str | None = None,
    ) -> OntologyVersion:
        """Create a new immutable revision (n+1) from an updated schema body."""
        self._guard_rest()
        body: dict[str, Any] = {"ontology": _as_document_dict(schema)}
        if validation_mode is not None:
            body["validation_mode"] = validation_mode
        payload = await self._transport.request(
            _SPEC_UPDATE, path_params={"id": ontology_id}, json=body
        )
        return _parse_version(payload or {})

    async def activate(self, version_id: str) -> OntologyVersion:
        """Bind this schema version and its validation mode to the workspace."""
        self._guard_rest()
        payload = await self._transport.request(_SPEC_ACTIVATE, json={"version_id": version_id})
        return _parse_version(payload or {})

    async def delete(self, ontology_id: str) -> None:
        """Delete a workspace-owned ontology."""
        self._guard_rest()
        await self._transport.request(_SPEC_DELETE, path_params={"id": ontology_id})

    async def import_(
        self,
        *,
        content: str | None = None,
        url: str | None = None,
        format: str | None = None,  # noqa: A002 — mirrors the wire field name
    ) -> OntologyImportResult:
        """Convert an external graph/ontology document into a native draft.

        Supply exactly one source:

        * ``content`` — the raw document inline (Arrows / Neo4j Data Importer
          / RDF / GraphQL / Cypher / LinkML / native JSON or YAML).
        * ``url`` — fetched server-side (https only, SSRF-guarded, size-capped).

        ``format`` is an explicit converter id (e.g. ``"arrows"``, ``"rdf"``)
        or ``None``/``"auto"`` to detect from the content. The result is a
        **non-persisted** draft plus conversion warnings; persist it with
        :meth:`create` (pass ``result.document``). Extraction-backed formats
        (e.g. ``rdf``) and URL fetches are rate-limited per workspace.
        """
        self._guard_rest()
        if not content and not url:
            raise ValueError("import_ requires either content= or url=.")
        body: dict[str, Any] = {}
        if format is not None:
            body["format"] = format
        if content is not None:
            body["content"] = content
        if url is not None:
            body["url"] = url
        payload = await self._transport.request(_SPEC_IMPORT, json=body)
        payload = payload or {}
        document = _parse_document_strict(payload.get("ontology"))
        return OntologyImportResult(
            document=document,
            warnings=[
                ImportWarning.model_validate(w)
                for w in (payload.get("warnings") or [])
                if isinstance(w, dict)
            ],
            detected_format=payload.get("detected_format"),
            suggested_name=payload.get("suggested_name"),
        )

    async def diff(self, ontology_id: str, from_revision: int, to_revision: int) -> OntologyDiff:
        """Diff two revisions of an ontology (added / removed / renamed / modified)."""
        self._guard_rest()
        payload = await self._transport.request(
            _SPEC_DIFF,
            path_params={"id": ontology_id},
            params={"from": from_revision, "to": to_revision},
        )
        return OntologyDiff.model_validate(payload or {})

    async def migrate(
        self,
        ontology_id: str,
        *,
        from_version_id: str,
        to_version_id: str,
        type_mappings: _List[tuple[str, str]] | _List[dict[str, str]],
        dry_run: bool = False,
        batch_size: int | None = None,
    ) -> MigrationJob:
        """Enqueue an async label-rename migration; returns the job (poll it).

        ``type_mappings`` are ``(from_label, to_label)`` pairs (or
        ``{"from": ..., "to": ...}`` dicts). With ``dry_run=True`` the job
        reports counts without mutating the graph. Track progress with
        :meth:`get_migration`.
        """
        self._guard_rest()
        mappings = [m if isinstance(m, dict) else {"from": m[0], "to": m[1]} for m in type_mappings]
        spec: dict[str, Any] = {
            "from_version_id": from_version_id,
            "to_version_id": to_version_id,
            "type_mappings": mappings,
            "dry_run": dry_run,
        }
        if batch_size is not None:
            spec["batch_size"] = batch_size
        payload = await self._transport.request(
            _SPEC_MIGRATE, path_params={"id": ontology_id}, json={"spec": spec}
        )
        return MigrationJob.model_validate(payload or {})

    async def get_migration(self, job_id: str) -> MigrationJob:
        """Return one migration job's current status."""
        self._guard_rest()
        payload = await self._transport.request(
            _SPEC_MIGRATION_STATUS, path_params={"job_id": job_id}
        )
        return MigrationJob.model_validate(payload or {})


__all__ = [
    "ActiveOntology",
    "DomainInfo",
    "EntityTypeDef",
    "ImportWarning",
    "MigrationJob",
    "NamsOntology",
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
