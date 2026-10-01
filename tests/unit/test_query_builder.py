"""Tests for query builder utilities."""

from neo4j_agent_memory.graph.query_builder import (
    VALID_ENTITY_TYPES,
    VALID_SUBTYPES,
    build_create_entity_query,
    build_label_set_clause,
    is_poleo_type,
    ontology_node_label,
    sanitize_label,
    to_pascal_case,
    validate_entity_type,
    validate_subtype,
)


class TestToPascalCase:
    """Tests for to_pascal_case function."""

    def test_uppercase_to_pascal(self):
        """Test conversion from UPPERCASE to PascalCase."""
        assert to_pascal_case("PERSON") == "Person"
        assert to_pascal_case("OBJECT") == "Object"
        assert to_pascal_case("LOCATION") == "Location"
        assert to_pascal_case("ORGANIZATION") == "Organization"

    def test_lowercase_to_pascal(self):
        """Test conversion from lowercase to PascalCase."""
        assert to_pascal_case("person") == "Person"
        assert to_pascal_case("vehicle") == "Vehicle"

    def test_snake_case_to_pascal(self):
        """Test conversion from snake_case to PascalCase."""
        assert to_pascal_case("my_type") == "MyType"
        assert to_pascal_case("CUSTOM_TYPE") == "CustomType"
        assert to_pascal_case("my_custom_entity") == "MyCustomEntity"

    def test_already_pascal(self):
        """Test that PascalCase input is normalized."""
        assert to_pascal_case("Person") == "Person"
        assert to_pascal_case("MyType") == "Mytype"  # Each part is title-cased

    def test_empty_string(self):
        """Test empty string handling."""
        assert to_pascal_case("") == ""

    def test_with_numbers(self):
        """Test handling of strings with numbers."""
        assert to_pascal_case("TYPE2") == "Type2"
        assert to_pascal_case("entity123") == "Entity123"


class TestSanitizeLabel:
    """Tests for sanitize_label function."""

    def test_valid_labels_to_pascal_case(self):
        """Test that valid labels are converted to PascalCase."""
        assert sanitize_label("PERSON") == "Person"
        assert sanitize_label("person") == "Person"
        assert sanitize_label("Person") == "Person"
        assert sanitize_label("VEHICLE") == "Vehicle"

    def test_labels_with_underscores(self):
        """Test that labels with underscores are converted to PascalCase."""
        assert sanitize_label("MY_CUSTOM_TYPE") == "MyCustomType"
        assert sanitize_label("entity_v2") == "EntityV2"
        assert sanitize_label("Custom_Type") == "CustomType"

    def test_labels_with_numbers(self):
        """Test that labels can contain numbers (but not start with them)."""
        assert sanitize_label("Type2") == "Type2"
        assert sanitize_label("Entity123") == "Entity123"
        assert sanitize_label("2Invalid") is None  # Can't start with number

    def test_invalid_labels(self):
        """Test that invalid labels return None."""
        assert sanitize_label("") is None
        assert sanitize_label("  ") is None
        assert sanitize_label("123") is None  # Starts with number
        assert sanitize_label("has-dash") is None  # Contains dash
        assert sanitize_label("has space") is None  # Contains space
        assert sanitize_label("has.dot") is None  # Contains dot
        assert sanitize_label("special!char") is None  # Contains special char

    def test_whitespace_handling(self):
        """Test that whitespace is trimmed."""
        assert sanitize_label("  PERSON  ") == "Person"
        assert sanitize_label("\tCUSTOM\n") == "Custom"

    def test_none_and_non_string(self):
        """Test handling of None and non-string input."""
        assert sanitize_label(None) is None
        assert sanitize_label(123) is None  # type: ignore


class TestIsPOLEOType:
    """Tests for is_poleo_type function."""

    def test_valid_poleo_types(self):
        """Test that POLE+O types are recognized."""
        assert is_poleo_type("PERSON") is True
        assert is_poleo_type("OBJECT") is True
        assert is_poleo_type("LOCATION") is True
        assert is_poleo_type("EVENT") is True
        assert is_poleo_type("ORGANIZATION") is True

    def test_case_insensitive(self):
        """Test case insensitivity."""
        assert is_poleo_type("person") is True
        assert is_poleo_type("Person") is True

    def test_custom_types_not_poleo(self):
        """Test that custom types are not POLE+O."""
        assert is_poleo_type("CUSTOM") is False
        assert is_poleo_type("PRODUCT") is False
        assert is_poleo_type("SERVICE") is False


class TestValidateEntityType:
    """Tests for validate_entity_type function."""

    def test_valid_poleo_types(self):
        """Test validation of valid POLE+O entity types returns PascalCase."""
        assert validate_entity_type("PERSON") == "Person"
        assert validate_entity_type("OBJECT") == "Object"
        assert validate_entity_type("LOCATION") == "Location"
        assert validate_entity_type("EVENT") == "Event"
        assert validate_entity_type("ORGANIZATION") == "Organization"

    def test_case_insensitive(self):
        """Test that validation is case-insensitive and returns PascalCase."""
        assert validate_entity_type("person") == "Person"
        assert validate_entity_type("Person") == "Person"
        assert validate_entity_type("PERSON") == "Person"
        assert validate_entity_type("object") == "Object"
        assert validate_entity_type("Location") == "Location"

    def test_custom_types_are_valid(self):
        """Test that custom types are accepted and return PascalCase."""
        assert validate_entity_type("CUSTOM") == "Custom"
        assert validate_entity_type("PRODUCT") == "Product"
        assert validate_entity_type("SERVICE") == "Service"
        assert validate_entity_type("MY_ENTITY") == "MyEntity"
        assert validate_entity_type("Entity123") == "Entity123"

    def test_invalid_label_formats(self):
        """Test that invalid label formats return None."""
        assert validate_entity_type("") is None
        assert validate_entity_type("123start") is None  # Can't start with number
        assert validate_entity_type("has-dash") is None  # Invalid character
        assert validate_entity_type("has space") is None  # Invalid character


class TestValidateSubtype:
    """Tests for validate_subtype function."""

    def test_valid_person_subtypes(self):
        """Test validation of valid PERSON subtypes returns PascalCase."""
        assert validate_subtype("PERSON", "INDIVIDUAL") == "Individual"
        assert validate_subtype("PERSON", "ALIAS") == "Alias"
        assert validate_subtype("PERSON", "PERSONA") == "Persona"
        assert validate_subtype("PERSON", "SUSPECT") == "Suspect"
        assert validate_subtype("PERSON", "WITNESS") == "Witness"
        assert validate_subtype("PERSON", "VICTIM") == "Victim"

    def test_valid_object_subtypes(self):
        """Test validation of valid OBJECT subtypes returns PascalCase."""
        assert validate_subtype("OBJECT", "VEHICLE") == "Vehicle"
        assert validate_subtype("OBJECT", "PHONE") == "Phone"
        assert validate_subtype("OBJECT", "EMAIL") == "Email"
        assert validate_subtype("OBJECT", "DOCUMENT") == "Document"
        assert validate_subtype("OBJECT", "DEVICE") == "Device"
        assert validate_subtype("OBJECT", "WEAPON") == "Weapon"

    def test_valid_location_subtypes(self):
        """Test validation of valid LOCATION subtypes returns PascalCase."""
        assert validate_subtype("LOCATION", "ADDRESS") == "Address"
        assert validate_subtype("LOCATION", "CITY") == "City"
        assert validate_subtype("LOCATION", "COUNTRY") == "Country"
        assert validate_subtype("LOCATION", "LANDMARK") == "Landmark"
        assert validate_subtype("LOCATION", "FACILITY") == "Facility"

    def test_valid_event_subtypes(self):
        """Test validation of valid EVENT subtypes returns PascalCase."""
        assert validate_subtype("EVENT", "INCIDENT") == "Incident"
        assert validate_subtype("EVENT", "MEETING") == "Meeting"
        assert validate_subtype("EVENT", "TRANSACTION") == "Transaction"
        assert validate_subtype("EVENT", "COMMUNICATION") == "Communication"

    def test_valid_organization_subtypes(self):
        """Test validation of valid ORGANIZATION subtypes returns PascalCase."""
        assert validate_subtype("ORGANIZATION", "COMPANY") == "Company"
        assert validate_subtype("ORGANIZATION", "NONPROFIT") == "Nonprofit"
        assert validate_subtype("ORGANIZATION", "GOVERNMENT") == "Government"
        assert validate_subtype("ORGANIZATION", "EDUCATIONAL") == "Educational"

    def test_case_insensitive(self):
        """Test that subtype validation is case-insensitive and returns PascalCase."""
        assert validate_subtype("PERSON", "individual") == "Individual"
        assert validate_subtype("person", "INDIVIDUAL") == "Individual"
        assert validate_subtype("Object", "vehicle") == "Vehicle"

    def test_invalid_subtype_for_poleo_type(self):
        """Test that subtypes invalid for a POLE+O type return None."""
        # VEHICLE is valid for OBJECT, not for PERSON
        assert validate_subtype("PERSON", "VEHICLE") is None
        # ADDRESS is valid for LOCATION, not for OBJECT
        assert validate_subtype("OBJECT", "ADDRESS") is None
        # COMPANY is valid for ORGANIZATION, not for EVENT
        assert validate_subtype("EVENT", "COMPANY") is None

    def test_invalid_subtype_for_poleo_type_custom_subtype(self):
        """Test that custom subtypes for POLE+O types return None."""
        # POLE+O types only allow predefined subtypes
        assert validate_subtype("PERSON", "CUSTOM_SUBTYPE") is None
        assert validate_subtype("OBJECT", "FOOBAR") is None

    def test_empty_subtype(self):
        """Test that empty subtype returns None."""
        assert validate_subtype("LOCATION", "") is None
        assert validate_subtype("CUSTOM", "") is None

    def test_custom_type_accepts_custom_subtypes(self):
        """Test that custom entity types accept any valid subtype in PascalCase."""
        # Custom types allow any valid label as subtype
        assert validate_subtype("PRODUCT", "ELECTRONICS") == "Electronics"
        assert validate_subtype("SERVICE", "SUBSCRIPTION") == "Subscription"
        assert validate_subtype("MY_ENTITY", "MY_SUBTYPE") == "MySubtype"
        assert validate_subtype("CUSTOM", "Custom_Sub") == "CustomSub"

    def test_custom_type_invalid_subtype_format(self):
        """Test that custom types still require valid label format for subtypes."""
        assert validate_subtype("CUSTOM", "has-dash") is None
        assert validate_subtype("PRODUCT", "123invalid") is None
        assert validate_subtype("SERVICE", "has space") is None


class TestBuildLabelSetClause:
    """Tests for build_label_set_clause function."""

    def test_type_only(self):
        """Test building clause with type only (no subtype) uses PascalCase."""
        clause = build_label_set_clause("PERSON", None)
        assert clause == "SET e:Person"

        clause = build_label_set_clause("OBJECT", None)
        assert clause == "SET e:Object"

        clause = build_label_set_clause("LOCATION", None)
        assert clause == "SET e:Location"

    def test_type_and_subtype(self):
        """Test building clause with both type and subtype uses PascalCase."""
        clause = build_label_set_clause("OBJECT", "VEHICLE")
        assert "SET" in clause
        assert "e:Object" in clause
        assert "e:Vehicle" in clause

        clause = build_label_set_clause("PERSON", "INDIVIDUAL")
        assert "e:Person" in clause
        assert "e:Individual" in clause

        clause = build_label_set_clause("LOCATION", "ADDRESS")
        assert "e:Location" in clause
        assert "e:Address" in clause

    def test_custom_node_variable(self):
        """Test building clause with custom node variable."""
        clause = build_label_set_clause("PERSON", None, node_var="n")
        assert clause == "SET n:Person"

        clause = build_label_set_clause("OBJECT", "VEHICLE", node_var="entity")
        assert "entity:Object" in clause
        assert "entity:Vehicle" in clause

    def test_invalid_label_format_returns_empty(self):
        """Test that invalid label format returns empty string."""
        clause = build_label_set_clause("has-dash", None)
        assert clause == ""

        clause = build_label_set_clause("123invalid", None)
        assert clause == ""

    def test_custom_type_and_subtype(self):
        """Test building clause with custom type and subtype uses PascalCase."""
        clause = build_label_set_clause("PRODUCT", "ELECTRONICS")
        assert "SET" in clause
        assert "e:Product" in clause
        assert "e:Electronics" in clause

        clause = build_label_set_clause("SERVICE", "SUBSCRIPTION")
        assert "e:Service" in clause
        assert "e:Subscription" in clause

    def test_invalid_subtype_for_poleo_only_includes_type(self):
        """Test that invalid subtype for POLE+O type still includes type label."""
        clause = build_label_set_clause("PERSON", "INVALID_SUBTYPE")
        assert clause == "SET e:Person"
        assert "Invalid" not in clause

    def test_custom_type_invalid_subtype_format(self):
        """Test that custom type with invalid subtype format only includes type."""
        clause = build_label_set_clause("CUSTOM", "has-dash")
        assert clause == "SET e:Custom"
        assert "has-dash" not in clause

    def test_case_insensitive(self):
        """Test that clause building is case-insensitive and uses PascalCase."""
        clause = build_label_set_clause("person", "individual")
        assert "e:Person" in clause
        assert "e:Individual" in clause


class TestBuildCreateEntityQuery:
    """Tests for build_create_entity_query function."""

    def test_query_contains_merge(self):
        """Test that generated query contains MERGE clause."""
        query = build_create_entity_query("PERSON", None)
        assert "MERGE (e:Entity" in query
        assert "{name: $name, type: $type}" in query

    def test_query_contains_on_create_set(self):
        """Test that generated query contains ON CREATE SET."""
        query = build_create_entity_query("PERSON", None)
        assert "ON CREATE SET" in query
        assert "e.id = $id" in query
        assert "e.subtype = $subtype" in query
        assert "e.created_at = datetime()" in query

    def test_query_contains_on_match_set(self):
        """Test that generated query contains ON MATCH SET."""
        query = build_create_entity_query("PERSON", None)
        assert "ON MATCH SET" in query
        assert "e.updated_at = datetime()" in query

    def test_query_contains_return(self):
        """Test that generated query ends with RETURN."""
        query = build_create_entity_query("PERSON", None)
        assert query.strip().endswith("RETURN e")

    def test_query_includes_type_label_in_pascal_case(self):
        """Test that query includes type as PascalCase label."""
        query = build_create_entity_query("PERSON", None)
        assert "SET e:Person" in query

        query = build_create_entity_query("OBJECT", None)
        assert "SET e:Object" in query

        query = build_create_entity_query("ORGANIZATION", None)
        assert "SET e:Organization" in query

    def test_query_includes_subtype_label_in_pascal_case(self):
        """Test that query includes subtype as PascalCase label when valid."""
        query = build_create_entity_query("OBJECT", "VEHICLE")
        assert "e:Object" in query
        assert "e:Vehicle" in query

        query = build_create_entity_query("PERSON", "INDIVIDUAL")
        assert "e:Person" in query
        assert "e:Individual" in query

        query = build_create_entity_query("LOCATION", "ADDRESS")
        assert "e:Location" in query
        assert "e:Address" in query

    def test_query_with_custom_type(self):
        """Test that custom types are added as PascalCase labels."""
        query = build_create_entity_query("PRODUCT", None)
        assert "MERGE (e:Entity" in query
        assert "SET e:Product" in query

        query = build_create_entity_query("SERVICE", None)
        assert "SET e:Service" in query

    def test_query_with_custom_type_and_subtype(self):
        """Test that custom types with subtypes have both as PascalCase labels."""
        query = build_create_entity_query("PRODUCT", "ELECTRONICS")
        assert "e:Product" in query
        assert "e:Electronics" in query

        query = build_create_entity_query("SERVICE", "SUBSCRIPTION")
        assert "e:Service" in query
        assert "e:Subscription" in query

    def test_query_with_invalid_label_format_no_label_set(self):
        """Test that invalid label format doesn't add label SET clause."""
        query = build_create_entity_query("has-dash", None)
        # Should still have valid query structure
        assert "MERGE (e:Entity" in query
        assert "RETURN e" in query
        # But no SET clause for labels (only ON CREATE/MATCH SET)
        lines = query.strip().split("\n")
        # The only SET should be within ON CREATE SET and ON MATCH SET
        set_lines = [l for l in lines if l.strip().startswith("SET e:")]
        assert len(set_lines) == 0

    def test_query_with_invalid_subtype_for_poleo_only_type_label(self):
        """Test that invalid subtype for POLE+O type still adds type label."""
        query = build_create_entity_query("PERSON", "INVALID_SUBTYPE")
        assert "SET e:Person" in query
        assert "Invalid" not in query

    def test_query_with_custom_type_invalid_subtype_format(self):
        """Test that custom type with invalid subtype format only has type label."""
        query = build_create_entity_query("CUSTOM", "has-dash")
        assert "SET e:Custom" in query
        assert "has-dash" not in query

    def test_all_pole_o_types(self):
        """Test query generation for all POLE+O types uses PascalCase."""
        expected_labels = {
            "PERSON": "Person",
            "OBJECT": "Object",
            "LOCATION": "Location",
            "EVENT": "Event",
            "ORGANIZATION": "Organization",
        }
        for entity_type in VALID_ENTITY_TYPES:
            query = build_create_entity_query(entity_type, None)
            expected = expected_labels[entity_type]
            assert f"SET e:{expected}" in query

    def test_sample_subtypes_for_each_type(self):
        """Test query generation for sample subtypes uses PascalCase."""
        test_cases = [
            ("PERSON", "INDIVIDUAL", "Person", "Individual"),
            ("OBJECT", "VEHICLE", "Object", "Vehicle"),
            ("LOCATION", "ADDRESS", "Location", "Address"),
            ("EVENT", "MEETING", "Event", "Meeting"),
            ("ORGANIZATION", "COMPANY", "Organization", "Company"),
        ]
        for entity_type, subtype, expected_type, expected_subtype in test_cases:
            query = build_create_entity_query(entity_type, subtype)
            assert f"e:{expected_type}" in query
            assert f"e:{expected_subtype}" in query


class TestValidSubtypesConsistency:
    """Tests to ensure VALID_SUBTYPES matches schema/models.py."""

    def test_all_pole_o_types_have_subtypes(self):
        """Test that all POLE+O types have subtype definitions."""
        for entity_type in VALID_ENTITY_TYPES:
            assert entity_type in VALID_SUBTYPES
            assert len(VALID_SUBTYPES[entity_type]) > 0

    def test_person_subtypes_complete(self):
        """Test that PERSON subtypes include expected values."""
        expected = {"INDIVIDUAL", "ALIAS", "PERSONA", "SUSPECT", "WITNESS", "VICTIM"}
        assert expected.issubset(VALID_SUBTYPES["PERSON"])

    def test_object_subtypes_complete(self):
        """Test that OBJECT subtypes include expected values."""
        expected = {"VEHICLE", "PHONE", "EMAIL", "DOCUMENT", "DEVICE", "WEAPON"}
        assert expected.issubset(VALID_SUBTYPES["OBJECT"])

    def test_location_subtypes_complete(self):
        """Test that LOCATION subtypes include expected values."""
        expected = {"ADDRESS", "CITY", "REGION", "COUNTRY", "LANDMARK", "FACILITY"}
        assert expected.issubset(VALID_SUBTYPES["LOCATION"])

    def test_event_subtypes_complete(self):
        """Test that EVENT subtypes include expected values."""
        expected = {"INCIDENT", "MEETING", "TRANSACTION", "COMMUNICATION"}
        assert expected.issubset(VALID_SUBTYPES["EVENT"])

    def test_organization_subtypes_complete(self):
        """Test that ORGANIZATION subtypes include expected values."""
        expected = {"COMPANY", "NONPROFIT", "GOVERNMENT", "EDUCATIONAL"}
        assert expected.issubset(VALID_SUBTYPES["ORGANIZATION"])


class TestOntologyLabel:
    """The ontology's declared label is added in PascalCase, keeping its capitals."""

    def test_pascal_case_keeps_existing_capitals(self):
        assert ontology_node_label("SupportCase") == "SupportCase"
        assert ontology_node_label("  Ticket ") == "Ticket"
        # The built-in templates declare GLiNER-style lowercase labels.
        assert ontology_node_label("company") == "Company"
        assert ontology_node_label("tv_show") == "TvShow"
        assert ontology_node_label("financial_metric") == "FinancialMetric"

    def test_what_cannot_be_a_label_is_rejected(self):
        assert ontology_node_label(None) is None
        assert ontology_node_label("") is None
        assert ontology_node_label("Support Case") is None
        assert ontology_node_label("X`) DETACH DELETE e //") is None

    def test_the_create_query_adds_the_ontology_label(self):
        query = build_create_entity_query("OBJECT", "TICKET", ontology_label="SupportCase")
        assert "SET e:Object, e:SupportCase" in query
        assert "Supportcase" not in query

    def test_a_label_equal_to_an_existing_label_is_not_repeated(self):
        assert build_label_set_clause("PERSON", None, ontology_label="person") == "SET e:Person"
        assert (
            build_label_set_clause("ORGANIZATION", "COMPANY", ontology_label="company")
            == "SET e:Organization, e:Company"
        )

    def test_an_invalid_ontology_label_is_skipped(self):
        clause = build_label_set_clause("OBJECT", "TICKET", ontology_label="Support Case")
        assert clause == "SET e:Object"
