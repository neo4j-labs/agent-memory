"""Unit tests for entity extraction."""

import pytest

from neo4j_agent_memory.extraction.base import (
    ENTITY_STOPWORDS,
    ExtractedEntity,
    ExtractedPreference,
    ExtractedRelation,
    ExtractionResult,
    NoOpExtractor,
    is_valid_entity_name,
)


class TestExtractedEntity:
    """Tests for ExtractedEntity model."""

    def test_create_entity(self):
        """Test creating an extracted entity."""
        entity = ExtractedEntity(
            name="John Smith",
            type="PERSON",
            confidence=0.95,
        )

        assert entity.name == "John Smith"
        assert entity.type == "PERSON"
        assert entity.confidence == 0.95

    def test_normalized_name(self):
        """Test normalized name property."""
        entity = ExtractedEntity(
            name="  John Smith  ",
            type="PERSON",
        )

        assert entity.normalized_name == "john smith"

    def test_entity_with_span(self):
        """Test entity with character span."""
        entity = ExtractedEntity(
            name="Acme",
            type="ORGANIZATION",
            start_pos=10,
            end_pos=14,
        )

        assert entity.start_pos == 10
        assert entity.end_pos == 14


class TestExtractedRelation:
    """Tests for ExtractedRelation model."""

    def test_create_relation(self):
        """Test creating an extracted relation."""
        relation = ExtractedRelation(
            source="John",
            target="Acme",
            relation_type="works_at",
        )

        assert relation.source == "John"
        assert relation.target == "Acme"
        assert relation.relation_type == "works_at"

    def test_as_triple(self):
        """Test as_triple property."""
        relation = ExtractedRelation(
            source="Alice",
            target="Bob",
            relation_type="knows",
        )

        assert relation.as_triple == ("Alice", "knows", "Bob")


class TestExtractionResult:
    """Tests for ExtractionResult model."""

    def test_create_result(self):
        """Test creating an extraction result."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="John", type="PERSON"),
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
            ],
            relations=[ExtractedRelation(source="John", target="Acme", relation_type="works_at")],
        )

        assert result.entity_count == 2
        assert result.relation_count == 1

    def test_entities_by_type(self):
        """Test grouping entities by type."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="John", type="PERSON"),
                ExtractedEntity(name="Jane", type="PERSON"),
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
            ]
        )

        by_type = result.entities_by_type()

        assert len(by_type["PERSON"]) == 2
        assert len(by_type["ORGANIZATION"]) == 1

    def test_get_entities_of_type(self):
        """Test getting entities of a specific type."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="John", type="PERSON"),
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
            ]
        )

        people = result.get_entities_of_type("PERSON")
        orgs = result.get_entities_of_type("organization")  # Case insensitive

        assert len(people) == 1
        assert people[0].name == "John"
        assert len(orgs) == 1


class TestNoOpExtractor:
    """Tests for NoOpExtractor."""

    @pytest.mark.asyncio
    async def test_noop_extraction(self):
        """Test that NoOpExtractor returns empty result."""
        extractor = NoOpExtractor()

        result = await extractor.extract("Hello, I'm John from Acme")

        assert result.entity_count == 0
        assert result.relation_count == 0
        assert result.preference_count == 0
        assert result.source_text == "Hello, I'm John from Acme"


class TestEntityStopwords:
    """Tests for entity stopword filtering."""

    def test_stopwords_set_exists(self):
        """Test that ENTITY_STOPWORDS is a non-empty frozenset."""
        assert isinstance(ENTITY_STOPWORDS, frozenset)
        assert len(ENTITY_STOPWORDS) > 0

    def test_common_pronouns_in_stopwords(self):
        """Test that common pronouns are in stopwords."""
        pronouns = ["i", "me", "my", "you", "your", "he", "she", "they", "them", "it", "we"]
        for pronoun in pronouns:
            assert pronoun in ENTITY_STOPWORDS, f"'{pronoun}' should be in stopwords"

    def test_common_verbs_in_stopwords(self):
        """Test that common verbs are in stopwords."""
        verbs = ["is", "are", "was", "were", "be", "been", "have", "has", "do", "does", "did"]
        for verb in verbs:
            assert verb in ENTITY_STOPWORDS, f"'{verb}' should be in stopwords"

    def test_articles_in_stopwords(self):
        """Test that articles are in stopwords."""
        articles = ["a", "an", "the"]
        for article in articles:
            assert article in ENTITY_STOPWORDS, f"'{article}' should be in stopwords"


class TestIsValidEntityName:
    """Tests for is_valid_entity_name function."""

    def test_valid_person_names(self):
        """Test that valid person names are accepted."""
        valid_names = ["John Smith", "Alice", "Bob Johnson", "María García", "李明"]
        for name in valid_names:
            assert is_valid_entity_name(name), f"'{name}' should be valid"

    def test_valid_organization_names(self):
        """Test that valid organization names are accepted."""
        valid_names = ["Acme Corp", "Google", "United Nations", "NASA"]
        for name in valid_names:
            assert is_valid_entity_name(name), f"'{name}' should be valid"

    def test_valid_location_names(self):
        """Test that valid location names are accepted."""
        valid_names = ["New York", "Paris", "Tokyo", "Mount Everest"]
        for name in valid_names:
            assert is_valid_entity_name(name), f"'{name}' should be valid"

    def test_stopwords_rejected(self):
        """Test that stopwords are rejected."""
        invalid_names = ["they", "them", "you", "me", "it", "we", "he", "she"]
        for name in invalid_names:
            assert not is_valid_entity_name(name), f"'{name}' should be invalid (stopword)"

    def test_case_insensitive_stopword_check(self):
        """Test that stopword check is case insensitive."""
        invalid_names = ["They", "THEM", "You", "ME", "It", "WE"]
        for name in invalid_names:
            assert not is_valid_entity_name(name), (
                f"'{name}' should be invalid (stopword, case insensitive)"
            )

    def test_short_names_rejected(self):
        """Test that single-character names are rejected."""
        invalid_names = ["a", "b", "x", "1", "?"]
        for name in invalid_names:
            assert not is_valid_entity_name(name), f"'{name}' should be invalid (too short)"

    def test_numeric_values_rejected(self):
        """Test that purely numeric values are rejected."""
        invalid_names = ["10", "123", "45.67", "100,000", "50%", "12 34"]
        for name in invalid_names:
            assert not is_valid_entity_name(name), f"'{name}' should be invalid (numeric)"

    def test_punctuation_only_rejected(self):
        """Test that punctuation-only strings are rejected."""
        invalid_names = ["...", "---", "!!!", "???", "..."]
        for name in invalid_names:
            assert not is_valid_entity_name(name), f"'{name}' should be invalid (punctuation only)"

    def test_whitespace_handling(self):
        """Test that names with extra whitespace are handled."""
        # Should still be valid after stripping
        assert is_valid_entity_name("  John Smith  ")
        # Empty after stripping should be invalid
        assert not is_valid_entity_name("   ")
        assert not is_valid_entity_name("")

    def test_mixed_alphanumeric_accepted(self):
        """Test that mixed alphanumeric names are accepted."""
        valid_names = ["Apollo 11", "iPhone 15", "Building 42", "Route 66"]
        for name in valid_names:
            assert is_valid_entity_name(name), f"'{name}' should be valid (mixed alphanumeric)"


class TestExtractionResultFilterInvalidEntities:
    """Tests for ExtractionResult.filter_invalid_entities method."""

    def test_filter_removes_stopword_entities(self):
        """Test that filter removes entities with stopword names."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="John Smith", type="PERSON"),
                ExtractedEntity(name="they", type="PERSON"),
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
                ExtractedEntity(name="them", type="PERSON"),
            ]
        )

        filtered = result.filter_invalid_entities()

        assert filtered.entity_count == 2
        entity_names = [e.name for e in filtered.entities]
        assert "John Smith" in entity_names
        assert "Acme" in entity_names
        assert "they" not in entity_names
        assert "them" not in entity_names

    def test_filter_removes_numeric_entities(self):
        """Test that filter removes entities with numeric names."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="John", type="PERSON"),
                ExtractedEntity(name="10", type="OBJECT"),
                ExtractedEntity(name="123.45", type="OBJECT"),
            ]
        )

        filtered = result.filter_invalid_entities()

        assert filtered.entity_count == 1
        assert filtered.entities[0].name == "John"

    def test_filter_preserves_valid_relations(self):
        """Test that filter preserves relations between valid entities."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="John", type="PERSON"),
                ExtractedEntity(name="Acme", type="ORGANIZATION"),
                ExtractedEntity(name="they", type="PERSON"),
            ],
            relations=[
                ExtractedRelation(source="John", target="Acme", relation_type="works_at"),
            ],
        )

        filtered = result.filter_invalid_entities()

        assert filtered.relation_count == 1
        assert filtered.relations[0].source == "John"
        assert filtered.relations[0].target == "Acme"

    def test_filter_removes_relations_with_invalid_entities(self):
        """Test that filter removes relations referencing invalid entities."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="John", type="PERSON"),
                ExtractedEntity(name="they", type="PERSON"),
            ],
            relations=[
                ExtractedRelation(source="they", target="John", relation_type="knows"),
            ],
        )

        filtered = result.filter_invalid_entities()

        # Relation should be removed because "they" is filtered out
        assert filtered.relation_count == 0

    def test_filter_preserves_preferences(self):
        """Test that filter preserves preferences."""
        result = ExtractionResult(
            entities=[
                ExtractedEntity(name="they", type="PERSON"),
            ],
            preferences=[
                ExtractedPreference(
                    category="food",
                    preference="likes coffee",
                ),
            ],
        )

        filtered = result.filter_invalid_entities()

        assert filtered.entity_count == 0
        assert filtered.preference_count == 1

    def test_filter_returns_new_instance(self):
        """Test that filter returns a new ExtractionResult instance."""
        result = ExtractionResult(
            entities=[ExtractedEntity(name="John", type="PERSON")],
        )

        filtered = result.filter_invalid_entities()

        assert filtered is not result
        assert filtered.entities is not result.entities

    def test_filter_empty_result(self):
        """Test filtering an empty result."""
        result = ExtractionResult(entities=[])

        filtered = result.filter_invalid_entities()

        assert filtered.entity_count == 0


class TestDomainSchemas:
    """Tests for the domain catalogs of labelled entity types."""

    def test_domain_schema_model(self):
        """Test DomainSchema model creation."""
        from neo4j_agent_memory.extraction.domain_schemas import DomainSchema

        schema = DomainSchema(
            name="test",
            entity_types={
                "person": "A human individual",
                "company": "A business organization",
            },
        )

        assert schema.name == "test"
        assert len(schema.entity_types) == 2
        assert "person" in schema.entity_types
        assert schema.entity_types["person"] == "A human individual"

    def test_domain_schema_with_relations(self):
        """Test DomainSchema model with relation types."""
        from neo4j_agent_memory.extraction.domain_schemas import DomainSchema

        schema = DomainSchema(
            name="test",
            entity_types={"person": "A person"},
            relation_types={
                "works_at": "Employment relationship",
                "knows": "Personal acquaintance",
            },
        )

        assert len(schema.relation_types) == 2
        assert "works_at" in schema.relation_types

    def test_get_schema_valid(self):
        """Test get_schema with valid schema name."""
        from neo4j_agent_memory.extraction.domain_schemas import get_schema

        schema = get_schema("poleo")

        assert schema.name == "poleo"
        assert "person" in schema.entity_types
        assert "organization" in schema.entity_types
        assert "location" in schema.entity_types
        assert "event" in schema.entity_types
        assert "object" in schema.entity_types

    def test_get_schema_invalid(self):
        """Test get_schema with invalid schema name."""
        from neo4j_agent_memory.extraction.domain_schemas import get_schema

        with pytest.raises(ValueError, match="Unknown schema"):
            get_schema("nonexistent")

    def test_list_schemas(self):
        """Test list_schemas returns all available schemas."""
        from neo4j_agent_memory.extraction.domain_schemas import list_schemas

        schemas = list_schemas()

        assert isinstance(schemas, list)
        assert "poleo" in schemas
        assert "podcast" in schemas
        assert "news" in schemas
        assert "scientific" in schemas
        assert "business" in schemas
        assert "entertainment" in schemas
        assert "medical" in schemas
        assert "legal" in schemas

    def test_podcast_schema_entity_types(self):
        """Test that podcast schema has appropriate entity types."""
        from neo4j_agent_memory.extraction.domain_schemas import get_schema

        schema = get_schema("podcast")

        assert schema.name == "podcast"
        # Check podcast-specific entity types
        assert "person" in schema.entity_types
        assert "company" in schema.entity_types
        assert "product" in schema.entity_types
        assert "concept" in schema.entity_types
        assert "book" in schema.entity_types
        assert "technology" in schema.entity_types

    def test_all_schemas_have_descriptions(self):
        """Test that all entity types have descriptions."""
        from neo4j_agent_memory.extraction.domain_schemas import DOMAIN_SCHEMAS

        for name, schema in DOMAIN_SCHEMAS.items():
            for entity_type, description in schema.entity_types.items():
                assert isinstance(description, str), (
                    f"{name}/{entity_type} should have string description"
                )
                assert len(description) > 10, (
                    f"{name}/{entity_type} description should be meaningful"
                )

    def test_schemas_are_reachable_from_the_package(self):
        """The catalog is re-exported from ``neo4j_agent_memory.extraction``."""
        from neo4j_agent_memory.extraction import DOMAIN_SCHEMAS, DomainSchema, get_schema

        assert isinstance(get_schema("news"), DomainSchema)
        assert set(DOMAIN_SCHEMAS) >= {"poleo", "news"}


class TestDomainSchemaToOntology:
    """``DomainSchema.to_ontology()`` — the bridge to the extraction schema."""

    def test_labels_become_typed_entity_types(self):
        from neo4j_agent_memory.extraction.domain_schemas import get_schema

        doc = get_schema("podcast").to_ontology()

        assert doc.domain.name == "podcast"
        assert doc.labels() == list(get_schema("podcast").entity_types)
        assert doc.label_map()["company"] == ("ORGANIZATION", "COMPANY")
        assert doc.validate_structure() == []

    def test_descriptions_survive_as_annotation_guidelines(self):
        from neo4j_agent_memory.extraction.domain_schemas import get_schema

        schema = get_schema("medical")
        doc = schema.to_ontology()

        by_label = {et.label: et for et in doc.entity_types}
        assert by_label["disease"].description == schema.entity_types["disease"]

    def test_catalog_alone_declares_no_relationships(self):
        from neo4j_agent_memory.extraction.domain_schemas import get_schema

        assert get_schema("legal").to_ontology().relationships == []

    def test_supplied_relationships_pick_up_catalog_descriptions(self):
        from neo4j_agent_memory.extraction.domain_schemas import DomainSchema
        from neo4j_agent_memory.ontology.models import RelationshipDef

        schema = DomainSchema(
            name="tiny",
            entity_types={"person": "A person", "company": "A company"},
            relation_types={"WORKS_AT": "Employment."},
        )

        doc = schema.to_ontology(
            relationships=[RelationshipDef(type="WORKS_AT", source="person", target="company")]
        )

        assert doc.relationships[0].description == "Employment."
        assert doc.patterns() == {("person", "WORKS_AT", "company")}
        assert doc.validate_structure() == []

    def test_all_eight_catalogs_convert_cleanly(self):
        from neo4j_agent_memory.extraction.domain_schemas import DOMAIN_SCHEMAS

        for name, schema in DOMAIN_SCHEMAS.items():
            doc = schema.to_ontology()
            assert doc.validate_structure() == [], f"{name} does not validate"


class TestRemovedNames:
    """The GLiNER v1 / GLiREL names removed in 0.7 explain themselves."""

    @pytest.mark.parametrize(
        "name",
        [
            "GLiNEREntityExtractor",
            "GLiNERConfig",
            "GLiNERWithRelationsExtractor",
            "GLiRELExtractor",
            "GLiRELConfig",
            "DEFAULT_RELATION_TYPES",
            "is_gliner_available",
            "is_glirel_available",
        ],
    )
    def test_removed_name_raises_import_error(self, name):
        import neo4j_agent_memory.extraction as extraction

        with pytest.raises(ImportError, match="removed in 0.7"):
            getattr(extraction, name)

    def test_the_package_still_imports_cleanly(self):
        """Importing the module must not depend on the removed-name machinery.

        ``RemovedExtractorError`` is created at import time, so a base-class
        combination CPython rejects (``ImportError`` + ``AttributeError``:
        "multiple bases have instance lay-out conflict") would fail the whole
        package import, not just the removed names.
        """
        import subprocess
        import sys

        # A subprocess, so the check is a real cold import and cannot perturb
        # the modules the rest of the suite already holds.
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import neo4j_agent_memory.extraction as e; "
                "print(e.RemovedExtractorError.__mro__[1].__name__)",
            ],
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ImportError"

    @pytest.mark.parametrize("name", ["is_gliner_available", "is_glirel_available"])
    def test_hasattr_on_a_removed_name_raises_by_design(self, name):
        """``hasattr`` cannot answer ``False`` here, and that is deliberate.

        The migration hint only survives the ``from ... import`` path as an
        ``ImportError``, and CPython will not let one class be both that and an
        ``AttributeError``. Probe with ``is_gliner2_available()`` or
        ``try/except ImportError`` instead.
        """
        import neo4j_agent_memory.extraction as extraction
        from neo4j_agent_memory.extraction import RemovedExtractorError

        assert issubclass(RemovedExtractorError, ImportError)
        with pytest.raises(ImportError, match="removed in 0.7"):
            hasattr(extraction, name)

    def test_from_import_keeps_the_migration_message(self):
        with pytest.raises(ImportError, match="use is_gliner2_available"):
            exec("from neo4j_agent_memory.extraction import is_gliner_available")

    def test_unknown_name_still_raises_attribute_error(self):
        import neo4j_agent_memory.extraction as extraction

        name = "NotAThing"
        with pytest.raises(AttributeError):
            getattr(extraction, name)

    def test_gliner_extractor_module_is_gone(self):
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("neo4j_agent_memory.extraction.gliner_extractor")


class TestLLMExtractorOntologyPrompt:
    """The LLM stage's prompt is derived from the ontology when one is set."""

    @staticmethod
    def _ontology():
        from neo4j_agent_memory.ontology.models import (
            DomainInfo,
            EntityTypeDef,
            OntologyDocument,
            RelationshipDef,
        )

        return OntologyDocument(
            domain=DomainInfo(id="support", name="support"),
            entity_types=[
                EntityTypeDef(
                    label="Customer",
                    pole_type="PERSON",
                    subtype="INDIVIDUAL",
                    description="A person who reported a ticket. Not a support engineer.",
                ),
                EntityTypeDef(
                    label="Vendor",
                    pole_type="ORGANIZATION",
                    subtype="COMPANY",
                    description="A company that sells support.",
                ),
            ],
            relationships=[
                RelationshipDef(
                    type="BUYS_FROM",
                    source="Customer",
                    target="Vendor",
                    description="A customer purchases from a vendor.",
                    unique_source=True,
                ),
            ],
        )

    @staticmethod
    def _extractor(**kwargs):
        from unittest.mock import MagicMock

        from neo4j_agent_memory.extraction.llm_extractor import LLMEntityExtractor

        # A MagicMock provider skips provider construction entirely; the
        # prompt is built without any network call.
        return LLMEntityExtractor(provider=MagicMock(), **kwargs)

    def test_the_prompt_carries_the_guidelines_and_the_relationship_catalogue(self):
        extractor = self._extractor(ontology=self._ontology())

        prompt = extractor._build_prompt("Ada bought support from Acme.", ["PERSON"])

        assert "## Entity types" in prompt
        assert "Customer (PERSON:INDIVIDUAL)" in prompt
        assert "Not a support engineer." in prompt
        assert "## Relationship types" in prompt
        assert "BUYS_FROM: Customer -> Vendor" in prompt
        assert "at most one per source" in prompt
        # And the text under analysis is still interpolated.
        assert "Ada bought support from Acme." in prompt

    def test_relations_are_omitted_when_relation_extraction_is_off(self):
        extractor = self._extractor(ontology=self._ontology(), extract_relations=False)

        prompt = extractor._build_prompt("text", ["PERSON"])

        assert "Customer (PERSON:INDIVIDUAL)" in prompt
        assert "BUYS_FROM" not in prompt

    def test_entity_types_and_subtypes_come_from_the_ontology(self):
        extractor = self._extractor(ontology=self._ontology())

        assert extractor._entity_types == ["PERSON", "ORGANIZATION"]
        assert extractor._subtypes == {
            "PERSON": ["INDIVIDUAL"],
            "ORGANIZATION": ["COMPANY"],
        }

    def test_explicit_entity_types_still_win(self):
        extractor = self._extractor(ontology=self._ontology(), entity_types=["EVENT"])
        assert extractor._entity_types == ["EVENT"]

    def test_without_an_ontology_the_prompt_is_unchanged(self):
        from neo4j_agent_memory.extraction.llm_extractor import POLEO_SUBTYPES

        extractor = self._extractor()

        prompt = extractor._build_prompt("text", ["PERSON", "OBJECT"])

        assert extractor._ontology is None
        assert extractor._subtypes == POLEO_SUBTYPES
        assert "## Entity types" not in prompt
        assert "Subtypes (optional" in prompt
        assert "PERSON: INDIVIDUAL, ALIAS, PERSONA" in prompt
