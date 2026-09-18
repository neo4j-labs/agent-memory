"""Bolt-backed ontology store — ``client.ontology`` on your own Neo4j.

:class:`BoltOntology` implements the same
:class:`~neo4j_agent_memory.ontology.protocol.OntologyAPI` surface the hosted
backend exposes through
:class:`~neo4j_agent_memory.nams.ontology.NamsOntology`, so code that manages
ontologies reads the same on both backends. Instead of REST calls it writes
two node types::

    (:Ontology {id, name, description, is_system, created_at})
      -[:HAS_VERSION]->
    (:OntologyVersion {id, ontology_id, revision, validation_mode,
                       document, schema_hash, is_active, created_at, message})

``document`` is the JSON-serialised
:class:`~neo4j_agent_memory.ontology.models.OntologyDocument` (Neo4j has no
map property type — the same approach ``:Schema.config`` already uses), and
``schema_hash`` is the SHA-256 of
:meth:`~neo4j_agent_memory.ontology.models.OntologyDocument.content_key`, so
two revisions that describe the same schema hash identically.

Lifecycle::

    v = await client.ontology.clone("podcast")        # editable copy, rev 1
    v = await client.ontology.update(v.ontology_id, doc)   # -> rev 2
    await client.ontology.activate(v.id)              # exactly one active
    active = await client.ontology.get_active()       # document + mode

Where the two backends differ, the bolt store raises
:class:`~neo4j_agent_memory.core.exceptions.NotSupportedError` naming NAMS:
``import_(url=...)`` (no server-side fetch) and the extraction-backed import
formats (``rdf``, ``graphql``, ``cypher``, ``linkml``). :meth:`migrate` runs
synchronously rather than as a background job — see its docstring.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from neo4j_agent_memory.core.exceptions import (
    NotFoundError,
    NotSupportedError,
    SchemaError,
)
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.graph.query_builder import sanitize_label
from neo4j_agent_memory.ontology.builtin import get_template, list_templates
from neo4j_agent_memory.ontology.convert import (
    from_arrows,
    is_entity_schema_shape,
)
from neo4j_agent_memory.ontology.diff import diff_documents
from neo4j_agent_memory.ontology.models import (
    ActiveOntology,
    ImportWarning,
    MigrationJob,
    Ontology,
    OntologyDiff,
    OntologyDocument,
    OntologyImportResult,
    OntologyRecord,
    OntologySummary,
    OntologyVersion,
    _parse_document,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.graph.client import Neo4jClient

logger = logging.getLogger(__name__)

#: Synthetic id prefix for the read-only built-in templates surfaced by
#: :meth:`BoltOntology.list` (they have no rows in the database).
TEMPLATE_ID_PREFIX = "template:"

#: Validation mode applied when a caller does not pick one.
DEFAULT_VALIDATION_MODE = "permissive"

#: Import formats the bolt store converts locally. Everything else — and any
#: ``url=`` fetch — is a NAMS capability.
LOCAL_IMPORT_FORMATS = frozenset({"auto", "json", "yaml", "native", "arrows"})

# ``BoltOntology`` defines a method named ``list`` which shadows the builtin
# inside class-scope annotation resolution for some type checkers. Expose the
# builtin under an alias so the affected annotations stay unambiguous.
_List = list


# -----------------------------------------------------------------------------
# Row helpers
# -----------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    """Render a Neo4j ``DateTime`` (or anything datetime-ish) as ISO-8601.

    The ontology models carry timestamps as strings so the same shape works
    off the NAMS wire and out of the graph.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    to_native = getattr(value, "to_native", None)
    if callable(to_native):
        value = to_native()
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _document_json(document: OntologyDocument) -> str:
    """Serialise a document for the ``OntologyVersion.document`` property."""
    return document.model_dump_json()


def _schema_hash(document: OntologyDocument) -> str:
    """Content-address a document (SHA-256 over its ``content_key()``)."""
    return hashlib.sha256(document.content_key().encode("utf-8")).hexdigest()


def _as_document(schema: OntologyDocument | dict[str, Any]) -> OntologyDocument:
    """Accept either an already-built document or a raw mapping."""
    if isinstance(schema, OntologyDocument):
        return schema
    return OntologyDocument.model_validate(schema)


def _require_sound(document: OntologyDocument) -> None:
    """Raise :class:`ValueError` listing every structural problem, if any."""
    problems = document.validate_structure()
    if problems:
        listing = "\n".join(f"  - {problem}" for problem in problems)
        raise ValueError(f"Ontology is structurally invalid:\n{listing}")


def _record_from_node(node: dict[str, Any]) -> OntologyRecord:
    """Build an :class:`OntologyRecord` from an ``(:Ontology)`` node."""
    return OntologyRecord(
        id=node["id"],
        name=node.get("name") or "",
        description=node.get("description"),
        is_system=bool(node.get("is_system", False)),
        created_at=_iso(node.get("created_at")),
    )


def _version_from_node(node: dict[str, Any]) -> OntologyVersion:
    """Build an :class:`OntologyVersion` from an ``(:OntologyVersion)`` node."""
    return OntologyVersion(
        id=node["id"],
        ontology_id=node.get("ontology_id") or "",
        revision=int(node.get("revision") or 1),
        validation_mode=node.get("validation_mode") or DEFAULT_VALIDATION_MODE,
        document=_parse_document(node.get("document")),
        schema_hash=node.get("schema_hash"),
        created_at=_iso(node.get("created_at")),
        message=node.get("message"),
    )


def _migration_from_node(node: dict[str, Any]) -> MigrationJob:
    """Build a :class:`MigrationJob` from an ``(:OntologyMigration)`` node."""
    raw_spec = node.get("spec")
    spec: dict[str, Any] | None
    if isinstance(raw_spec, str):
        try:
            parsed = json.loads(raw_spec)
        except ValueError:
            parsed = None
        spec = parsed if isinstance(parsed, dict) else None
    elif isinstance(raw_spec, dict):
        spec = raw_spec
    else:
        spec = None

    return MigrationJob(
        id=node["id"],
        ontology_id=node.get("ontology_id"),
        status=node.get("status"),
        total=node.get("total"),
        processed=node.get("processed"),
        errored=node.get("errored"),
        error_message=node.get("error_message"),
        spec=spec,
        created_at=_iso(node.get("created_at")),
        completed_at=_iso(node.get("completed_at")),
    )


def _normalize_mappings(
    type_mappings: _List[tuple[str, str]] | _List[dict[str, str]],
) -> _List[tuple[str, str]]:
    """Normalise ``(from, to)`` pairs or ``{"from": ..., "to": ...}`` dicts."""
    pairs: list[tuple[str, str]] = []
    for mapping in type_mappings:
        if isinstance(mapping, dict):
            source = mapping.get("from")
            target = mapping.get("to")
        else:
            source, target = mapping[0], mapping[1]
        if not source or not target:
            raise ValueError(f"Invalid type mapping {mapping!r}: needs both 'from' and 'to'.")
        pairs.append((source, target))
    if not pairs:
        raise ValueError("migrate() requires at least one type mapping.")
    return pairs


# -----------------------------------------------------------------------------
# Accessor
# -----------------------------------------------------------------------------


class BoltOntology:
    """``client.ontology`` on bolt — versioned ontologies stored in Neo4j.

    Satisfies :class:`~neo4j_agent_memory.ontology.protocol.OntologyAPI`.
    Every method is async and goes through
    :class:`~neo4j_agent_memory.graph.client.Neo4jClient`.
    """

    def __init__(self, client: Neo4jClient) -> None:
        """Initialise the store.

        Args:
            client: Connected Neo4j client.
        """
        self._client = client

    # -- reads ----------------------------------------------------------------

    async def list(self) -> _List[OntologySummary]:
        """List built-in templates first, then ontologies stored here.

        Templates are read-only rows synthesised from
        :func:`~neo4j_agent_memory.ontology.builtin.list_templates`; they carry
        ``is_system=True``, an ``id`` of ``"template:<name>"`` and no
        ``current_revision``. Stored ontologies follow in creation order, with
        ``current_revision`` set to their highest revision and ``is_active``
        true when any of their versions is the bound one.

        Returns:
            One :class:`OntologySummary` per template and stored ontology.
        """
        summaries = [self._template_summary(name) for name in list_templates()]

        rows = await self._client.execute_read(queries.LIST_ONTOLOGIES)
        for row in rows:
            node = row.get("ontology")
            if not node:
                continue
            summaries.append(
                OntologySummary(
                    id=node["id"],
                    name=node.get("name") or "",
                    display_name=node.get("name"),
                    description=node.get("description"),
                    is_system=False,
                    current_revision=row.get("current_revision"),
                    is_active=bool(row.get("is_active", False)),
                )
            )
        return summaries

    async def get(self, ontology_id: str) -> Ontology:
        """Return one ontology with its full revision history.

        A ``"template:<name>"`` id resolves to a synthetic, read-only record
        with a single revision holding the built-in document.

        Args:
            ontology_id: Stored ontology id, or a template id.

        Returns:
            The :class:`Ontology`, versions sorted by ascending revision.

        Raises:
            NotFoundError: No such ontology (or template).
        """
        if ontology_id.startswith(TEMPLATE_ID_PREFIX):
            return self._template_ontology(ontology_id)

        rows = await self._client.execute_read(queries.GET_ONTOLOGY, {"id": ontology_id})
        if not rows or not rows[0].get("ontology"):
            raise NotFoundError(f"Ontology {ontology_id!r} not found.")

        row = rows[0]
        versions = [_version_from_node(node) for node in (row.get("versions") or []) if node]
        versions.sort(key=lambda version: version.revision)
        return Ontology(record=_record_from_node(row["ontology"]), versions=versions)

    async def get_active(self) -> ActiveOntology:
        """Return the bound ontology's document plus its version metadata.

        .. note::

           When nothing is bound this raises
           :class:`~neo4j_agent_memory.core.exceptions.NotSupportedError`,
           exactly as :meth:`NamsOntology.get_active
           <neo4j_agent_memory.nams.ontology.NamsOntology.get_active>` does for
           a workspace with no active ontology — backend-neutral code can catch
           one error type. It does **not** silently fall back to the built-in
           POLE+O ontology; callers that want that fallback (the runtime
           ontology resolution does) apply it themselves.

        Returns:
            The :class:`ActiveOntology` with ``validation_mode``, ``revision``,
            ``ontology_id`` and ``version_id`` populated.

        Raises:
            NotSupportedError: No active ontology is bound in this database.
            SchemaError: The active version's stored document is unreadable.
        """
        rows = await self._client.execute_read(queries.GET_ACTIVE_ONTOLOGY_VERSION)
        if not rows or not rows[0].get("v"):
            raise NotSupportedError(
                backend="bolt",
                method="ontology.get_active",
                message="No active ontology bound for this database.",
                workaround="Create or clone one, then call client.ontology.activate(version.id).",
            )

        row = rows[0]
        version = _version_from_node(row["v"])
        if version.document is None:
            raise SchemaError(
                f"Active ontology version {version.id!r} holds an unreadable document; "
                "activate a different revision or re-create it."
            )

        ontology_node = row.get("ontology") or {}
        return ActiveOntology(
            document=version.document,
            validation_mode=version.validation_mode,
            revision=version.revision,
            ontology_id=version.ontology_id or ontology_node.get("id"),
            version_id=version.id,
        )

    # -- writes ---------------------------------------------------------------

    async def clone(self, template_name: str) -> OntologyVersion:
        """Copy a built-in template into an editable ontology at revision 1.

        The copy takes the template's name, or ``"<name>-copy-N"`` when that
        name is already taken, and its ``domain.id``/``domain.name`` are
        rewritten to match. The new version is **not** activated.

        Args:
            template_name: One of
                :func:`~neo4j_agent_memory.ontology.builtin.list_templates`.

        Returns:
            The created revision-1 :class:`OntologyVersion`.

        Raises:
            NotFoundError: No such built-in template.
        """
        try:
            template = get_template(template_name)
        except ValueError as exc:
            raise NotFoundError(str(exc)) from exc

        name = await self._unique_name(template_name)
        document = template.model_copy(
            update={"domain": template.domain.model_copy(update={"id": name, "name": name})}
        )
        return await self._insert(
            name=name,
            document=document,
            validation_mode=DEFAULT_VALIDATION_MODE,
            message=f"Cloned from built-in template {template_name!r}",
        )

    async def create(
        self,
        name: str,
        schema: OntologyDocument | dict[str, Any],
        *,
        validation_mode: str | None = None,
    ) -> OntologyVersion:
        """Create a new ontology from a schema body (revision 1, not active).

        Identity comes from ``schema.domain`` (``id``, then ``name``) like the
        hosted backend does; ``name`` is the fallback when the document's
        domain carries neither.

        Args:
            name: Fallback ontology name.
            schema: An :class:`OntologyDocument` or an equivalent mapping.
            validation_mode: ``"permissive"`` (default) or ``"strict"``.

        Returns:
            The created revision-1 :class:`OntologyVersion`.

        Raises:
            ValueError: The document has structural problems — the message
                lists every one of them (see
                :meth:`OntologyDocument.validate_structure`).
        """
        document = _as_document(schema)
        _require_sound(document)
        return await self._insert(
            name=document.domain.id or document.domain.name or name,
            document=document,
            validation_mode=validation_mode or DEFAULT_VALIDATION_MODE,
            message=None,
        )

    async def update(
        self,
        ontology_id: str,
        schema: OntologyDocument | dict[str, Any],
        *,
        validation_mode: str | None = None,
    ) -> OntologyVersion:
        """Append an immutable revision (n+1) with an updated schema body.

        Revisions are never rewritten, and this does not change which version
        is active — call :meth:`activate` for that. When ``validation_mode`` is
        omitted the new revision inherits the previous revision's mode.

        Args:
            ontology_id: The stored ontology to extend.
            schema: An :class:`OntologyDocument` or an equivalent mapping.
            validation_mode: Override the inherited mode.

        Returns:
            The newly created :class:`OntologyVersion`.

        Raises:
            ValueError: The document has structural problems, or
                ``ontology_id`` names a read-only built-in template.
            NotFoundError: No such ontology.
        """
        self._reject_template(ontology_id, "updated")
        document = _as_document(schema)
        _require_sound(document)

        rows = await self._client.execute_read(
            queries.GET_ONTOLOGY_MAX_REVISION, {"id": ontology_id}
        )
        if not rows:
            raise NotFoundError(f"Ontology {ontology_id!r} not found.")

        row = rows[0]
        inherited = row.get("latest_validation_mode")
        return await self._add_version(
            ontology_id,
            document,
            revision=int(row.get("max_revision") or 0) + 1,
            validation_mode=validation_mode or inherited or DEFAULT_VALIDATION_MODE,
            message=None,
        )

    async def activate(self, version_id: str) -> OntologyVersion:
        """Bind one version, clearing the flag on every other one.

        The whole swap happens in a single write, so "exactly one active
        version per database" holds even when two callers activate at once.

        Args:
            version_id: The :class:`OntologyVersion` to bind.

        Returns:
            The now-active version.

        Raises:
            NotFoundError: No such version.
        """
        rows = await self._client.execute_write(
            queries.ACTIVATE_ONTOLOGY_VERSION, {"id": version_id}
        )
        if not rows or not rows[0].get("v"):
            raise NotFoundError(f"Ontology version {version_id!r} not found.")
        return _version_from_node(rows[0]["v"])

    async def delete(self, ontology_id: str) -> None:
        """Delete an ontology, all its revisions and its migration records.

        Args:
            ontology_id: The stored ontology to delete.

        Raises:
            ValueError: ``ontology_id`` names a read-only built-in template.
            NotFoundError: No such ontology.
        """
        self._reject_template(ontology_id, "deleted")
        rows = await self._client.execute_write(queries.DELETE_ONTOLOGY, {"id": ontology_id})
        if not rows:
            raise NotFoundError(f"Ontology {ontology_id!r} not found.")

    # -- conversion + comparison ---------------------------------------------

    async def import_(
        self,
        *,
        content: str | None = None,
        url: str | None = None,
        format: str | None = None,  # noqa: A002 — mirrors the wire field name
    ) -> OntologyImportResult:
        """Convert an external document into a **non-persisted** draft.

        Bolt converts locally, so only the formats that need no extraction
        service are available: ``"json"``, ``"yaml"``, ``"native"`` (an
        :class:`OntologyDocument` or legacy ``EntitySchemaConfig`` body),
        ``"arrows"`` (an `arrows.app <https://arrows.app>`_ export) and
        ``"auto"``. ``auto`` picks arrows when the parsed body has top-level
        ``nodes`` and ``relationships`` keys, and the native path otherwise.

        Persist the result with ``create(name, result.document)``.

        Args:
            content: The raw document, inline.
            url: NAMS-only (server-side fetch).
            format: An explicit converter id, or ``None``/``"auto"``.

        Returns:
            An :class:`OntologyImportResult` — the draft plus any conversion
            warnings, including a ``invalid_structure`` warning per problem
            :meth:`OntologyDocument.validate_structure` reports.

        Raises:
            NotSupportedError: ``url=`` was passed, or ``format`` names a
                converter only the hosted backend runs (``rdf``, ``graphql``,
                ``cypher``, ``linkml``, ...).
            ValueError: No ``content``, or it could not be parsed.
        """
        if url is not None:
            raise NotSupportedError(
                backend="bolt",
                method="ontology.import_(url=...)",
                message="Fetching an ontology by URL happens server-side on NAMS "
                "(SSRF-guarded and size-capped); the bolt store only converts "
                "content you hand it.",
                workaround="Download the document yourself and pass it as content=.",
            )
        if not content:
            raise ValueError("import_ requires content= (url= is a NAMS capability).")

        requested = (format or "auto").lower()
        if requested not in LOCAL_IMPORT_FORMATS:
            raise NotSupportedError(
                backend="bolt",
                method=f"ontology.import_(format={format!r})",
                message="Only "
                + ", ".join(sorted(LOCAL_IMPORT_FORMATS))
                + " are converted locally; extraction-backed converters "
                "(rdf, graphql, cypher, linkml, ...) are a NAMS capability.",
                workaround="Convert to native JSON/YAML or arrows first, or use the "
                "hosted backend.",
            )

        if requested == "arrows":
            document, warnings = from_arrows(content)
            detected = "arrows"
        else:
            data = _parse_inline(content, requested)
            # Shape detection applies to every format that only names a
            # *syntax* (json, yaml, auto): an arrows export is JSON, so
            # ``format="json"`` used to reach ``_parse_native`` and fail with a
            # raw pydantic error about a missing ``domain``. Only ``native``
            # asserts the body really is an ontology document.
            if requested != "native" and _is_arrows_shape(data):
                document, warnings = from_arrows(data)
                detected = "arrows"
            else:
                document, detected = _parse_native(data)
                warnings = []

        warnings.extend(
            ImportWarning(code="invalid_structure", message=problem)
            for problem in document.validate_structure()
        )
        return OntologyImportResult(
            document=document,
            warnings=warnings,
            detected_format=detected,
            suggested_name=document.domain.name or document.domain.id or None,
        )

    async def diff(self, ontology_id: str, from_revision: int, to_revision: int) -> OntologyDiff:
        """Diff two revisions of one ontology.

        Args:
            ontology_id: The ontology (a ``"template:<name>"`` id works too,
                though it only ever has revision 1).
            from_revision: The older revision.
            to_revision: The newer revision.

        Returns:
            An :class:`OntologyDiff` with ``from_revision``/``to_revision``
            filled in.

        Raises:
            NotFoundError: The ontology or one of the revisions is unknown, or
                a revision holds an unreadable document.
        """
        ontology = await self.get(ontology_id)
        before = _document_at(ontology, from_revision)
        after = _document_at(ontology, to_revision)

        result = diff_documents(before, after)
        result.from_revision = from_revision
        result.to_revision = to_revision
        return result

    # -- migration ------------------------------------------------------------

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
        """Relabel existing ``:Entity`` nodes for a renamed ontology label.

        .. note::

           Unlike NAMS — which enqueues a background job you poll — this runs
           **synchronously**, one transaction per ``(from_label, to_label)``
           pair, and returns an already-terminal job. ``batch_size`` is
           accepted for signature parity and ignored. Prefer a maintenance
           window (or an ``apoc.periodic.iterate`` equivalent) on large graphs.

        For each pair the ``from`` label is removed, the ``to`` label added,
        ``e.subtype`` is set from the target version's entity type, and
        ``e.type`` is rewritten when the target label maps onto a different
        POLE+O ``pole_type``. When the target entity type declares no subtype
        the subtype labels the *source* version could have written are removed
        too, so a node does not keep a ``:Individual`` label while reporting
        ``subtype = null``. Labels are sanitised with
        :func:`~neo4j_agent_memory.graph.query_builder.sanitize_label`, so they
        are matched in their PascalCase form.

        The job is persisted as an ``(:OntologyMigration)`` node either way and
        can be read back with :meth:`get_migration`.

        Args:
            ontology_id: The ontology being migrated (recorded on the job).
            from_version_id: The revision the graph was written against.
            to_version_id: The revision it should conform to.
            type_mappings: ``(from_label, to_label)`` pairs, or
                ``{"from": ..., "to": ...}`` dicts.
            dry_run: Count matching nodes without mutating anything.
            batch_size: Ignored (see the note).

        Returns:
            A terminal :class:`MigrationJob` — ``status`` is ``"completed"``
            or ``"failed"``, ``total`` counts matched nodes and ``processed``
            counts relabelled ones (always 0 for a dry run).

        Raises:
            NotFoundError: Either version id is unknown.
            ValueError: A mapping is malformed, a label is not a valid Neo4j
                identifier, the target label is not declared by the target
                version's document, or the target declares no subtype while
                the source label it replaces is not declared by the source
                version (the stale subtype labels cannot be derived).
        """
        pairs = _normalize_mappings(type_mappings)
        source_document = await self._version_document(from_version_id)
        target_document = await self._version_document(to_version_id)

        plan = [_plan_mapping(source_document, target_document, pair) for pair in pairs]

        total = 0
        processed = 0
        errored = 0
        errors: list[str] = []

        for step in plan:
            try:
                if dry_run:
                    rows = await self._client.execute_read(
                        queries.count_entities_with_label_query(step.from_label)
                    )
                    total += int(rows[0]["matched"]) if rows else 0
                else:
                    params: dict[str, Any] = {"subtype": step.subtype}
                    if step.set_type:
                        params["type"] = step.pole_type
                    rows = await self._client.execute_write(
                        queries.relabel_entities_query(
                            step.from_label,
                            step.to_label,
                            set_type=step.set_type,
                            remove_subtype_labels=step.remove_subtype_labels,
                        ),
                        params,
                    )
                    migrated = int(rows[0]["migrated"]) if rows else 0
                    total += migrated
                    processed += migrated
            except Exception as exc:  # noqa: BLE001 — recorded on the job, not swallowed
                errored += 1
                errors.append(f"{step.from_label} -> {step.to_label}: {exc}")
                logger.warning(
                    "Ontology migration step %s -> %s failed.",
                    step.from_label,
                    step.to_label,
                    exc_info=True,
                )

        spec: dict[str, Any] = {
            "from_version_id": from_version_id,
            "to_version_id": to_version_id,
            "type_mappings": [{"from": source, "to": target} for source, target in pairs],
            "dry_run": dry_run,
        }
        if batch_size is not None:
            spec["batch_size"] = batch_size

        rows = await self._client.execute_write(
            queries.CREATE_ONTOLOGY_MIGRATION,
            {
                "id": uuid4().hex,
                "ontology_id": ontology_id,
                "status": "failed" if errored else "completed",
                "total": total,
                "processed": processed,
                "errored": errored,
                "spec": json.dumps(spec),
                "error_message": "; ".join(errors) or None,
            },
        )
        if not rows or not rows[0].get("m"):
            raise SchemaError("Failed to record the ontology migration job.")
        return _migration_from_node(rows[0]["m"])

    async def get_migration(self, job_id: str) -> MigrationJob:
        """Read one recorded migration back.

        Args:
            job_id: The job id returned by :meth:`migrate`.

        Returns:
            The :class:`MigrationJob`.

        Raises:
            NotFoundError: No such job.
        """
        rows = await self._client.execute_read(queries.GET_ONTOLOGY_MIGRATION, {"id": job_id})
        if not rows or not rows[0].get("m"):
            raise NotFoundError(f"Ontology migration {job_id!r} not found.")
        return _migration_from_node(rows[0]["m"])

    # -- internals ------------------------------------------------------------

    def _template_summary(self, name: str) -> OntologySummary:
        """One read-only ``is_system`` row for a built-in template."""
        document = get_template(name)
        return OntologySummary(
            id=f"{TEMPLATE_ID_PREFIX}{name}",
            name=name,
            display_name=document.domain.name,
            description=document.domain.description,
            emoji=document.domain.emoji,
            tagline=document.domain.tagline,
            is_system=True,
            current_revision=None,
            is_active=False,
        )

    def _template_ontology(self, ontology_id: str) -> Ontology:
        """Synthesise the single-revision view of a built-in template."""
        name = ontology_id[len(TEMPLATE_ID_PREFIX) :]
        try:
            document = get_template(name)
        except ValueError as exc:
            raise NotFoundError(str(exc)) from exc

        return Ontology(
            record=OntologyRecord(
                id=ontology_id,
                name=name,
                description=document.domain.description,
                is_system=True,
            ),
            versions=[
                OntologyVersion(
                    id=f"{ontology_id}:1",
                    ontology_id=ontology_id,
                    revision=1,
                    validation_mode=DEFAULT_VALIDATION_MODE,
                    document=document,
                    schema_hash=_schema_hash(document),
                )
            ],
        )

    @staticmethod
    def _reject_template(ontology_id: str, verb: str) -> None:
        """Guard the mutating paths against ``"template:<name>"`` ids."""
        if ontology_id.startswith(TEMPLATE_ID_PREFIX):
            raise ValueError(
                f"Built-in template {ontology_id!r} is read-only and cannot be {verb}. "
                "Clone it first: await client.ontology.clone(<name>)."
            )

    async def _unique_name(self, base: str) -> str:
        """``base``, or ``"<base>-copy-N"`` when that name is taken."""
        rows = await self._client.execute_read(queries.LIST_ONTOLOGIES)
        taken = {row["ontology"].get("name") for row in rows if row.get("ontology") is not None}
        if base not in taken:
            return base
        suffix = 1
        while f"{base}-copy-{suffix}" in taken:
            suffix += 1
        return f"{base}-copy-{suffix}"

    async def _insert(
        self,
        *,
        name: str,
        document: OntologyDocument,
        validation_mode: str,
        message: str | None,
    ) -> OntologyVersion:
        """Create an ``(:Ontology)`` plus its revision-1 version."""
        ontology_id = uuid4().hex
        await self._client.execute_write(
            queries.CREATE_ONTOLOGY,
            {
                "id": ontology_id,
                "name": name,
                "description": document.domain.description,
            },
        )
        return await self._add_version(
            ontology_id,
            document,
            revision=1,
            validation_mode=validation_mode,
            message=message,
        )

    async def _add_version(
        self,
        ontology_id: str,
        document: OntologyDocument,
        *,
        revision: int,
        validation_mode: str,
        message: str | None,
    ) -> OntologyVersion:
        """Append one ``(:OntologyVersion)`` to an existing ontology."""
        rows = await self._client.execute_write(
            queries.CREATE_ONTOLOGY_VERSION,
            {
                "id": uuid4().hex,
                "ontology_id": ontology_id,
                "revision": revision,
                "validation_mode": validation_mode,
                "document": _document_json(document),
                "schema_hash": _schema_hash(document),
                "message": message,
            },
        )
        if not rows or not rows[0].get("v"):
            raise NotFoundError(f"Ontology {ontology_id!r} not found.")
        return _version_from_node(rows[0]["v"])

    async def _version_document(self, version_id: str) -> OntologyDocument:
        """Load one version's parsed document."""
        rows = await self._client.execute_read(queries.GET_ONTOLOGY_VERSION, {"id": version_id})
        if not rows or not rows[0].get("v"):
            raise NotFoundError(f"Ontology version {version_id!r} not found.")
        document = _version_from_node(rows[0]["v"]).document
        if document is None:
            raise SchemaError(f"Ontology version {version_id!r} holds an unreadable document.")
        return document


# -----------------------------------------------------------------------------
# Migration planning
# -----------------------------------------------------------------------------


class _MappingStep:
    """One resolved ``(from_label, to_label)`` relabel step."""

    __slots__ = (
        "from_label",
        "pole_type",
        "remove_subtype_labels",
        "set_type",
        "subtype",
        "to_label",
    )

    def __init__(
        self,
        *,
        from_label: str,
        to_label: str,
        subtype: str | None,
        pole_type: str,
        set_type: bool,
        remove_subtype_labels: tuple[str, ...] = (),
    ) -> None:
        self.from_label = from_label
        self.to_label = to_label
        self.subtype = subtype
        self.pole_type = pole_type
        self.set_type = set_type
        self.remove_subtype_labels = remove_subtype_labels


def _plan_mapping(
    source: OntologyDocument,
    target: OntologyDocument,
    pair: tuple[str, str],
) -> _MappingStep:
    """Resolve one mapping against the two documents, or explain why not."""
    raw_from, raw_to = pair
    from_label = sanitize_label(raw_from)
    to_label = sanitize_label(raw_to)
    if from_label is None or to_label is None:
        raise ValueError(
            f"Type mapping {raw_from!r} -> {raw_to!r} contains a label that is not a "
            "valid Neo4j identifier ([A-Za-z][A-Za-z0-9_]*)."
        )

    target_type = target.entity_type(raw_to)
    if target_type is None:
        raise ValueError(
            f"Target label {raw_to!r} is not declared by the target ontology version; "
            f"declared labels: {', '.join(target.labels()) or '(none)'}."
        )

    source_type = source.entity_type(raw_from)
    pole_type = target_type.pole_type.upper()
    set_type = source_type is None or source_type.pole_type.upper() != pole_type
    subtype = target_type.subtype.upper() if target_type.subtype else None
    return _MappingStep(
        from_label=from_label,
        to_label=to_label,
        subtype=subtype,
        pole_type=pole_type,
        set_type=set_type,
        remove_subtype_labels=(
            () if subtype is not None else _stale_subtype_labels(source, raw_from, raw_to)
        ),
    )


def _stale_subtype_labels(source: OntologyDocument, raw_from: str, raw_to: str) -> tuple[str, ...]:
    """The subtype labels to strip when the target declares no subtype.

    ``relabel_entities_query`` sets ``e.subtype = null``, but Cypher cannot
    remove a label without naming it, so a node written as
    ``:Entity:Person:Individual`` would keep ``:Individual`` — still matching
    every subtype-scoped query — while claiming no subtype. The labels a node
    under ``raw_from`` can be carrying are exactly the ones the *source*
    version declares for that entity type's ``pole_type``, so they come from
    the source document. Removing one a node does not carry is a no-op, so an
    over-broad set is safe.

    Args:
        source: The ontology version the graph was written against.
        raw_from: The label being migrated away from.
        raw_to: The target label (for the error message only).

    Returns:
        Sanitised (PascalCase) subtype labels to remove, sorted.

    Raises:
        ValueError: ``raw_from`` is not declared by the source version and
            that version declares subtypes, so the set cannot be derived.
    """
    source_type = source.entity_type(raw_from)
    if source_type is None:
        if any(et.subtype for et in source.entity_types):
            raise ValueError(
                f"Cannot map {raw_from!r} -> {raw_to!r}: the target entity type declares no "
                f"subtype, so the migration has to strip the subtype label the old one left "
                f"behind — but {raw_from!r} is not declared by the source ontology version "
                f"(declared: {', '.join(source.labels()) or '(none)'}), so there is no way to "
                f"tell which subtype labels its nodes carry. Declare {raw_from!r} in the "
                f"source version, or map to a target entity type that declares a subtype."
            )
        return ()

    pole_type = source_type.pole_type.upper()
    labels = {
        sanitize_label(et.subtype)
        for et in source.entity_types
        if et.subtype and et.pole_type.upper() == pole_type
    }
    return tuple(sorted(label for label in labels if label is not None))


def _document_at(ontology: Ontology, revision: int) -> OntologyDocument:
    """Pull one revision's document out of a loaded ontology."""
    for version in ontology.versions:
        if version.revision == revision:
            if version.document is None:
                raise NotFoundError(
                    f"Revision {revision} of ontology {ontology.record.id!r} holds an "
                    "unreadable document."
                )
            return version.document
    raise NotFoundError(f"Ontology {ontology.record.id!r} has no revision {revision}.")


# -----------------------------------------------------------------------------
# Inline parsing (import_)
# -----------------------------------------------------------------------------


def _parse_inline(content: str, requested: str) -> dict[str, Any]:
    """Parse inline JSON/YAML content into a mapping.

    ``load_ontology`` only reads files, so ``import_`` parses the string here.

    Args:
        content: The raw document.
        requested: ``"json"``, ``"yaml"``, ``"native"`` or ``"auto"``.

    Returns:
        The parsed mapping.

    Raises:
        ValueError: The content is not parseable as the requested format, or
            does not decode to a mapping.
    """
    data: Any = None
    if requested == "json":
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise ValueError(f"Not valid JSON: {exc}") from exc
    elif requested == "yaml":
        data = _yaml_load(content)
    else:
        try:
            data = json.loads(content)
        except ValueError:
            data = _yaml_load(content)

    if not isinstance(data, dict):
        raise ValueError("Ontology content does not decode to a mapping.")
    return data


def _yaml_load(content: str) -> Any:
    """Parse YAML, with a clear error when PyYAML is not installed."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a dev dependency
        raise ValueError(
            "PyYAML is required to import YAML ontologies. Install with: pip install pyyaml"
        ) from exc
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Not valid YAML: {exc}") from exc


def _is_arrows_shape(data: dict[str, Any]) -> bool:
    """Whether a parsed mapping looks like an arrows.app export."""
    return isinstance(data.get("nodes"), list) and isinstance(data.get("relationships"), list)


def _parse_native(data: dict[str, Any]) -> tuple[OntologyDocument, str]:
    """Parse a native ontology body, or a legacy ``EntitySchemaConfig`` one."""
    if is_entity_schema_shape(data):
        from neo4j_agent_memory.ontology.convert import from_entity_schema
        from neo4j_agent_memory.schema.models import EntitySchemaConfig

        return from_entity_schema(EntitySchemaConfig(**data)), "entity_schema"
    return OntologyDocument.model_validate(data), "native"


__all__ = [
    "DEFAULT_VALIDATION_MODE",
    "LOCAL_IMPORT_FORMATS",
    "TEMPLATE_ID_PREFIX",
    "BoltOntology",
]
