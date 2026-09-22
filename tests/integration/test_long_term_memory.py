"""Comprehensive integration tests for long-term memory."""

from datetime import datetime

import pytest

from neo4j_agent_memory.memory.long_term import EntityType


@pytest.mark.integration
class TestLongTermMemoryEntities:
    """Test entity operations in long-term memory."""

    @pytest.mark.asyncio
    async def test_add_entity_basic(self, memory_client):
        """Test adding a basic entity."""
        entity, dedup_result = await memory_client.long_term.add_entity(
            name="John Smith",
            entity_type=EntityType.PERSON,
            description="A test person",
            resolve=False,
            generate_embedding=False,
        )

        assert entity is not None
        assert entity.name == "John Smith"
        assert entity.type == EntityType.PERSON
        assert entity.description == "A test person"
        assert entity.id is not None
        assert dedup_result is not None

    @pytest.mark.asyncio
    async def test_add_entity_all_types(self, memory_client):
        """Test adding entities of all supported types."""
        entity_types = [
            EntityType.PERSON,
            EntityType.ORGANIZATION,
            EntityType.LOCATION,
            EntityType.EVENT,
            EntityType.CONCEPT,
        ]

        for etype in entity_types:
            entity, _ = await memory_client.long_term.add_entity(
                name=f"Test {etype.value}",
                entity_type=etype,
                resolve=False,
                generate_embedding=False,
            )
            assert entity.type == etype

    @pytest.mark.asyncio
    async def test_add_entity_with_embedding(self, memory_client):
        """Test adding an entity with embedding generation."""
        entity, _ = await memory_client.long_term.add_entity(
            name="Google Inc",
            entity_type=EntityType.ORGANIZATION,
            description="A major technology company",
            resolve=False,
            generate_embedding=True,
        )

        assert entity.embedding is not None
        assert len(entity.embedding) > 0

    @pytest.mark.asyncio
    async def test_search_entities_basic(self, memory_client):
        """Test basic entity search."""
        # Add some entities
        await memory_client.long_term.add_entity(
            name="Apple Inc",
            entity_type=EntityType.ORGANIZATION,
            description="Technology company making iPhones",
            resolve=False,
            generate_embedding=True,
        )
        await memory_client.long_term.add_entity(
            name="Microsoft",
            entity_type=EntityType.ORGANIZATION,
            description="Software company making Windows",
            resolve=False,
            generate_embedding=True,
        )
        await memory_client.long_term.add_entity(
            name="Central Park",
            entity_type=EntityType.LOCATION,
            description="Famous park in New York City",
            resolve=False,
            generate_embedding=True,
        )

        # Search for tech companies
        results = await memory_client.long_term.search_entities(
            "technology companies",
            limit=10,
        )

        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_get_entity_by_name(self, memory_client):
        """Test retrieving an entity by name."""
        # Add entity
        await memory_client.long_term.add_entity(
            name="TestEntity123",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        # Retrieve by name
        entity = await memory_client.long_term.get_entity_by_name("TestEntity123")

        assert entity is not None
        assert entity.name == "TestEntity123"

    @pytest.mark.asyncio
    async def test_get_nonexistent_entity(self, memory_client):
        """Test retrieving a non-existent entity."""
        entity = await memory_client.long_term.get_entity_by_name("NonExistentEntity12345")

        assert entity is None


@pytest.mark.integration
class TestLongTermMemoryPreferences:
    """Test preference operations in long-term memory."""

    @pytest.mark.asyncio
    async def test_add_preference_basic(self, memory_client):
        """Test adding a basic preference."""
        pref = await memory_client.long_term.add_preference(
            category="food",
            preference="I love spicy Thai food",
            generate_embedding=False,
        )

        assert pref is not None
        assert pref.category == "food"
        assert pref.preference == "I love spicy Thai food"
        assert pref.id is not None

    @pytest.mark.asyncio
    async def test_add_preference_with_context(self, memory_client):
        """Test adding a preference with context."""
        pref = await memory_client.long_term.add_preference(
            category="communication",
            preference="Prefers brief, direct responses",
            context="When asking technical questions",
            generate_embedding=False,
        )

        assert pref.context == "When asking technical questions"

    @pytest.mark.asyncio
    async def test_add_preference_with_embedding(self, memory_client):
        """Test adding a preference with embedding."""
        pref = await memory_client.long_term.add_preference(
            category="music",
            preference="Enjoys jazz and classical music",
            generate_embedding=True,
        )

        assert pref.embedding is not None
        assert len(pref.embedding) > 0

    @pytest.mark.asyncio
    async def test_search_preferences(self, memory_client):
        """Test searching preferences."""
        # Add various preferences
        prefs = [
            ("food", "Loves Italian cuisine"),
            ("food", "Vegetarian diet"),
            ("music", "Enjoys rock music"),
            ("sports", "Plays tennis regularly"),
            ("work", "Prefers remote work"),
        ]

        for category, preference in prefs:
            await memory_client.long_term.add_preference(
                category=category,
                preference=preference,
                generate_embedding=True,
            )

        # Search for food preferences
        results = await memory_client.long_term.search_preferences(
            "dining and cuisine",
            limit=10,
        )

        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_get_preferences_by_category(self, memory_client):
        """Test getting preferences by category."""
        # Add preferences in same category
        await memory_client.long_term.add_preference(
            category="test-category",
            preference="Test preference 1",
            generate_embedding=False,
        )
        await memory_client.long_term.add_preference(
            category="test-category",
            preference="Test preference 2",
            generate_embedding=False,
        )
        await memory_client.long_term.add_preference(
            category="other-category",
            preference="Other preference",
            generate_embedding=False,
        )

        # Get by category
        results = await memory_client.long_term.get_preferences_by_category("test-category")

        assert len(results) >= 2
        assert all(p.category == "test-category" for p in results)


@pytest.mark.integration
class TestLongTermMemoryFacts:
    """Test fact operations in long-term memory."""

    @pytest.mark.asyncio
    async def test_add_fact_basic(self, memory_client):
        """Test adding a basic fact."""
        fact = await memory_client.long_term.add_fact(
            subject="Alice",
            predicate="works_at",
            obj="Acme Corp",
            generate_embedding=False,
        )

        assert fact is not None
        assert fact.subject == "Alice"
        assert fact.predicate == "works_at"
        assert fact.object == "Acme Corp"
        assert fact.id is not None

    @pytest.mark.asyncio
    async def test_add_fact_with_validity(self, memory_client):
        """Test adding a fact with temporal validity."""
        valid_from = datetime(2023, 1, 1)
        valid_until = datetime(2024, 12, 31)

        fact = await memory_client.long_term.add_fact(
            subject="Bob",
            predicate="employed_at",
            obj="Tech Inc",
            valid_from=valid_from,
            valid_until=valid_until,
            generate_embedding=False,
        )

        assert fact.valid_from == valid_from
        assert fact.valid_until == valid_until

    @pytest.mark.asyncio
    async def test_add_fact_with_embedding(self, memory_client):
        """Test adding a fact with embedding."""
        fact = await memory_client.long_term.add_fact(
            subject="Charlie",
            predicate="lives_in",
            obj="New York",
            generate_embedding=True,
        )

        assert fact.embedding is not None
        assert len(fact.embedding) > 0

    @pytest.mark.asyncio
    async def test_get_facts_about_subject(self, memory_client):
        """Test getting facts about a subject."""
        # Add facts about same subject
        await memory_client.long_term.add_fact(
            subject="TestSubject",
            predicate="has_property",
            obj="value1",
            generate_embedding=False,
        )
        await memory_client.long_term.add_fact(
            subject="TestSubject",
            predicate="has_another_property",
            obj="value2",
            generate_embedding=False,
        )
        await memory_client.long_term.add_fact(
            subject="OtherSubject",
            predicate="has_property",
            obj="value3",
            generate_embedding=False,
        )

        # Get facts about TestSubject
        facts = await memory_client.long_term.get_facts_about("TestSubject")

        assert len(facts) >= 2
        assert all(f.subject == "TestSubject" for f in facts)

    @pytest.mark.asyncio
    async def test_search_facts(self, memory_client):
        """Test searching facts."""
        # Add various facts
        await memory_client.long_term.add_fact(
            subject="Python",
            predicate="is_a",
            obj="programming language",
            generate_embedding=True,
        )
        await memory_client.long_term.add_fact(
            subject="Python",
            predicate="created_by",
            obj="Guido van Rossum",
            generate_embedding=True,
        )
        await memory_client.long_term.add_fact(
            subject="JavaScript",
            predicate="is_a",
            obj="programming language",
            generate_embedding=True,
        )

        # Search for programming facts
        results = await memory_client.long_term.search_facts(
            "programming languages",
            limit=10,
        )

        assert isinstance(results, list)


@pytest.mark.integration
class TestLongTermMemoryRelationships:
    """Test entity relationship operations."""

    @pytest.mark.asyncio
    async def test_add_relationship_between_entities(self, memory_client):
        """Test creating relationships between entities."""
        # Add entities
        entity1, _ = await memory_client.long_term.add_entity(
            name="Company A",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )
        entity2, _ = await memory_client.long_term.add_entity(
            name="Company B",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )

        # Add relationship using entity objects
        rel = await memory_client.long_term.add_relationship(
            source=entity1,
            target=entity2,
            relationship_type="PARTNER_OF",
        )

        assert rel is not None

    @pytest.mark.asyncio
    async def test_get_entity_relationships(self, memory_client):
        """Test getting relationships for an entity."""
        # Add entities and relationships
        entity1, _ = await memory_client.long_term.add_entity(
            name="RelTestEntity1",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )
        entity2, _ = await memory_client.long_term.add_entity(
            name="RelTestEntity2",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )

        await memory_client.long_term.add_relationship(
            source=entity1,
            target=entity2,
            relationship_type="WORKS_AT",
        )

        # Get relationships
        rels = await memory_client.long_term.get_entity_relationships("RelTestEntity1")

        assert isinstance(rels, list)


@pytest.mark.integration
class TestMergeTransfersRelationProvenance:
    """``merge_duplicate_entities`` must not lose (or choke on) edge provenance."""

    @pytest.mark.asyncio
    async def test_transferred_edge_keeps_its_type_and_support(self, clean_memory_client):
        """The merge keyed on ``{type: r.type}``, which Neo4j rejects when null.

        A relation written before the v0.7 provenance rework only carries
        ``relation_type``, so ``r.type`` is null and the whole merge failed --
        taking the mentions and provenance transfers down with it. The new
        ``ON CREATE SET`` also has to carry the provenance across instead of
        dropping it.
        """
        client = clean_memory_client
        source, _ = await client.long_term.add_entity(
            name="Acme Corporation",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )
        target, _ = await client.long_term.add_entity(
            name="Acme Corp",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )
        neighbour, _ = await client.long_term.add_entity(
            name="Ada Lovelace",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        # A typed, provenance-carrying edge...
        await client._client.execute_write(
            """
            MATCH (n:Entity {id: $neighbour}), (s:Entity {id: $source})
            CREATE (n)-[:RELATED_TO {
                id: 'rel-typed',
                type: 'EMPLOYED_BY',
                relation_type: 'EMPLOYED_BY',
                confidence: 0.9,
                support: 3,
                derived: false,
                extractor: 'keyword',
                source_message_ids: ['m1', 'm2'],
                evidence: ['Ada Lovelace works at Acme Corporation']
            }]->(s)
            """,
            {"neighbour": str(neighbour.id), "source": str(source.id)},
        )
        # ...and a pre-v0.7 edge with a null ``type``, which is what broke.
        await client._client.execute_write(
            """
            MATCH (s:Entity {id: $source}), (n:Entity {id: $neighbour})
            CREATE (s)-[:RELATED_TO {
                id: 'rel-legacy',
                relation_type: 'KNOWS',
                confidence: 0.7
            }]->(n)
            """,
            {"neighbour": str(neighbour.id), "source": str(source.id)},
        )

        merged = await client.long_term.merge_duplicate_entities(
            source_id=source.id, target_id=target.id
        )
        assert merged is not None

        edges = await client._client.execute_read(
            """
            MATCH (a:Entity)-[r:RELATED_TO]-(b:Entity)
            WHERE $target IN [a.id, b.id]
            RETURN r.type AS type, r.support AS support, r.derived AS derived,
                   r.extractor AS extractor,
                   r.source_message_ids AS source_message_ids,
                   r.evidence AS evidence, r.migrated_from AS migrated_from
            ORDER BY type
            """,
            {"target": str(target.id)},
        )
        by_type = {edge["type"]: edge for edge in edges}
        assert set(by_type) == {"EMPLOYED_BY", "KNOWS"}, (
            "both edges must reach the surviving entity, including the null-typed one"
        )

        employed = by_type["EMPLOYED_BY"]
        assert employed["support"] == 3, "provenance must survive the transfer"
        assert employed["derived"] is False
        assert employed["extractor"] == "keyword"
        assert employed["source_message_ids"] == ["m1", "m2"]
        assert employed["evidence"] == ["Ada Lovelace works at Acme Corporation"]
        assert employed["migrated_from"] == str(source.id)

        # The null-typed edge falls back to relation_type for its merge key.
        assert by_type["KNOWS"]["support"] == 1


@pytest.mark.integration
class TestLongTermMemoryEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_entity_with_special_characters(self, memory_client):
        """Test entity with special characters in name."""
        entity, _ = await memory_client.long_term.add_entity(
            name="O'Brien & Co.",
            entity_type=EntityType.ORGANIZATION,
            resolve=False,
            generate_embedding=False,
        )

        assert entity.name == "O'Brien & Co."

    @pytest.mark.asyncio
    async def test_preference_with_unicode(self, memory_client):
        """Test preference with unicode characters."""
        pref = await memory_client.long_term.add_preference(
            category="language",
            preference="Speaks 日本語 and Español fluently",
            generate_embedding=False,
        )

        assert "日本語" in pref.preference
        assert "Español" in pref.preference

    @pytest.mark.asyncio
    async def test_fact_with_long_values(self, memory_client):
        """Test fact with very long subject/object."""
        long_text = "A" * 1000

        fact = await memory_client.long_term.add_fact(
            subject=long_text,
            predicate="has_property",
            obj=long_text,
            generate_embedding=False,
        )

        assert len(fact.subject) == 1000
        assert len(fact.object) == 1000

    @pytest.mark.asyncio
    async def test_concurrent_entity_additions(self, memory_client):
        """Test concurrent entity additions."""
        import asyncio

        async def add_entity(index):
            return await memory_client.long_term.add_entity(
                name=f"ConcurrentEntity{index}",
                entity_type=EntityType.PERSON,
                resolve=False,
                generate_embedding=False,
            )

        # Add 10 entities concurrently
        tasks = [add_entity(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10
        assert all(e is not None for e in results)

    @pytest.mark.asyncio
    async def test_duplicate_entity_handling(self, memory_client):
        """Test handling of duplicate entities."""
        # Add same entity twice (should use resolution or update)
        entity1, _ = await memory_client.long_term.add_entity(
            name="DuplicateTestEntity",
            entity_type=EntityType.PERSON,
            description="First description",
            resolve=False,
            generate_embedding=False,
        )

        entity2, _ = await memory_client.long_term.add_entity(
            name="DuplicateTestEntity",
            entity_type=EntityType.PERSON,
            description="Second description",
            resolve=True,  # Enable resolution
            generate_embedding=False,
        )

        # Both should return valid entities
        assert entity1 is not None
        assert entity2 is not None

    @pytest.mark.asyncio
    async def test_empty_preference_category(self, memory_client):
        """Test preference with empty category."""
        pref = await memory_client.long_term.add_preference(
            category="",
            preference="A preference without category",
            generate_embedding=False,
        )

        assert pref.category == ""

    @pytest.mark.asyncio
    async def test_search_with_no_embeddings(self, memory_client):
        """Test search when no embeddings exist."""
        # Add entity without embedding
        await memory_client.long_term.add_entity(
            name="NoEmbeddingEntity",
            entity_type=EntityType.PERSON,
            resolve=False,
            generate_embedding=False,
        )

        # Search should still work (may return empty results)
        results = await memory_client.long_term.search_entities(
            "some query",
            limit=10,
        )

        assert isinstance(results, list)


@pytest.mark.integration
class TestEntityNodeLabels:
    """Test that entities have type and subtype as Neo4j node labels (PascalCase)."""

    @pytest.mark.asyncio
    async def test_entity_has_type_label(self, memory_client):
        """Test that created entities have type as a PascalCase node label."""
        entity, _ = await memory_client.long_term.add_entity(
            name="Label Test Person",
            entity_type="PERSON",
            resolve=False,
            generate_embedding=False,
        )

        # Query Neo4j directly to check labels
        result = await memory_client._client.execute_read(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels",
            {"id": str(entity.id)},
        )

        labels = result[0]["labels"]
        assert "Entity" in labels
        assert "Person" in labels  # PascalCase

    @pytest.mark.asyncio
    async def test_entity_has_subtype_label(self, memory_client):
        """Test that created entities have subtype as a PascalCase node label."""
        entity, _ = await memory_client.long_term.add_entity(
            name="Tesla Model 3",
            entity_type="OBJECT",
            subtype="VEHICLE",
            resolve=False,
            generate_embedding=False,
        )

        # Query Neo4j directly to check labels
        result = await memory_client._client.execute_read(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels",
            {"id": str(entity.id)},
        )

        labels = result[0]["labels"]
        assert "Entity" in labels
        assert "Object" in labels  # PascalCase
        assert "Vehicle" in labels  # PascalCase

    @pytest.mark.asyncio
    async def test_all_pole_o_types_have_labels(self, memory_client):
        """Test that all POLE+O types are added as PascalCase labels."""
        # Map from input type to expected PascalCase label
        test_cases = [
            ("PERSON", None, "Person"),
            ("OBJECT", None, "Object"),
            ("LOCATION", None, "Location"),
            ("EVENT", None, "Event"),
            ("ORGANIZATION", None, "Organization"),
        ]

        for entity_type, subtype, expected_label in test_cases:
            entity, _ = await memory_client.long_term.add_entity(
                name=f"Test {entity_type}",
                entity_type=entity_type,
                subtype=subtype,
                resolve=False,
                generate_embedding=False,
            )

            result = await memory_client._client.execute_read(
                "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels",
                {"id": str(entity.id)},
            )

            labels = result[0]["labels"]
            assert "Entity" in labels, f"Missing Entity label for {entity_type}"
            assert expected_label in labels, f"Missing {expected_label} label"

    @pytest.mark.asyncio
    async def test_subtypes_have_labels(self, memory_client):
        """Test that various subtypes are added as PascalCase labels."""
        test_cases = [
            ("PERSON", "INDIVIDUAL", "Person", "Individual"),
            ("OBJECT", "VEHICLE", "Object", "Vehicle"),
            ("OBJECT", "DOCUMENT", "Object", "Document"),
            ("LOCATION", "ADDRESS", "Location", "Address"),
            ("LOCATION", "CITY", "Location", "City"),
            ("EVENT", "MEETING", "Event", "Meeting"),
            ("ORGANIZATION", "COMPANY", "Organization", "Company"),
        ]

        for entity_type, subtype, expected_type_label, expected_subtype_label in test_cases:
            entity, _ = await memory_client.long_term.add_entity(
                name=f"Test {entity_type} {subtype}",
                entity_type=entity_type,
                subtype=subtype,
                resolve=False,
                generate_embedding=False,
            )

            result = await memory_client._client.execute_read(
                "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels",
                {"id": str(entity.id)},
            )

            labels = result[0]["labels"]
            assert "Entity" in labels
            assert expected_type_label in labels, f"Missing {expected_type_label} label"
            assert expected_subtype_label in labels, f"Missing {expected_subtype_label} label"

    @pytest.mark.asyncio
    async def test_query_entities_by_type_label(self, memory_client):
        """Test querying entities using PascalCase type label directly."""
        # Create entities of different types
        await memory_client.long_term.add_entity(
            name="John Doe",
            entity_type="PERSON",
            resolve=False,
            generate_embedding=False,
        )
        await memory_client.long_term.add_entity(
            name="Acme Corp",
            entity_type="ORGANIZATION",
            resolve=False,
            generate_embedding=False,
        )
        await memory_client.long_term.add_entity(
            name="New York",
            entity_type="LOCATION",
            resolve=False,
            generate_embedding=False,
        )

        # Query by Person label directly (PascalCase)
        result = await memory_client._client.execute_read(
            "MATCH (p:Person) WHERE p:Entity RETURN p.name AS name"
        )
        names = [r["name"] for r in result]
        assert "John Doe" in names
        assert "Acme Corp" not in names
        assert "New York" not in names

        # Query by Organization label (PascalCase)
        result = await memory_client._client.execute_read(
            "MATCH (o:Organization) WHERE o:Entity RETURN o.name AS name"
        )
        names = [r["name"] for r in result]
        assert "Acme Corp" in names
        assert "John Doe" not in names

    @pytest.mark.asyncio
    async def test_query_entities_by_subtype_label(self, memory_client):
        """Test querying entities using PascalCase subtype label directly."""
        # Create entities with subtypes
        await memory_client.long_term.add_entity(
            name="Ford F-150",
            entity_type="OBJECT",
            subtype="VEHICLE",
            resolve=False,
            generate_embedding=False,
        )
        await memory_client.long_term.add_entity(
            name="iPhone 15",
            entity_type="OBJECT",
            subtype="DEVICE",
            resolve=False,
            generate_embedding=False,
        )

        # Query by Vehicle label (PascalCase)
        result = await memory_client._client.execute_read(
            "MATCH (v:Vehicle) WHERE v:Entity RETURN v.name AS name"
        )
        names = [r["name"] for r in result]
        assert "Ford F-150" in names
        assert "iPhone 15" not in names

        # Query by Device label (PascalCase)
        result = await memory_client._client.execute_read(
            "MATCH (d:Device) WHERE d:Entity RETURN d.name AS name"
        )
        names = [r["name"] for r in result]
        assert "iPhone 15" in names
        assert "Ford F-150" not in names

    @pytest.mark.asyncio
    async def test_custom_type_has_label(self, memory_client):
        """Test that custom entity types are also added as PascalCase labels."""
        # Custom types (valid identifiers) should become labels
        entity, _ = await memory_client.long_term.add_entity(
            name="Custom Entity",
            entity_type="PRODUCT",  # Custom type (not POLE+O)
            resolve=False,
            generate_embedding=False,
        )

        result = await memory_client._client.execute_read(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels",
            {"id": str(entity.id)},
        )

        labels = result[0]["labels"]
        assert "Entity" in labels
        # Custom types ARE now added as PascalCase labels
        assert "Product" in labels

    @pytest.mark.asyncio
    async def test_custom_type_and_subtype_have_labels(self, memory_client):
        """Test that custom types with custom subtypes have both as PascalCase labels."""
        entity, _ = await memory_client.long_term.add_entity(
            name="iPhone 15 Pro",
            entity_type="PRODUCT",
            subtype="ELECTRONICS",
            resolve=False,
            generate_embedding=False,
        )

        result = await memory_client._client.execute_read(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels",
            {"id": str(entity.id)},
        )

        labels = result[0]["labels"]
        assert "Entity" in labels
        assert "Product" in labels  # PascalCase
        assert "Electronics" in labels  # PascalCase

    @pytest.mark.asyncio
    async def test_query_by_custom_type_label(self, memory_client):
        """Test querying entities using custom PascalCase type labels."""
        # Create entities with custom types
        await memory_client.long_term.add_entity(
            name="Widget A",
            entity_type="PRODUCT",
            resolve=False,
            generate_embedding=False,
        )
        await memory_client.long_term.add_entity(
            name="Subscription Plan",
            entity_type="SERVICE",
            resolve=False,
            generate_embedding=False,
        )

        # Query by Product label (PascalCase)
        result = await memory_client._client.execute_read(
            "MATCH (p:Product) WHERE p:Entity RETURN p.name AS name"
        )
        names = [r["name"] for r in result]
        assert "Widget A" in names
        assert "Subscription Plan" not in names

        # Query by Service label (PascalCase)
        result = await memory_client._client.execute_read(
            "MATCH (s:Service) WHERE s:Entity RETURN s.name AS name"
        )
        names = [r["name"] for r in result]
        assert "Subscription Plan" in names
        assert "Widget A" not in names

    @pytest.mark.asyncio
    async def test_invalid_label_format_no_extra_label(self, memory_client):
        """Test that invalid label formats don't add extra labels."""
        # Types with invalid label format should still create Entity but without type label
        entity, _ = await memory_client.long_term.add_entity(
            name="Entity with bad type",
            entity_type="has-dash",  # Invalid: contains dash
            resolve=False,
            generate_embedding=False,
        )

        result = await memory_client._client.execute_read(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels",
            {"id": str(entity.id)},
        )

        labels = result[0]["labels"]
        assert "Entity" in labels
        # Invalid format should NOT be a label
        assert len(labels) == 1  # Only Entity label

    @pytest.mark.asyncio
    async def test_type_subtype_string_format(self, memory_client):
        """Test adding entity with TYPE:SUBTYPE string format."""
        entity, _ = await memory_client.long_term.add_entity(
            name="123 Main St",
            entity_type="LOCATION:ADDRESS",  # Combined format
            resolve=False,
            generate_embedding=False,
        )

        result = await memory_client._client.execute_read(
            "MATCH (e:Entity {id: $id}) RETURN labels(e) AS labels, e.type AS type, e.subtype AS subtype",
            {"id": str(entity.id)},
        )

        row = result[0]
        labels = row["labels"]

        # Should have both type and subtype as PascalCase labels
        assert "Entity" in labels
        assert "Location" in labels
        assert "Address" in labels

        # Properties should also be set correctly
        assert row["type"] == "LOCATION"
        assert row["subtype"] == "ADDRESS"
