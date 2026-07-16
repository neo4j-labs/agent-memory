"""Phase 1 smoke tests: Protocols are importable, runtime-checkable, and
declare the expected SPEC-tier methods.

These are not full conformance tests — Phase 3 (memory impls) and the
integration TCK suite verify behavior. Here we just assert the Protocol
shape so dependent code in later phases can rely on it.
"""

from __future__ import annotations

import inspect

from neo4j_agent_memory import (
    CypherQueryProtocol,
    LongTermProtocol,
    ReasoningProtocol,
    ShortTermProtocol,
)


def _method_names(proto: type) -> set[str]:
    """Return public method names declared on a Protocol class."""
    return {
        name
        for name, value in inspect.getmembers(proto, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


class TestShortTermProtocol:
    def test_runtime_checkable(self):
        # @runtime_checkable allows isinstance() against duck-typed impls.
        assert hasattr(ShortTermProtocol, "_is_runtime_protocol")
        assert ShortTermProtocol._is_runtime_protocol is True

    def test_bronze_methods_present(self):
        names = _method_names(ShortTermProtocol)
        for required in (
            "add_message",
            "get_conversation",
            "search_messages",
            "list_sessions",
        ):
            assert required in names, f"Bronze method missing: {required}"

    def test_silver_methods_present(self):
        names = _method_names(ShortTermProtocol)
        for required in (
            "delete_message",
            "clear_session",
            "get_context",
            "get_conversation_summary",
        ):
            assert required in names, f"Silver method missing: {required}"

    def test_gold_methods_present(self):
        names = _method_names(ShortTermProtocol)
        for required in ("create_conversation", "list_conversations"):
            assert required in names, f"Gold method missing: {required}"

    def test_platinum_methods_present(self):
        names = _method_names(ShortTermProtocol)
        for required in ("bulk_add_messages", "get_observations", "get_reflections"):
            assert required in names, f"Platinum method missing: {required}"


class TestLongTermProtocol:
    def test_runtime_checkable(self):
        assert LongTermProtocol._is_runtime_protocol is True

    def test_bronze_methods_present(self):
        names = _method_names(LongTermProtocol)
        for required in (
            "add_entity",
            "add_preference",
            "add_fact",
            "add_relationship",
            "search_entities",
            "search_preferences",
            "search_facts",
            "get_entity_by_name",
        ):
            assert required in names

    def test_silver_methods_present(self):
        # supersede_preference / get_entity_relationships are intentionally
        # absent: bolt and NAMS disagree on arity/return type for both (not
        # just an optional extra), so no single signature is honest for
        # both backends without an implementation change. See
        # LongTermProtocol's class-level comment and task-3-report.md.
        names = _method_names(LongTermProtocol)
        for required in (
            "get_related_entities",
            "get_preferences_for",
            "get_facts_about",
            "get_context",
        ):
            assert required in names

    def test_gold_methods_present(self):
        names = _method_names(LongTermProtocol)
        assert "get_entity_provenance" in names

    def test_platinum_methods_present(self):
        names = _method_names(LongTermProtocol)
        for required in ("set_entity_feedback", "get_entity_history"):
            assert required in names


class TestReasoningProtocol:
    def test_runtime_checkable(self):
        assert ReasoningProtocol._is_runtime_protocol is True

    def test_bronze_methods_present(self):
        names = _method_names(ReasoningProtocol)
        for required in (
            "start_trace",
            "add_step",
            "record_tool_call",
            "complete_trace",
        ):
            assert required in names

    def test_silver_methods_present(self):
        names = _method_names(ReasoningProtocol)
        for required in (
            "search_steps",
            "get_similar_traces",
            "get_trace",
            "get_trace_with_steps",
            "get_session_traces",
            "list_traces",
            "get_context",
        ):
            assert required in names

    def test_gold_methods_present(self):
        # get_tool_stats is intentionally absent: it's a bolt-only
        # capability (NAMS raises NotSupportedError unconditionally, and
        # bolt/NAMS disagree on the aggregate-stats shape). It belongs on
        # a bolt-specific sub-protocol in a later task, not the base
        # contract. See task-4-report.md.
        names = _method_names(ReasoningProtocol)
        assert "link_trace_to_message" in names


class TestCypherQueryProtocol:
    def test_runtime_checkable(self):
        assert CypherQueryProtocol._is_runtime_protocol is True

    def test_cypher_method_present(self):
        names = _method_names(CypherQueryProtocol)
        assert "cypher" in names

    def test_cypher_signature(self):
        sig = inspect.signature(CypherQueryProtocol.cypher)
        params = list(sig.parameters.keys())
        assert "query" in params
        assert "params" in params


class TestBoltImplsImplementProtocols:
    """Sanity check: existing bolt memory classes structurally satisfy the Protocols.

    @runtime_checkable Protocol isinstance() checks method presence (not
    signatures), so this catches the most basic structural mismatch — e.g.
    a Protocol that names a method the bolt impl doesn't have.
    """

    def test_short_term_memory_satisfies_protocol(self):
        from neo4j_agent_memory.memory.short_term import ShortTermMemory

        # We can't easily instantiate ShortTermMemory without dependencies,
        # but we can check class-level method presence. As of the type-safety
        # Phase 5 audit, bolt implements the *full* protocol — the Gold-tier
        # conversation methods are real, the Platinum-tier
        # observation/reflection methods raise NotSupportedError — so no
        # method may be missing.
        proto_methods = _method_names(ShortTermProtocol)
        impl_methods = {name for name in dir(ShortTermMemory) if not name.startswith("_")}
        missing = proto_methods - impl_methods
        assert not missing, f"ShortTermMemory missing protocol methods: {missing}"

    def test_long_term_memory_satisfies_protocol(self):
        from neo4j_agent_memory.memory.long_term import LongTermMemory

        proto_methods = _method_names(LongTermProtocol)
        impl_methods = {name for name in dir(LongTermMemory) if not name.startswith("_")}
        missing = proto_methods - impl_methods
        assert not missing, f"LongTermMemory missing protocol methods: {missing}"

    def test_reasoning_memory_satisfies_protocol(self):
        from neo4j_agent_memory.memory.reasoning import ReasoningMemory

        proto_methods = _method_names(ReasoningProtocol)
        impl_methods = {name for name in dir(ReasoningMemory) if not name.startswith("_")}
        missing = proto_methods - impl_methods
        assert not missing, f"ReasoningMemory missing protocol methods: {missing}"
