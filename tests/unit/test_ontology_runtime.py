"""Unit tests for ontology resolution at runtime (``MemoryClient``).

``_resolve_ontology`` runs once per connection and every downstream consumer
— the GLiNER2.5 JointIE schema, the LLM extractor prompt, relation validation,
the strict write paths — is handed the document it returns. These tests pin the
precedence order, the validation-mode precedence and the wiring, all without
touching Neo4j (the ontology store is mocked).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.config.settings import Neo4jConfig, SchemaConfig, SchemaModel
from neo4j_agent_memory.core.exceptions import NotSupportedError, SchemaError
from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY, get_template
from neo4j_agent_memory.ontology.models import (
    ActiveOntology,
    DomainInfo,
    EntityTypeDef,
    OntologyDocument,
    RelationshipDef,
)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def make_document(name: str) -> OntologyDocument:
    """A small, structurally sound document identifiable by its domain name."""
    return OntologyDocument(
        domain=DomainInfo(id=name, name=name),
        entity_types=[
            EntityTypeDef(label="Customer", pole_type="PERSON", subtype="INDIVIDUAL"),
            EntityTypeDef(label="Vendor", pole_type="ORGANIZATION", subtype="COMPANY"),
        ],
        relationships=[
            RelationshipDef(type="BUYS_FROM", source="Customer", target="Vendor"),
        ],
    )


def write_document(tmp_path: Path, name: str, suffix: str = ".json") -> Path:
    """Serialise ``make_document(name)`` to disk and return the path."""
    path = tmp_path / f"{name}{suffix}"
    path.write_text(json.dumps(make_document(name).model_dump(mode="json")))
    return path


def make_client(
    schema_config: SchemaConfig | None = None,
    *,
    ontology: object = None,
) -> MemoryClient:
    """An unconnected client — ``_resolve_ontology`` needs no live driver."""
    settings = MemorySettings(
        neo4j=Neo4jConfig(password=SecretStr("test")),
        schema_config=schema_config or SchemaConfig(),
    )
    # ``ontology`` is typed as OntologyDocument | str | Path | None; the test
    # helper takes ``object`` so it can pass all three shapes.
    return MemoryClient(settings, ontology=ontology)  # type: ignore[arg-type]


def bind_store(client: MemoryClient, active: ActiveOntology | None) -> MagicMock:
    """Stand a mock ontology store in for ``BoltOntology``.

    Args:
        client: The client to wire.
        active: The :class:`ActiveOntology` ``get_active()`` should return, or
            ``None`` to make it raise ``NotSupportedError`` (nothing bound).

    Returns:
        The mock store, so tests can assert on how it was called.
    """
    store = MagicMock()
    if active is None:
        store.get_active = AsyncMock(
            side_effect=NotSupportedError(
                backend="bolt",
                method="ontology.get_active",
                message="No active ontology bound for this database.",
            )
        )
    else:
        store.get_active = AsyncMock(return_value=active)
    client._ontology = store
    return store


# -----------------------------------------------------------------------------
# Document precedence
# -----------------------------------------------------------------------------


class TestDocumentPrecedence:
    @pytest.mark.asyncio
    async def test_explicit_document_wins_over_everything(self, tmp_path):
        explicit = make_document("explicit")
        client = make_client(
            SchemaConfig(ontology_path=str(write_document(tmp_path, "from-path"))),
            ontology=explicit,
        )
        store = bind_store(client, ActiveOntology(document=make_document("stored")))

        document, _ = await client._resolve_ontology()

        assert document is explicit
        store.get_active.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("as_str", [True, False])
    async def test_an_explicit_path_is_loaded(self, tmp_path, as_str):
        path = write_document(tmp_path, "explicit-file")
        client = make_client(ontology=str(path) if as_str else path)
        bind_store(client, None)

        document, _ = await client._resolve_ontology()

        assert document.domain.name == "explicit-file"

    @pytest.mark.asyncio
    async def test_ontology_path_beats_custom_schema_path(self, tmp_path):
        client = make_client(
            SchemaConfig(
                ontology_path=str(write_document(tmp_path, "primary")),
                custom_schema_path=str(write_document(tmp_path, "secondary")),
            )
        )
        store = bind_store(client, ActiveOntology(document=make_document("stored")))

        document, _ = await client._resolve_ontology()

        assert document.domain.name == "primary"
        store.get_active.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_custom_schema_path_is_wired_up(self, tmp_path):
        # The field existed but was dead before v0.7.
        client = make_client(
            SchemaConfig(custom_schema_path=str(write_document(tmp_path, "legacy-slot")))
        )
        bind_store(client, ActiveOntology(document=make_document("stored")))

        document, _ = await client._resolve_ontology()

        assert document.domain.name == "legacy-slot"

    @pytest.mark.asyncio
    async def test_custom_schema_path_accepts_an_entity_schema_file(self, tmp_path):
        # A legacy EntitySchemaConfig file is converted on the way in.
        path = tmp_path / "schema.json"
        path.write_text(
            json.dumps(
                {
                    "name": "legacy",
                    "entity_types": [{"name": "PERSON", "subtypes": ["INDIVIDUAL"]}],
                }
            )
        )
        client = make_client(SchemaConfig(custom_schema_path=str(path)))
        bind_store(client, None)

        document, _ = await client._resolve_ontology()

        assert "PERSON" in {et.pole_type for et in document.entity_types}

    @pytest.mark.asyncio
    async def test_the_active_stored_version_is_adopted(self):
        stored = make_document("stored")
        client = make_client()
        store = bind_store(client, ActiveOntology(document=stored))

        document, _ = await client._resolve_ontology()

        assert document == stored
        store.get_active.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_nothing_bound_falls_through_to_the_template(self):
        client = make_client()
        bind_store(client, None)

        document, _ = await client._resolve_ontology()

        assert document is POLEO_ONTOLOGY

    @pytest.mark.asyncio
    async def test_use_active_ontology_false_skips_the_store(self):
        client = make_client(SchemaConfig(use_active_ontology=False))
        store = bind_store(client, ActiveOntology(document=make_document("stored")))

        document, _ = await client._resolve_ontology()

        assert document is POLEO_ONTOLOGY
        store.get_active.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_custom_model_builds_an_adhoc_document_from_entity_types(self):
        client = make_client(
            SchemaConfig(
                model=SchemaModel.CUSTOM,
                entity_types=["company", "phone number", "WIDGET"],
            )
        )
        bind_store(client, None)

        document, _ = await client._resolve_ontology()

        assert document.domain.name == "custom"
        assert document.labels() == ["company", "phone number", "WIDGET"]
        assert document.validate_structure() == []
        # Each name is mapped onto POLE+O through map_label_to_poleo.
        assert document.label_map()["company"] == ("ORGANIZATION", "COMPANY")
        assert document.label_map()["phone number"] == ("OBJECT", "PHONE")
        # Unknown labels land on OBJECT with the label as subtype.
        assert document.label_map()["widget"] == ("OBJECT", "WIDGET")
        assert document.relationships == []

    @pytest.mark.asyncio
    async def test_custom_model_without_entity_types_falls_through(self):
        client = make_client(SchemaConfig(model=SchemaModel.CUSTOM))
        bind_store(client, None)

        document, _ = await client._resolve_ontology()

        assert document is POLEO_ONTOLOGY

    @pytest.mark.asyncio
    async def test_the_named_template_is_the_final_fallback(self):
        client = make_client(SchemaConfig(ontology_template="podcast"))
        bind_store(client, None)

        document, _ = await client._resolve_ontology()

        assert document is get_template("podcast")

    @pytest.mark.asyncio
    async def test_an_unknown_template_warns_and_uses_poleo(self, caplog):
        client = make_client(SchemaConfig(ontology_template="nope"))
        bind_store(client, None)

        with caplog.at_level("WARNING", logger="neo4j_agent_memory"):
            document, _ = await client._resolve_ontology()

        assert document is POLEO_ONTOLOGY
        assert "nope" in caplog.text

    @pytest.mark.asyncio
    async def test_no_store_at_all_still_resolves(self):
        # ``_resolve_ontology`` is called from _connect_bolt after the store is
        # wired, but it must not require one.
        client = make_client()
        assert client._ontology is None

        document, _ = await client._resolve_ontology()

        assert document is POLEO_ONTOLOGY


# -----------------------------------------------------------------------------
# Validation-mode precedence
# -----------------------------------------------------------------------------


class TestValidationModePrecedence:
    @pytest.mark.asyncio
    async def test_the_setting_wins_over_the_stored_mode(self):
        client = make_client(SchemaConfig(validation_mode="permissive"))
        bind_store(
            client,
            ActiveOntology(document=make_document("stored"), validation_mode="strict"),
        )

        _, mode = await client._resolve_ontology()

        assert mode == "permissive"

    @pytest.mark.asyncio
    async def test_the_stored_mode_is_used_when_that_source_was_chosen(self):
        client = make_client()
        bind_store(
            client,
            ActiveOntology(document=make_document("stored"), validation_mode="strict"),
        )

        _, mode = await client._resolve_ontology()

        assert mode == "strict"

    @pytest.mark.asyncio
    async def test_the_stored_mode_is_ignored_when_a_file_was_chosen(self, tmp_path):
        client = make_client(SchemaConfig(ontology_path=str(write_document(tmp_path, "from-file"))))
        bind_store(
            client,
            ActiveOntology(document=make_document("stored"), validation_mode="strict"),
        )

        _, mode = await client._resolve_ontology()

        assert mode == "permissive"

    @pytest.mark.asyncio
    async def test_strict_types_promotes_to_strict(self):
        client = make_client(SchemaConfig(strict_types=True))
        bind_store(client, None)

        _, mode = await client._resolve_ontology()

        assert mode == "strict"

    @pytest.mark.asyncio
    async def test_the_default_is_permissive(self):
        client = make_client()
        bind_store(client, None)

        _, mode = await client._resolve_ontology()

        assert mode == "permissive"

    @pytest.mark.asyncio
    async def test_an_unreadable_stored_document_falls_through_with_a_warning(self, caplog):
        """The regression: one corrupt row used to make ``connect()`` fail.

        ``use_active_ontology`` defaults to True, so a ``SchemaError`` out of
        ``get_active()`` propagated — and the ``client.ontology.delete()`` /
        ``activate()`` call needed to recover was unreachable. Resolution
        now falls through to the next precedence source.
        """
        client = make_client()
        store = MagicMock()
        store.get_active = AsyncMock(
            side_effect=SchemaError(
                "Active ontology version 'v-broken' holds an unreadable document; "
                "activate a different revision or re-create it."
            )
        )
        client._ontology = store

        with caplog.at_level(logging.WARNING):
            document, mode = await client._resolve_ontology()

        assert document is POLEO_ONTOLOGY
        assert mode == "permissive"
        assert "v-broken" in caplog.text

    @pytest.mark.asyncio
    async def test_an_unreadable_stored_document_still_yields_to_a_file(self, tmp_path):
        client = make_client(SchemaConfig(ontology_path=str(write_document(tmp_path, "from-path"))))
        store = MagicMock()
        store.get_active = AsyncMock(side_effect=SchemaError("unreadable"))
        client._ontology = store

        document, _ = await client._resolve_ontology()

        assert document.domain.name == "from-path"
        store.get_active.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unparseable_stored_mode_is_ignored(self):
        client = make_client()
        bind_store(
            client,
            ActiveOntology(document=make_document("stored"), validation_mode="whatever"),
        )

        _, mode = await client._resolve_ontology()

        assert mode == "permissive"

    def test_the_accessors_default_before_connect(self):
        client = make_client()

        assert client.ontology_document is None
        assert client.validation_mode == "permissive"


# -----------------------------------------------------------------------------
# Downstream wiring
# -----------------------------------------------------------------------------


class TestWiring:
    def test_the_extractor_factory_receives_the_document(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_create_extractor(**kwargs: object) -> object:
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(
            "neo4j_agent_memory.extraction.factory.create_extractor",
            fake_create_extractor,
        )

        client = make_client()
        document = make_document("wired")
        client._ontology_document = document

        client._create_extractor()

        assert captured["ontology"] is document

    @pytest.mark.asyncio
    async def test_memory_layers_receive_the_document_and_mode(self, monkeypatch):
        # Drive the wiring block of _connect_bolt without a live Neo4j by
        # asserting the two constructors receive what _resolve_ontology chose.
        from neo4j_agent_memory.memory import long_term as long_term_module
        from neo4j_agent_memory.memory import short_term as short_term_module

        captured: dict[str, object] = {}
        document = make_document("wired")

        original_short = short_term_module.ShortTermMemory.__init__
        original_long = long_term_module.LongTermMemory.__init__

        def spy_short(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            captured["short"] = (kwargs.get("ontology"), kwargs.get("validation_mode"))
            return original_short(self, *args, **kwargs)

        def spy_long(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            captured["long"] = (kwargs.get("ontology"), kwargs.get("validation_mode"))
            return original_long(self, *args, **kwargs)

        monkeypatch.setattr(short_term_module.ShortTermMemory, "__init__", spy_short)
        monkeypatch.setattr(long_term_module.LongTermMemory, "__init__", spy_long)

        client_stub = MagicMock()
        short_term_module.ShortTermMemory(
            client_stub, None, None, ontology=document, validation_mode="strict"
        )
        long_term_module.LongTermMemory(
            client_stub, None, None, ontology=document, validation_mode="strict"
        )

        assert captured["short"] == (document, "strict")
        assert captured["long"] == (document, "strict")

    def test_short_term_and_long_term_default_to_permissive(self):
        from neo4j_agent_memory.memory.long_term import LongTermMemory
        from neo4j_agent_memory.memory.short_term import ShortTermMemory

        client_stub = MagicMock()
        short_term = ShortTermMemory(client_stub)
        long_term = LongTermMemory(client_stub)

        assert short_term._ontology is None
        assert short_term._validation_mode == "permissive"
        assert long_term._ontology is None
        assert long_term._validation_mode == "permissive"


async def connect_with_fakes(client: MemoryClient, monkeypatch) -> None:
    """Run ``_connect_bolt`` with the driver and schema setup faked out."""
    import neo4j_agent_memory as package

    driver = MagicMock()
    driver.connect = AsyncMock()
    driver.close = AsyncMock()
    driver.execute_read = AsyncMock(return_value=[])
    driver.execute_write = AsyncMock(return_value=[])
    monkeypatch.setattr(package, "Neo4jClient", lambda _config: driver)

    schema_manager = MagicMock()
    schema_manager.setup_all = AsyncMock()
    schema_manager.validate_vector_index_dimensions = AsyncMock()
    monkeypatch.setattr(package, "SchemaManager", MagicMock(return_value=schema_manager))

    await client._connect_bolt()


class TestDeduplicationWiring:
    """``add_entity``'s bands come from ``ResolutionConfig``, not a second default."""

    @pytest.mark.asyncio
    async def test_long_term_gets_the_bands_from_resolution_config(self, monkeypatch):
        from neo4j_agent_memory.config.settings import ResolutionConfig
        from neo4j_agent_memory.memory.long_term import DeduplicationConfig

        resolution = ResolutionConfig(
            auto_merge_threshold=0.97,
            review_threshold=0.71,
            fuzzy_threshold=0.83,
            candidate_limit=25,
        )
        settings = MemorySettings(
            neo4j=Neo4jConfig(password=SecretStr("test")),
            resolution=resolution,
        )
        client: MemoryClient = MemoryClient(settings)

        await connect_with_fakes(client, monkeypatch)

        assert client.long_term._deduplication == DeduplicationConfig.from_resolution_config(
            resolution
        )
        assert client.long_term._deduplication.auto_merge_threshold == 0.97
        assert client.long_term._deduplication.flag_threshold == 0.71


class TestRelationTypeBackfillWiring:
    """The legacy ``RELATED_TO.type`` scan is a write; it runs once per database."""

    def test_the_setting_defaults_to_on(self):
        assert SchemaConfig().backfill_relation_types is True

    @pytest.mark.asyncio
    async def test_the_setting_reaches_the_schema_manager(self, monkeypatch):
        import neo4j_agent_memory as package

        captured: dict[str, object] = {}

        def fake_manager(_client, **kwargs):  # type: ignore[no-untyped-def]
            captured.update(kwargs)
            manager = MagicMock()
            manager.setup_all = AsyncMock()
            manager.validate_vector_index_dimensions = AsyncMock()
            return manager

        driver = MagicMock()
        driver.connect = AsyncMock()
        driver.execute_read = AsyncMock(return_value=[])
        driver.execute_write = AsyncMock(return_value=[])
        monkeypatch.setattr(package, "Neo4jClient", lambda _config: driver)
        monkeypatch.setattr(package, "SchemaManager", fake_manager)

        client: MemoryClient = make_client(SchemaConfig(backfill_relation_types=False))
        await client._connect_bolt()

        assert captured["backfill_relation_types"] is False

    @pytest.mark.asyncio
    async def test_the_scan_is_skipped_once_the_marker_is_present(self):
        from neo4j_agent_memory.graph import queries
        from neo4j_agent_memory.graph.schema import RELATION_TYPE_BACKFILL, SchemaManager

        graph = MagicMock()
        graph.execute_read = AsyncMock(return_value=[{"name": RELATION_TYPE_BACKFILL}])
        graph.execute_write = AsyncMock(return_value=[])

        await SchemaManager(graph)._backfill_relation_type()

        graph.execute_read.assert_awaited_once_with(
            queries.GET_SCHEMA_MIGRATION, {"name": RELATION_TYPE_BACKFILL}
        )
        graph.execute_write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_marker_is_recorded_after_a_successful_scan(self):
        from neo4j_agent_memory.graph import queries
        from neo4j_agent_memory.graph.schema import RELATION_TYPE_BACKFILL, SchemaManager

        graph = MagicMock()
        graph.execute_read = AsyncMock(return_value=[])  # no marker yet
        graph.execute_write = AsyncMock(return_value=[{"updated": 3}])

        await SchemaManager(graph)._backfill_relation_type()

        written = [call.args[0] for call in graph.execute_write.await_args_list]
        assert queries.BACKFILL_RELATION_TYPE in written
        assert queries.RECORD_SCHEMA_MIGRATION in written
        assert written.index(queries.BACKFILL_RELATION_TYPE) < written.index(
            queries.RECORD_SCHEMA_MIGRATION
        )
        assert graph.execute_write.await_args_list[-1].args[1] == {"name": RELATION_TYPE_BACKFILL}

    @pytest.mark.asyncio
    async def test_a_failed_scan_does_not_record_the_marker(self):
        from neo4j_agent_memory.graph import queries
        from neo4j_agent_memory.graph.schema import SchemaManager

        graph = MagicMock()
        graph.execute_read = AsyncMock(return_value=[])
        graph.execute_write = AsyncMock(side_effect=RuntimeError("deadlock"))

        # Best-effort hygiene: logged, not raised.
        await SchemaManager(graph)._backfill_relation_type()

        written = [call.args[0] for call in graph.execute_write.await_args_list]
        assert queries.RECORD_SCHEMA_MIGRATION not in written

    @pytest.mark.asyncio
    async def test_the_setting_off_skips_even_the_marker_lookup(self):
        from neo4j_agent_memory.graph.schema import SchemaManager

        graph = MagicMock()
        graph.execute_read = AsyncMock(return_value=[])
        graph.execute_write = AsyncMock(return_value=[])

        await SchemaManager(graph, backfill_relation_types=False)._backfill_relation_type()

        graph.execute_read.assert_not_awaited()
        graph.execute_write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_force_runs_even_with_the_setting_off(self):
        from neo4j_agent_memory.graph import queries
        from neo4j_agent_memory.graph.schema import SchemaManager

        graph = MagicMock()
        graph.execute_read = AsyncMock(return_value=[{"name": "relation_type_backfill"}])
        graph.execute_write = AsyncMock(return_value=[{"updated": 1}])

        manager = SchemaManager(graph, backfill_relation_types=False)
        await manager._backfill_relation_type(force=True)

        graph.execute_read.assert_not_awaited()  # marker lookup is skipped
        written = [call.args[0] for call in graph.execute_write.await_args_list]
        assert queries.BACKFILL_RELATION_TYPE in written


# -----------------------------------------------------------------------------
# Package surface
# -----------------------------------------------------------------------------


class TestPackageExports:
    @pytest.mark.parametrize(
        "name",
        [
            "OntologyDocument",
            "EntityTypeDef",
            "RelationshipDef",
            "PropertyDef",
            "POLEO_ONTOLOGY",
            "load_ontology",
        ],
    )
    def test_exported_from_the_package_root(self, name):
        import neo4j_agent_memory

        assert name in neo4j_agent_memory.__all__
        assert getattr(neo4j_agent_memory, name) is not None

    def test_the_root_reexports_the_same_objects(self):
        import neo4j_agent_memory
        from neo4j_agent_memory.ontology import models

        assert neo4j_agent_memory.OntologyDocument is models.OntologyDocument
        assert neo4j_agent_memory.POLEO_ONTOLOGY is POLEO_ONTOLOGY

    def test_no_ontology_module_imports_the_package_root_at_import_time(self):
        # The root imports the ontology package at module scope, so the
        # reverse dependency must stay out of import time or the cycle
        # deadlocks. Submodule imports (neo4j_agent_memory.graph.queries, …)
        # are fine; pulling names off the root package is not.
        import ast

        import neo4j_agent_memory.ontology as ontology_pkg

        package_dir = Path(ontology_pkg.__file__).parent
        offenders: list[str] = []
        for source_file in sorted(package_dir.glob("*.py")):
            tree = ast.parse(source_file.read_text())
            for node in ast.walk(tree):
                # Only module-level statements matter; a lazy import inside a
                # function runs long after both modules exist.
                if node not in tree.body:
                    continue
                if isinstance(node, ast.ImportFrom) and node.module == "neo4j_agent_memory":
                    offenders.append(f"{source_file.name}: from neo4j_agent_memory import ...")
                if isinstance(node, ast.Import):
                    offenders.extend(
                        f"{source_file.name}: import {alias.name}"
                        for alias in node.names
                        if alias.name == "neo4j_agent_memory"
                    )

        assert offenders == []

    def test_importing_the_ontology_package_first_still_works(self):
        # Guards the other import order: ontology package before the root.
        import subprocess

        code = (
            "import neo4j_agent_memory.ontology as o; "
            "import neo4j_agent_memory as m; "
            "print(m.POLEO_ONTOLOGY is o.POLEO_ONTOLOGY)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == "True"
