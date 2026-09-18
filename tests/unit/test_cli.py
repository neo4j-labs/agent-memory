"""Unit tests for CLI module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip entire module if click is not installed
click = pytest.importorskip("click", reason="click not installed")
rich = pytest.importorskip("rich", reason="rich not installed")

from click.testing import CliRunner

from neo4j_agent_memory.cli.main import (
    cli,
    format_entities_table,
    format_preferences_table,
    format_relations_table,
    result_to_dict,
)
from neo4j_agent_memory.extraction import (
    ExtractedEntity,
    ExtractedPreference,
    ExtractedRelation,
    ExtractionResult,
)


@pytest.fixture
def runner(monkeypatch):
    """Create a CLI test runner with a deterministic environment.

    Clears NEO4J_* env vars so the `--password` and `--uri` Click options
    don't pick up the developer's local environment via `envvar=`. Without
    this, the `*_no_password` tests fail when run by anyone who has
    NEO4J_PASSWORD set in their shell.
    """
    for var in ("NEO4J_PASSWORD", "NEO4J_USER", "NEO4J_USERNAME", "NEO4J_URI"):
        monkeypatch.delenv(var, raising=False)
    return CliRunner()


@pytest.fixture
def sample_extraction_result():
    """Create a sample extraction result for testing."""
    return ExtractionResult(
        entities=[
            ExtractedEntity(
                name="John Smith",
                type="Person",
                confidence=0.95,
                attributes={"occupation": "engineer"},
            ),
            ExtractedEntity(
                name="Acme Corp",
                type="Organization",
                confidence=0.88,
                attributes={"industry": "technology"},
            ),
        ],
        relations=[
            ExtractedRelation(
                source="John Smith",
                target="Acme Corp",
                relation_type="WORKS_AT",
                confidence=0.85,
            ),
        ],
        preferences=[
            ExtractedPreference(
                category="programming_language",
                preference="Prefers Python",
                confidence=0.9,
            ),
        ],
    )


class TestFormatters:
    """Tests for output formatting functions."""

    def test_format_entities_table(self, sample_extraction_result):
        """Test entity table formatting."""
        table = format_entities_table(sample_extraction_result)
        assert table.title == "Extracted Entities"
        assert len(table.columns) == 4
        assert table.columns[0].header == "Type"
        assert table.columns[1].header == "Name"
        assert table.columns[2].header == "Confidence"
        assert table.columns[3].header == "Attributes"

    def test_format_relations_table(self, sample_extraction_result):
        """Test relations table formatting."""
        table = format_relations_table(sample_extraction_result)
        assert table.title == "Extracted Relations"
        assert len(table.columns) == 4
        assert table.columns[0].header == "Source"
        assert table.columns[1].header == "Relation"
        assert table.columns[2].header == "Target"
        assert table.columns[3].header == "Confidence"

    def test_format_preferences_table(self, sample_extraction_result):
        """Test preferences table formatting."""
        table = format_preferences_table(sample_extraction_result)
        assert table.title == "Extracted Preferences"
        assert len(table.columns) == 3
        assert table.columns[0].header == "Category"
        assert table.columns[1].header == "Preference"
        assert table.columns[2].header == "Confidence"

    def test_result_to_dict(self, sample_extraction_result):
        """Test conversion to dictionary."""
        result_dict = result_to_dict(sample_extraction_result)

        assert "entities" in result_dict
        assert "relations" in result_dict
        assert "preferences" in result_dict
        assert "source_text" in result_dict

        assert len(result_dict["entities"]) == 2
        assert result_dict["entities"][0]["name"] == "John Smith"
        assert result_dict["entities"][0]["type"] == "Person"
        assert result_dict["entities"][0]["confidence"] == 0.95

        assert len(result_dict["relations"]) == 1
        assert result_dict["relations"][0]["source"] == "John Smith"
        assert result_dict["relations"][0]["relation_type"] == "WORKS_AT"

        assert len(result_dict["preferences"]) == 1
        assert result_dict["preferences"][0]["category"] == "programming_language"

    def test_result_to_dict_empty(self):
        """Test conversion of empty result."""
        empty_result = ExtractionResult(
            entities=[],
            relations=[],
            preferences=[],
        )
        result_dict = result_to_dict(empty_result)

        assert result_dict["entities"] == []
        assert result_dict["relations"] == []
        assert result_dict["preferences"] == []


class TestCLIHelp:
    """Tests for CLI help and version commands."""

    def test_cli_help(self, runner):
        """Test CLI help command."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Neo4j Agent Memory" in result.output
        assert "extract" in result.output
        assert "schemas" in result.output
        assert "stats" in result.output

    def test_extract_help(self, runner):
        """Test extract command help."""
        result = runner.invoke(cli, ["extract", "--help"])
        assert result.exit_code == 0
        assert "Extract entities from text" in result.output
        assert "--format" in result.output
        assert "--schema" in result.output
        assert "--ontology" in result.output
        assert "--gliner-schema" in result.output
        assert "--extractor" in result.output
        assert "fastino/gliner2.5-base-v1" in result.output

    def test_schemas_help(self, runner):
        """Test schemas command help."""
        result = runner.invoke(cli, ["schemas", "--help"])
        assert result.exit_code == 0
        assert "Manage extraction schemas" in result.output

    def test_schemas_list_help(self, runner):
        """Test schemas list command help."""
        result = runner.invoke(cli, ["schemas", "list", "--help"])
        assert result.exit_code == 0
        assert "--uri" in result.output
        assert "--password" in result.output

    def test_stats_help(self, runner):
        """Test stats command help."""
        result = runner.invoke(cli, ["stats", "--help"])
        assert result.exit_code == 0
        assert "extraction statistics" in result.output


class TestExtractCommand:
    """Tests for the extract command."""

    def test_extract_no_text_error(self, runner):
        """Test error when no text is provided."""
        result = runner.invoke(cli, ["extract"])
        assert result.exit_code == 1
        # In test environment, stdin may appear as tty so we get "No text" or it reads empty
        assert "No text provided" in result.output or "Empty text provided" in result.output

    def test_extract_empty_text_error(self, runner):
        """Test error when empty text is provided."""
        result = runner.invoke(cli, ["extract", "   "])
        assert result.exit_code == 1
        assert "Empty text provided" in result.output

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_with_text_json_format(
        self, mock_builder_class, runner, sample_extraction_result
    ):
        """Test extraction with JSON output."""
        # Setup mock
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "--format",
                "json",
                "--quiet",
            ],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert "entities" in output
        assert len(output["entities"]) == 2

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_with_text_jsonl_format(
        self, mock_builder_class, runner, sample_extraction_result
    ):
        """Test extraction with JSONL output."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "--format",
                "jsonl",
            ],
        )

        assert result.exit_code == 0
        lines = [line for line in result.output.strip().split("\n") if line]
        # 2 entities + 1 relation + 1 preference = 4 lines
        assert len(lines) == 4

        # Check first line is an entity
        first_line = json.loads(lines[0])
        assert first_line["type"] == "entity"

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_with_entity_types(self, mock_builder_class, runner, sample_extraction_result):
        """Test extraction with specific entity types."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_schema.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "-e",
                "Person",
                "-e",
                "Organization",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        mock_builder.with_schema.assert_called_once()

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_with_llm_extractor(self, mock_builder_class, runner, sample_extraction_result):
        """Test extraction with LLM extractor."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_llm.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "--extractor",
                "llm",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        mock_builder.with_llm.assert_called()

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_with_hybrid_extractor(
        self, mock_builder_class, runner, sample_extraction_result
    ):
        """Test extraction with hybrid extractor."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_llm.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "--extractor",
                "hybrid",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        mock_builder.with_gliner.assert_called()
        mock_builder.with_llm.assert_called()

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_with_custom_model(self, mock_builder_class, runner, sample_extraction_result):
        """Test extraction with custom model name."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "--model",
                "custom-gliner-model",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        mock_builder.with_gliner.assert_called_with(model_name="custom-gliner-model")

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_no_relations(self, mock_builder_class, runner, sample_extraction_result):
        """Test extraction without relations."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "--no-relations",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        mock_extractor.extract.assert_called_once()
        call_kwargs = mock_extractor.extract.call_args[1]
        assert call_kwargs["extract_relations"] is False

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_with_preferences(self, mock_builder_class, runner, sample_extraction_result):
        """Test extraction with preferences enabled."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "--preferences",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        call_kwargs = mock_extractor.extract.call_args[1]
        assert call_kwargs["extract_preferences"] is True

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_with_confidence_threshold(
        self, mock_builder_class, runner, sample_extraction_result
    ):
        """Test extraction with custom confidence threshold."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "--confidence-threshold",
                "0.8",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        mock_builder.with_confidence_threshold.assert_called_with(0.8)

    def test_extract_from_stdin(self, runner):
        """Test extraction from stdin."""
        with patch("neo4j_agent_memory.cli.main.ExtractorBuilder") as mock_builder_class:
            mock_builder = MagicMock()
            mock_builder_class.return_value = mock_builder
            mock_builder.with_gliner.return_value = mock_builder
            mock_builder.with_confidence_threshold.return_value = mock_builder
            mock_builder.extract_relations.return_value = mock_builder

            mock_extractor = MagicMock()
            mock_builder.build.return_value = mock_extractor
            mock_extractor.extract = AsyncMock(
                return_value=ExtractionResult(
                    entities=[], relations=[], preferences=[], metadata={}
                )
            )

            result = runner.invoke(cli, ["extract", "-", "--format", "json"], input="Hello world")
            assert result.exit_code == 0


class TestSchemasCommand:
    """Tests for the schemas command."""

    def test_schemas_list_no_password(self, runner):
        """Test schemas list without password."""
        result = runner.invoke(cli, ["schemas", "list"])
        assert result.exit_code == 1
        assert "password required" in result.output

    @patch("neo4j_agent_memory.graph.client.AsyncGraphDatabase")
    @patch("neo4j_agent_memory.schema.SchemaManager")
    def test_schemas_list_json(self, mock_manager_class, mock_driver_class, runner):
        """Test schemas list with JSON output."""

        from neo4j_agent_memory.schema import SchemaListItem

        # Patch path follows the CLI's new dependency: schemas_list constructs a
        # Neo4jClient internally, and Neo4jClient.connect() calls
        # AsyncGraphDatabase.driver() and verify_connectivity().
        mock_driver = MagicMock()
        mock_driver_class.driver.return_value = mock_driver
        mock_driver.close = AsyncMock()
        mock_driver.verify_connectivity = AsyncMock()

        mock_manager = MagicMock()
        mock_manager_class.return_value = mock_manager
        mock_manager.list_schemas = AsyncMock(
            return_value=[
                SchemaListItem(
                    name="test_schema",
                    latest_version="1.0",
                    description="Test schema",
                    version_count=1,
                    is_active=True,
                ),
            ]
        )

        result = runner.invoke(
            cli,
            [
                "schemas",
                "list",
                "--password",
                "test",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output) == 1
        assert output[0]["name"] == "test_schema"

    def test_schemas_show_no_password(self, runner):
        """Test schemas show without password."""
        result = runner.invoke(cli, ["schemas", "show", "test"])
        assert result.exit_code == 1
        assert "password required" in result.output

    def test_schemas_validate_valid_file(self, runner, tmp_path):
        """Test schema validation with valid file."""
        schema_file = tmp_path / "schema.yaml"
        schema_file.write_text("""
name: test_schema
entity_types:
  - name: Person
    description: A person
  - name: Organization
    description: An organization
""")

        result = runner.invoke(cli, ["schemas", "validate", str(schema_file)])
        assert result.exit_code == 0
        assert "is valid" in result.output

    def test_schemas_validate_invalid_file(self, runner, tmp_path):
        """Test schema validation with invalid file."""
        schema_file = tmp_path / "schema.yaml"
        schema_file.write_text("""
invalid: yaml
  content: here
    broken: true
""")

        result = runner.invoke(cli, ["schemas", "validate", str(schema_file)])
        assert result.exit_code == 1
        assert "Invalid schema" in result.output


class TestStatsCommand:
    """Tests for the stats command."""

    def test_stats_no_password(self, runner):
        """Test stats without password."""
        result = runner.invoke(cli, ["stats"])
        assert result.exit_code == 1
        assert "password required" in result.output

    @patch("neo4j_agent_memory.graph.client.AsyncGraphDatabase")
    @patch("neo4j_agent_memory.memory.LongTermMemory")
    def test_stats_json_output(self, mock_memory_class, mock_driver_class, runner):
        """Test stats with JSON output."""
        # Patch path follows the CLI's new dependency: stats constructs a
        # Neo4jClient internally, and Neo4jClient.connect() calls
        # AsyncGraphDatabase.driver() and verify_connectivity().
        mock_driver = MagicMock()
        mock_driver_class.driver.return_value = mock_driver
        mock_driver.close = AsyncMock()
        mock_driver.verify_connectivity = AsyncMock()

        mock_memory = MagicMock()
        mock_memory_class.return_value = mock_memory
        mock_memory.get_extraction_stats = AsyncMock(
            return_value={
                "total_entities": 100,
                "entities_with_provenance": 80,
                "total_extractors": 2,
                "entity_types": {"Person": 60, "Organization": 40},
            }
        )
        mock_memory.get_extractor_stats = AsyncMock(
            return_value=[
                {"name": "gliner", "version": "1.0", "entity_count": 60},
                {"name": "llm", "version": None, "entity_count": 40},
            ]
        )

        result = runner.invoke(
            cli,
            [
                "stats",
                "--password",
                "test",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["extraction_stats"]["total_entities"] == 100
        assert len(output["extractor_stats"]) == 2


class TestTableOutput:
    """Tests for table output formatting."""

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_table_output(self, mock_builder_class, runner, sample_extraction_result):
        """Test extraction with table output."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)

        result = runner.invoke(
            cli,
            [
                "extract",
                "John works at Acme Corp",
                "--format",
                "table",
                "--quiet",
            ],
        )

        assert result.exit_code == 0
        assert "Extracted Entities" in result.output
        assert "John Smith" in result.output
        assert "Acme Corp" in result.output

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_extract_empty_result_table(self, mock_builder_class, runner):
        """Test table output with no entities."""
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        mock_builder.with_gliner.return_value = mock_builder
        mock_builder.with_confidence_threshold.return_value = mock_builder
        mock_builder.extract_relations.return_value = mock_builder

        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(
            return_value=ExtractionResult(entities=[], relations=[], preferences=[], metadata={})
        )

        result = runner.invoke(
            cli,
            [
                "extract",
                "No entities here",
                "--format",
                "table",
                "--quiet",
            ],
        )

        assert result.exit_code == 0
        assert "No entities extracted" in result.output


class TestExtractGLiNER2Flags:
    """The GLiNER2.5-era flags on ``extract``."""

    def _builder(self, mock_builder_class, sample_extraction_result):
        from unittest.mock import AsyncMock, MagicMock

        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder
        for method in (
            "with_gliner",
            "with_gliner_schema",
            "with_ontology",
            "with_schema",
            "with_llm",
            "with_confidence_threshold",
            "extract_relations",
        ):
            getattr(mock_builder, method).return_value = mock_builder
        mock_extractor = MagicMock()
        mock_builder.build.return_value = mock_extractor
        mock_extractor.extract = AsyncMock(return_value=sample_extraction_result)
        return mock_builder

    def test_gliner_schema_choice_is_validated(self, runner):
        result = runner.invoke(cli, ["extract", "text", "--gliner-schema", "nonexistent"])
        assert result.exit_code == 2
        assert "nonexistent" in result.output

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_gliner_schema_selects_the_template(
        self, mock_builder_class, runner, sample_extraction_result
    ):
        mock_builder = self._builder(mock_builder_class, sample_extraction_result)

        result = runner.invoke(
            cli, ["extract", "text", "--gliner-schema", "podcast", "--quiet", "--format", "json"]
        )

        assert result.exit_code == 0
        mock_builder.with_gliner_schema.assert_called_once_with("podcast", model=None)
        mock_builder.with_gliner.assert_not_called()

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_ontology_path_is_loaded(
        self, mock_builder_class, runner, sample_extraction_result, tmp_path
    ):
        from neo4j_agent_memory.ontology import POLEO_ONTOLOGY

        mock_builder = self._builder(mock_builder_class, sample_extraction_result)
        path = tmp_path / "ontology.json"
        path.write_text(POLEO_ONTOLOGY.model_dump_json())

        result = runner.invoke(
            cli, ["extract", "text", "--ontology", str(path), "--quiet", "--format", "json"]
        )

        assert result.exit_code == 0
        mock_builder.with_ontology.assert_called_once()
        doc = mock_builder.with_ontology.call_args[0][0]
        assert doc.labels() == POLEO_ONTOLOGY.labels()
        mock_builder.with_schema.assert_not_called()

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_no_relations_is_threaded_to_the_builder(
        self, mock_builder_class, runner, sample_extraction_result
    ):
        mock_builder = self._builder(mock_builder_class, sample_extraction_result)

        result = runner.invoke(
            cli, ["extract", "text", "--no-relations", "--quiet", "--format", "json"]
        )

        assert result.exit_code == 0
        mock_builder.extract_relations.assert_called_once_with(False)

    @patch("neo4j_agent_memory.cli.main.ExtractorBuilder")
    def test_llm_extractor_with_an_ontology_stays_llm_only(
        self, mock_builder_class, runner, sample_extraction_result, tmp_path
    ):
        """``--extractor llm --ontology x.yaml`` must not build a GLiNER stage.

        The ontology says *what* to extract; ``--extractor`` says how. While
        ``with_ontology()`` also flipped the GLiNER2.5 stage on, this command
        line quietly downloaded and ran a 407 MB checkpoint.
        """
        from neo4j_agent_memory.ontology import POLEO_ONTOLOGY

        mock_builder = self._builder(mock_builder_class, sample_extraction_result)
        path = tmp_path / "ontology.json"
        path.write_text(POLEO_ONTOLOGY.model_dump_json())

        result = runner.invoke(
            cli,
            [
                "extract",
                "text",
                "--extractor",
                "llm",
                "--ontology",
                str(path),
                "--quiet",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0
        mock_builder.with_ontology.assert_called_once()
        mock_builder.with_llm.assert_called_once()
        mock_builder.with_gliner.assert_not_called()
        mock_builder.with_gliner_schema.assert_not_called()

    def test_llm_extractor_with_an_ontology_builds_no_gliner_stage(
        self, runner, tmp_path, llm_extractor_stub
    ):
        """The same thing against the real builder, not a mock chain."""
        from neo4j_agent_memory.extraction.factory import ExtractorBuilder
        from neo4j_agent_memory.ontology import POLEO_ONTOLOGY, load_ontology

        path = tmp_path / "ontology.json"
        path.write_text(POLEO_ONTOLOGY.model_dump_json())

        ontology = load_ontology(path)
        builder = ExtractorBuilder().with_ontology(ontology).with_llm()
        extractor = builder.build()

        assert builder._enable_gliner is False
        # One stage, and it is the LLM one: build() returns the lone stage
        # rather than a pipeline, so a GLiNER2.5 stage would have made this a
        # pipeline of two.
        assert isinstance(extractor, llm_extractor_stub)
        assert llm_extractor_stub.instances == [extractor]
        assert extractor.ontology is ontology


class TestOntologyCommand:
    """Tests for the `ontology validate` / `ontology compile` commands."""

    VALID = {
        "domain": {"id": "support", "name": "support"},
        "entity_types": [
            {
                "label": "Customer",
                "pole_type": "PERSON",
                "subtype": "INDIVIDUAL",
                "description": "A person who reported a ticket.",
            },
            {"label": "Vendor", "pole_type": "ORGANIZATION"},
        ],
        "relationships": [
            {"type": "BUYS_FROM", "source": "Customer", "target": "Vendor"},
        ],
    }

    def _write(self, tmp_path, payload, name="ontology.json"):
        path = tmp_path / name
        path.write_text(json.dumps(payload))
        return path

    def test_ontology_help(self, runner):
        result = runner.invoke(cli, ["ontology", "--help"])
        assert result.exit_code == 0
        assert "validate" in result.output
        assert "compile" in result.output

    def test_registered_on_the_root_group(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "ontology" in result.output

    def test_validate_sound_document(self, runner, tmp_path):
        path = self._write(tmp_path, self.VALID)

        result = runner.invoke(cli, ["ontology", "validate", str(path)])

        assert result.exit_code == 0
        assert "is valid" in result.output
        assert "Customer" in result.output
        assert "BUYS_FROM" in result.output

    def test_validate_reports_every_problem_and_exits_one(self, runner, tmp_path):
        broken = {
            "domain": {"id": "broken", "name": "broken"},
            "entity_types": [{"label": "Customer", "pole_type": "HUMAN"}],
            "relationships": [
                {"type": "BUYS_FROM", "source": "Customer", "target": "Nowhere"},
            ],
        }
        path = self._write(tmp_path, broken, "broken.json")

        result = runner.invoke(cli, ["ontology", "validate", str(path)])

        assert result.exit_code == 1
        assert "problem" in result.output
        assert "HUMAN" in result.output
        assert "Nowhere" in result.output

    def test_validate_accepts_a_legacy_entity_schema_file(self, runner, tmp_path):
        # An EntitySchemaConfig file is converted on the way in; the POLE+O
        # relation types it carries need all five types declared to resolve.
        path = self._write(
            tmp_path,
            {
                "name": "legacy",
                "entity_types": [
                    {"name": name}
                    for name in ("PERSON", "ORGANIZATION", "LOCATION", "EVENT", "OBJECT")
                ],
            },
            "legacy.json",
        )

        result = runner.invoke(cli, ["ontology", "validate", str(path)])

        assert result.exit_code == 0
        assert "is valid" in result.output
        assert "PERSON" in result.output

    def test_validate_reports_an_unreadable_file(self, runner, tmp_path):
        path = tmp_path / "ontology.txt"
        path.write_text("not an ontology")

        result = runner.invoke(cli, ["ontology", "validate", str(path)])

        assert result.exit_code == 1
        assert "Could not load ontology" in result.output

    def test_validate_missing_file(self, runner, tmp_path):
        result = runner.invoke(cli, ["ontology", "validate", str(tmp_path / "nope.json")])
        assert result.exit_code != 0

    def test_compile_prints_the_joint_schema_json(self, runner, tmp_path, monkeypatch):
        # A stand-in JointSchema with a to_dict(), installed as gliner2 so the
        # command runs without the optional dependency.
        import sys
        import types

        class FakeJointSchema:
            def __init__(self):
                self.payload = {"entities": [], "relations": []}

            def entity(self, name, description=None, **kwargs):
                self.payload["entities"].append(name)
                return self

            def relation(self, name, head, tail, description=None, **kwargs):
                self.payload["relations"].append({"type": name, "head": head, "tail": tail})
                return self

            def no_self_loops(self, relation=None):
                return self

            def to_dict(self):
                return self.payload

        module = types.ModuleType("gliner2")
        joint_ie = types.ModuleType("gliner2.joint_ie")
        joint_ie.JointSchema = FakeJointSchema
        module.joint_ie = joint_ie
        monkeypatch.setitem(sys.modules, "gliner2", module)
        monkeypatch.setitem(sys.modules, "gliner2.joint_ie", joint_ie)

        path = self._write(tmp_path, self.VALID)

        result = runner.invoke(cli, ["ontology", "compile", str(path)])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["entities"] == ["Customer", "Vendor"]
        assert payload["relations"] == [
            {"type": "BUYS_FROM", "head": ["Customer"], "tail": ["Vendor"]}
        ]

    def test_compile_prints_an_install_hint_without_gliner2(self, runner, tmp_path, monkeypatch):
        import importlib

        real_import_module = importlib.import_module

        def fake_import_module(name, *args, **kwargs):
            if name.startswith("gliner2"):
                raise ImportError("No module named 'gliner2'")
            return real_import_module(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", fake_import_module)

        path = self._write(tmp_path, self.VALID)

        result = runner.invoke(cli, ["ontology", "compile", str(path)])

        assert result.exit_code == 1
        assert "gliner2 is not installed" in result.output
        assert "neo4j-agent-memory[gliner2]" in result.output

    def test_compile_rejects_a_broken_document(self, runner, tmp_path):
        broken = {
            "domain": {"id": "broken", "name": "broken"},
            "entity_types": [{"label": "Customer", "pole_type": "PERSON"}],
            "relationships": [
                {"type": "BUYS_FROM", "source": "Customer", "target": "Nowhere"},
            ],
        }
        path = self._write(tmp_path, broken, "broken.json")

        result = runner.invoke(cli, ["ontology", "compile", str(path)])

        assert result.exit_code == 1
        assert "Could not compile ontology" in result.output
