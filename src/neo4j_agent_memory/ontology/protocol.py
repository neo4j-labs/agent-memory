"""The ``client.ontology`` surface, as a backend-neutral Protocol.

Both implementations satisfy it: :class:`~neo4j_agent_memory.nams.ontology.NamsOntology`
(hosted, REST) and the bolt-backed store. Typing the accessor as
:class:`OntologyAPI` keeps call sites backend-agnostic; operations a backend
cannot honour raise
:class:`~neo4j_agent_memory.core.exceptions.NotSupportedError` at runtime.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from neo4j_agent_memory.ontology.models import (
    ActiveOntology,
    MigrationJob,
    Ontology,
    OntologyDiff,
    OntologyDocument,
    OntologyImportResult,
    OntologySummary,
    OntologyVersion,
)

# ``OntologyAPI`` declares a method named ``list``, which shadows the builtin
# inside class-scope annotation resolution for some type checkers. Expose the
# builtin under an alias so the affected annotations stay unambiguous.
_List = list


@runtime_checkable
class OntologyAPI(Protocol):
    """The ontology lifecycle: templates, revisions, activation, migration."""

    async def list(self) -> _List[OntologySummary]:
        """List system templates + workspace-owned ontologies (active flagged)."""
        ...

    async def get(self, ontology_id: str) -> Ontology:
        """Return one ontology with its full revision history."""
        ...

    async def get_active(self) -> ActiveOntology:
        """Return the active ontology's parsed body + composed version metadata."""
        ...

    async def clone(self, template_name: str) -> OntologyVersion:
        """Clone a system template into an editable copy (revision 1)."""
        ...

    async def create(
        self,
        name: str,
        schema: OntologyDocument | dict[str, Any],
        *,
        validation_mode: str | None = None,
    ) -> OntologyVersion:
        """Create a new ontology from a schema body (revision 1)."""
        ...

    async def update(
        self,
        ontology_id: str,
        schema: OntologyDocument | dict[str, Any],
        *,
        validation_mode: str | None = None,
    ) -> OntologyVersion:
        """Create a new immutable revision (n+1) from an updated schema body."""
        ...

    async def activate(self, version_id: str) -> OntologyVersion:
        """Bind a version; subsequent entity writes validate against it."""
        ...

    async def delete(self, ontology_id: str) -> None:
        """Delete an ontology and its revisions."""
        ...

    async def import_(
        self,
        *,
        content: str | None = None,
        url: str | None = None,
        format: str | None = None,  # noqa: A002 — mirrors the wire field name
    ) -> OntologyImportResult:
        """Convert an external graph/ontology document into a native draft."""
        ...

    async def diff(self, ontology_id: str, from_revision: int, to_revision: int) -> OntologyDiff:
        """Diff two revisions of an ontology (added / removed / renamed / modified)."""
        ...

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
        """Run or enqueue a label-rename migration; returns the job."""
        ...

    async def get_migration(self, job_id: str) -> MigrationJob:
        """Return one migration job's current status."""
        ...


__all__ = ["OntologyAPI"]
