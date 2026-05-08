"""Integration tests for the v0.5 P2.3 evaluation harness."""

from __future__ import annotations

import pytest

from neo4j_agent_memory.memory.eval import (
    AuditCase,
    EvalSuite,
    PreferenceCase,
)
from neo4j_agent_memory.schema.models import EntityRef


@pytest.mark.integration
@pytest.mark.asyncio
class TestEvalHarness:
    async def test_audit_dimension_computes_recall(self, clean_memory_client, session_id):
        client = clean_memory_client

        # Seed: one trace with two steps that both TOUCH the same entity.
        trace = await client.reasoning.start_trace(session_id, "Audit eval seed")
        step_a = await client.reasoning.add_step(trace.id, thought="step a")
        step_b = await client.reasoning.add_step(trace.id, thought="step b")

        await client.reasoning.record_tool_call(
            step_a.id,
            tool_name="lookup_client",
            arguments={"name": "Anthem"},
            touched_entities=[EntityRef(name="Anthem", type="Client")],
        )
        await client.reasoning.record_tool_call(
            step_b.id,
            tool_name="lookup_industry",
            arguments={"name": "Anthem"},
            touched_entities=[EntityRef(name="Anthem", type="Client")],
        )

        # The entity id is computed deterministically as `name:type` when
        # neither id nor an existing entity is matched.
        rows = await client.graph.execute_read(
            "MATCH (e:Entity {name: 'Anthem'}) RETURN e.id AS id"
        )
        anthem_id = rows[0]["id"]

        suite = EvalSuite(
            audit=[
                AuditCase(
                    entity_id=anthem_id,
                    expected_step_ids={str(step_a.id), str(step_b.id)},
                ),
            ],
        )
        report = await client.eval.run(suite, dimensions=["audit"])
        assert report.audit is not None
        assert report.audit.cases == 1
        assert report.audit.score == 1.0  # full coverage

    async def test_preference_dimension_uses_f1(self, clean_memory_client):
        client = clean_memory_client
        await client.users.upsert_user(identifier="sara@omg.com")

        old = await client.long_term.add_preference(
            "consultants", "Junior", user_identifier="sara@omg.com"
        )
        new = await client.long_term.add_preference(
            "consultants", "Senior", user_identifier="sara@omg.com"
        )
        await client.long_term.supersede_preference(old.id, new.id)

        # Active pref expectations should match exactly.
        suite = EvalSuite(
            preference=[
                PreferenceCase(
                    user_identifier="sara@omg.com",
                    expected_active_pref_ids={str(new.id)},
                ),
            ],
        )
        report = await client.eval.run(suite, dimensions=["preference"])
        assert report.preference is not None
        assert report.preference.cases == 1
        assert report.preference.score == 1.0

        # Same evaluation but with a wrong expected set — f1 should drop.
        suite_wrong = EvalSuite(
            preference=[
                PreferenceCase(
                    user_identifier="sara@omg.com",
                    expected_active_pref_ids={str(old.id)},  # superseded
                ),
            ],
        )
        report = await client.eval.run(suite_wrong, dimensions=["preference"])
        assert report.preference.score < 1.0

    async def test_overall_score_means_dimensions(self, clean_memory_client, session_id):
        client = clean_memory_client
        # Empty audit + perfect preference + skipped retrieval.
        await client.users.upsert_user(identifier="sara@omg.com")
        pref = await client.long_term.add_preference(
            "food", "Italian", user_identifier="sara@omg.com"
        )

        suite = EvalSuite(
            audit=[
                AuditCase(
                    entity_id="nonexistent-entity",
                    expected_step_ids={"x", "y"},  # never matched -> 0
                ),
            ],
            preference=[
                PreferenceCase(
                    user_identifier="sara@omg.com",
                    expected_active_pref_ids={str(pref.id)},
                ),
            ],
        )
        report = await client.eval.run(suite)
        # audit = 0.0, preference = 1.0 → overall = 0.5
        assert report.audit is not None
        assert report.audit.score == 0.0
        assert report.preference is not None
        assert report.preference.score == 1.0
        assert report.overall_score == pytest.approx(0.5, abs=0.01)
