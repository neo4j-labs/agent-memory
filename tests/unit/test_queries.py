"""Unit tests for Cypher queries in queries.py.

These tests validate query syntax and structure without requiring a Neo4j database.
For integration tests that run queries against a live database, see
tests/integration/test_queries_neo4j.py.
"""

import re

import pytest

from neo4j_agent_memory.graph import queries


class TestQueryConstants:
    """Test that query constants are valid."""

    def get_all_query_constants(self) -> list[tuple[str, str]]:
        """Get all uppercase query constants from the queries module."""
        query_pairs = []
        for name in dir(queries):
            if name.isupper() and not name.startswith("_"):
                value = getattr(queries, name)
                if isinstance(value, str) and len(value.strip()) > 0:
                    query_pairs.append((name, value))
        return query_pairs

    def test_all_queries_are_non_empty_strings(self):
        """All query constants should be non-empty strings."""
        for name, query in self.get_all_query_constants():
            assert isinstance(query, str), f"{name} should be a string"
            assert len(query.strip()) > 0, f"{name} should not be empty"

    def test_queries_have_valid_structure(self):
        """Queries should start with valid Cypher keywords."""
        valid_starts = [
            "MATCH",
            "CREATE",
            "MERGE",
            "CALL",
            "WITH",
            "OPTIONAL",
            "UNWIND",
            "RETURN",
            "SHOW",
            "DROP",
            "//",  # Comments are valid
        ]
        for name, query in self.get_all_query_constants():
            # Skip template queries that start with placeholders
            if "{" in query.split()[0] if query.split() else True:
                continue
            first_word = query.strip().split()[0].upper()
            assert any(first_word.startswith(valid) for valid in valid_starts), (
                f"{name} starts with unexpected keyword: {first_word}"
            )

    def test_queries_have_balanced_braces(self):
        """Query strings should have balanced curly braces."""
        for name, query in self.get_all_query_constants():
            open_count = query.count("{")
            close_count = query.count("}")
            assert open_count == close_count, (
                f"{name} has unbalanced braces: {open_count} open, {close_count} close"
            )

    def test_queries_have_balanced_parentheses(self):
        """Query strings should have balanced parentheses."""
        for name, query in self.get_all_query_constants():
            open_count = query.count("(")
            close_count = query.count(")")
            assert open_count == close_count, (
                f"{name} has unbalanced parentheses: {open_count} open, {close_count} close"
            )

    def test_queries_have_balanced_brackets(self):
        """Query strings should have balanced square brackets."""
        for name, query in self.get_all_query_constants():
            open_count = query.count("[")
            close_count = query.count("]")
            assert open_count == close_count, (
                f"{name} has unbalanced brackets: {open_count} open, {close_count} close"
            )


class TestShortTermMemoryQueries:
    """Test short-term memory query structure."""

    def test_create_conversation_has_required_params(self):
        """CREATE_CONVERSATION should have all required parameter placeholders."""
        required = ["$id", "$session_id", "$title"]
        for param in required:
            assert param in queries.CREATE_CONVERSATION, f"Missing {param}"

    def test_create_message_has_required_params(self):
        """CREATE_MESSAGE should have all required parameter placeholders."""
        required = ["$conversation_id", "$id", "$role", "$content"]
        for param in required:
            assert param in queries.CREATE_MESSAGE, f"Missing {param}"

    def test_get_conversation_uses_id_param(self):
        """GET_CONVERSATION should use $id parameter."""
        assert "$id" in queries.GET_CONVERSATION

    def test_get_conversation_by_session_uses_session_id(self):
        """GET_CONVERSATION_BY_SESSION should use $session_id parameter."""
        assert "$session_id" in queries.GET_CONVERSATION_BY_SESSION

    def test_search_messages_by_embedding_has_required_params(self):
        """SEARCH_MESSAGES_BY_EMBEDDING should have all required params."""
        required = ["$limit", "$embedding", "$threshold"]
        for param in required:
            assert param in queries.SEARCH_MESSAGES_BY_EMBEDDING, f"Missing {param}"

    def test_list_sessions_has_ordering_params(self):
        """LIST_SESSIONS should support ordering."""
        assert "$order_by" in queries.LIST_SESSIONS
        assert "$order_dir" in queries.LIST_SESSIONS
        assert "$limit" in queries.LIST_SESSIONS
        assert "$offset" in queries.LIST_SESSIONS

    def test_entity_extraction_queries_use_session_id(self):
        """Entity extraction queries should use $session_id."""
        assert "$session_id" in queries.GET_MESSAGES_FOR_ENTITY_EXTRACTION
        assert "$session_id" in queries.GET_ALL_MESSAGES_FOR_SESSION
        assert "$session_id" in queries.GET_SUMMARY_ENTITIES


class TestLongTermMemoryQueries:
    """Test long-term memory query structure."""

    def test_create_entity_has_required_params(self):
        """CREATE_ENTITY should have all required parameter placeholders."""
        required = ["$id", "$name", "$type"]
        for param in required:
            assert param in queries.CREATE_ENTITY, f"Missing {param}"

    def test_create_preference_has_required_params(self):
        """CREATE_PREFERENCE should have all required parameter placeholders."""
        required = ["$id", "$category", "$preference"]
        for param in required:
            assert param in queries.CREATE_PREFERENCE, f"Missing {param}"

    def test_create_fact_has_required_params(self):
        """CREATE_FACT should have temporal parameters."""
        required = ["$subject", "$predicate", "$object", "$valid_from", "$valid_until"]
        for param in required:
            assert param in queries.CREATE_FACT, f"Missing {param}"

    def test_link_message_to_entity_params(self):
        """LINK_MESSAGE_TO_ENTITY should have required relationship params."""
        required = ["$message_id", "$entity_id", "$confidence"]
        for param in required:
            assert param in queries.LINK_MESSAGE_TO_ENTITY, f"Missing {param}"

    def test_entity_relation_queries_exist(self):
        """Relation queries should exist for both name and ID lookups."""
        assert hasattr(queries, "CREATE_ENTITY_RELATION_BY_NAME")
        assert hasattr(queries, "CREATE_ENTITY_RELATION_BY_ID")
        assert "$relation_type" in queries.CREATE_ENTITY_RELATION_BY_NAME
        assert "$confidence" in queries.CREATE_ENTITY_RELATION_BY_ID


class TestReasoningMemoryQueries:
    """Test reasoning memory query structure."""

    def test_create_reasoning_trace_has_required_params(self):
        """CREATE_REASONING_TRACE should have all required params."""
        required = ["$id", "$session_id", "$task"]
        for param in required:
            assert param in queries.CREATE_REASONING_TRACE, f"Missing {param}"

    def test_create_reasoning_step_has_required_params(self):
        """CREATE_REASONING_STEP should have all required params."""
        required = ["$trace_id", "$id", "$step_number", "$thought"]
        for param in required:
            assert param in queries.CREATE_REASONING_STEP, f"Missing {param}"

    def test_create_tool_call_has_required_params(self):
        """CREATE_TOOL_CALL should track tool usage."""
        required = ["$step_id", "$tool_name", "$arguments", "$status"]
        for param in required:
            assert param in queries.CREATE_TOOL_CALL, f"Missing {param}"

    def test_get_tool_stats_returns_metrics(self):
        """GET_TOOL_STATS should return success metrics."""
        assert "success_rate" in queries.GET_TOOL_STATS
        assert "total_calls" in queries.GET_TOOL_STATS


class TestSchemaPersistenceQueries:
    """Test schema persistence query structure."""

    def test_create_schema_has_required_params(self):
        """CREATE_SCHEMA should have all required params."""
        required = ["$id", "$name", "$version", "$config", "$is_active"]
        for param in required:
            assert param in queries.CREATE_SCHEMA, f"Missing {param}"

    def test_get_schema_queries_exist(self):
        """Schema retrieval queries should exist."""
        assert hasattr(queries, "GET_SCHEMA_BY_NAME")
        assert hasattr(queries, "GET_SCHEMA_BY_NAME_VERSION")
        assert hasattr(queries, "GET_SCHEMA_BY_ID")

    def test_list_schemas_supports_filtering(self):
        """LIST_SCHEMAS should support name filtering."""
        assert "$name" in queries.LIST_SCHEMAS

    def test_schema_index_queries_exist(self):
        """Schema index creation queries should exist."""
        assert hasattr(queries, "CREATE_SCHEMA_NAME_INDEX")
        assert hasattr(queries, "CREATE_SCHEMA_ID_INDEX")
        assert "schema_name_idx" in queries.CREATE_SCHEMA_NAME_INDEX
        assert "schema_id_idx" in queries.CREATE_SCHEMA_ID_INDEX


class TestSchemaManagementQueryFunctions:
    """Test schema management query builder functions."""

    def test_create_constraint_query(self):
        """Test create_constraint_query generates valid Cypher."""
        query = queries.create_constraint_query("test_constraint", "TestLabel", "prop")
        assert "CREATE CONSTRAINT test_constraint" in query
        assert "IF NOT EXISTS" in query
        assert "TestLabel" in query
        assert "prop" in query
        assert "IS UNIQUE" in query

    def test_create_index_query(self):
        """Test create_index_query generates valid Cypher."""
        query = queries.create_index_query("test_idx", "TestLabel", "prop")
        assert "CREATE INDEX test_idx" in query
        assert "IF NOT EXISTS" in query
        assert "TestLabel" in query
        assert "prop" in query

    def test_create_vector_index_query(self):
        """Test create_vector_index_query generates valid Cypher."""
        query = queries.create_vector_index_query("test_vec_idx", "Node", "embedding", 1536)
        assert "CREATE VECTOR INDEX test_vec_idx" in query
        assert "IF NOT EXISTS" in query
        assert "Node" in query
        assert "embedding" in query
        assert "1536" in query
        assert "cosine" in query

    def test_create_vector_index_query_custom_dimensions(self):
        """Test vector index with custom dimensions."""
        query = queries.create_vector_index_query("idx", "N", "emb", 768)
        assert "768" in query
        query = queries.create_vector_index_query("idx", "N", "emb", 3072)
        assert "3072" in query

    def test_create_point_index_query(self):
        """Test create_point_index_query generates valid Cypher."""
        query = queries.create_point_index_query("geo_idx", "Location", "coords")
        assert "CREATE POINT INDEX geo_idx" in query
        assert "IF NOT EXISTS" in query
        assert "Location" in query
        assert "coords" in query

    def test_drop_constraint_query(self):
        """Test drop_constraint_query generates valid Cypher."""
        query = queries.drop_constraint_query("my_constraint")
        assert "DROP CONSTRAINT my_constraint IF EXISTS" in query

    def test_drop_index_query(self):
        """Test drop_index_query generates valid Cypher."""
        query = queries.drop_index_query("my_index")
        assert "DROP INDEX my_index IF EXISTS" in query


class TestSchemaIntrospectionQueries:
    """Test schema introspection queries."""

    def test_show_constraints_query(self):
        """SHOW_CONSTRAINTS should be valid."""
        assert "SHOW CONSTRAINTS" in queries.SHOW_CONSTRAINTS
        assert "YIELD name" in queries.SHOW_CONSTRAINTS

    def test_show_indexes_query(self):
        """SHOW_INDEXES should be valid."""
        assert "SHOW INDEXES" in queries.SHOW_INDEXES
        assert "YIELD name" in queries.SHOW_INDEXES

    def test_show_constraints_detail_query(self):
        """SHOW_CONSTRAINTS_DETAIL should return full details."""
        query = queries.SHOW_CONSTRAINTS_DETAIL
        assert "SHOW CONSTRAINTS" in query
        assert "type" in query
        assert "labelsOrTypes" in query
        assert "properties" in query

    def test_show_indexes_detail_query(self):
        """SHOW_INDEXES_DETAIL should return full details."""
        query = queries.SHOW_INDEXES_DETAIL
        assert "SHOW INDEXES" in query
        assert "type" in query
        assert "labelsOrTypes" in query
        assert "properties" in query


class TestMetadataSearchQueryBuilder:
    """Test the metadata search query builder."""

    def test_build_metadata_search_query(self):
        """Test that build_metadata_search_query creates valid query."""
        metadata_clause = "m.metadata CONTAINS 'speaker'"
        query = queries.build_metadata_search_query(metadata_clause)

        assert "CALL db.index.vector.queryNodes" in query
        assert "message_embedding_idx" in query
        assert metadata_clause in query
        assert "$embedding" in query
        assert "$limit" in query
        assert "$threshold" in query

    def test_build_metadata_search_query_complex_clause(self):
        """Test with a complex metadata clause."""
        clause = "(m.metadata CONTAINS 'speaker' OR m.metadata CONTAINS 'guest')"
        query = queries.build_metadata_search_query(clause)
        assert clause in query


class TestGeospatialQueries:
    """Test geospatial query structure."""

    def test_update_entity_location_params(self):
        """UPDATE_ENTITY_LOCATION should have coordinate params."""
        required = ["$id", "$latitude", "$longitude"]
        for param in required:
            assert param in queries.UPDATE_ENTITY_LOCATION, f"Missing {param}"

    def test_search_locations_near_params(self):
        """SEARCH_LOCATIONS_NEAR should have proximity params."""
        required = ["$latitude", "$longitude", "$radius_meters", "$limit"]
        for param in required:
            assert param in queries.SEARCH_LOCATIONS_NEAR, f"Missing {param}"

    def test_search_locations_in_bounding_box_params(self):
        """SEARCH_LOCATIONS_IN_BOUNDING_BOX should have bounding box params."""
        required = ["$min_lat", "$min_lon", "$max_lat", "$max_lon"]
        for param in required:
            assert param in queries.SEARCH_LOCATIONS_IN_BOUNDING_BOX, f"Missing {param}"


class TestProvenanceTrackingQueries:
    """Test provenance tracking query structure."""

    def test_create_extractor_params(self):
        """CREATE_EXTRACTOR should track extractor metadata."""
        required = ["$name", "$id", "$version"]
        for param in required:
            assert param in queries.CREATE_EXTRACTOR, f"Missing {param}"

    def test_create_extracted_from_relationship_params(self):
        """EXTRACTED_FROM relationship should link entity to message."""
        required = ["$entity_id", "$message_id", "$confidence"]
        for param in required:
            assert param in queries.CREATE_EXTRACTED_FROM_RELATIONSHIP, f"Missing {param}"

    def test_get_entity_provenance_returns_sources(self):
        """GET_ENTITY_PROVENANCE should return source information."""
        assert "EXTRACTED_FROM" in queries.GET_ENTITY_PROVENANCE
        assert "EXTRACTED_BY" in queries.GET_ENTITY_PROVENANCE


class TestDeduplicationQueries:
    """Test entity deduplication query structure."""

    def test_create_same_as_relationship_params(self):
        """CREATE_SAME_AS_RELATIONSHIP should track similarity."""
        required = ["$source_id", "$target_id", "$confidence", "$status"]
        for param in required:
            assert param in queries.CREATE_SAME_AS_RELATIONSHIP, f"Missing {param}"

    def test_get_potential_duplicates_filters_pending(self):
        """GET_POTENTIAL_DUPLICATES should filter by status."""
        assert "status" in queries.GET_POTENTIAL_DUPLICATES
        assert "pending" in queries.GET_POTENTIAL_DUPLICATES

    def test_get_potential_duplicates_returns_each_pair_once_with_confidence(self):
        """A directed match returns each pair once; confidence is a scalar column.

        Result.data() flattens a returned relationship, so reading confidence
        off ``r`` always fell back to 0.0.
        """
        query = queries.GET_POTENTIAL_DUPLICATES
        assert "-[r:SAME_AS]->" in query
        assert "r.confidence AS confidence" in query

    def test_merge_entities_transfers_relationships(self):
        """MERGE_ENTITIES should transfer relationships."""
        query = queries.MERGE_ENTITIES
        assert "$source_id" in query
        assert "$target_id" in query
        assert "merged_into" in query
        assert "aliases" in query


class TestGraphExportQueries:
    """Test graph export query structure."""

    def test_graph_export_queries_exist(self):
        """Graph export queries should exist for each memory type."""
        assert hasattr(queries, "GET_GRAPH_SHORT_TERM")
        assert hasattr(queries, "GET_GRAPH_LONG_TERM")
        assert hasattr(queries, "GET_GRAPH_REASONING")
        assert hasattr(queries, "GET_GRAPH_ALL")

    def test_graph_export_supports_embedding_option(self):
        """Graph export queries should support include_embeddings option."""
        assert "$include_embeddings" in queries.GET_GRAPH_SHORT_TERM
        assert "$include_embeddings" in queries.GET_GRAPH_LONG_TERM
        assert "$include_embeddings" in queries.GET_GRAPH_REASONING
