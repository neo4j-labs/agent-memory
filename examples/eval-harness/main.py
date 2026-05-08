"""Eval-harness example: tiny seed set + report.

Demonstrates the v0.2 evaluation harness — the building blocks for
labeled regression tests over memory quality (retrieval, audit, preference).

Run from the repo root::

    uv run python examples/eval-harness/main.py
"""

from __future__ import annotations

import asyncio
import os

from pydantic import SecretStr

from neo4j_agent_memory import MemoryClient, MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
)
from neo4j_agent_memory.memory.eval import (
    AuditCase,
    EvalSuite,
    PreferenceCase,
)
from neo4j_agent_memory.schema.models import EntityRef


def build_settings() -> MemorySettings:
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        llm=None,
        embedding=EmbeddingConfig(
            provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
            model="all-MiniLM-L6-v2",
            dimensions=384,
        ),
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
    )


async def seed(client: MemoryClient) -> dict:
    """Seed a deterministic minimal dataset and return the labels."""
    # Reset.
    await client.graph.execute_write(
        """
        MATCH (n) WHERE n.id STARTS WITH 'eval:' OR n.session_id = 'eval-demo'
        DETACH DELETE n
        """
    )

    # Seed: a user, a preference, a trace that touches an entity.
    await client.users.upsert_user(identifier="eval-user@demo")
    pref = await client.long_term.add_preference(
        "consultants",
        "Senior on healthcare",
        user_identifier="eval-user@demo",
    )

    trace = await client.reasoning.start_trace(
        "eval-demo", "Demo trace", user_identifier="eval-user@demo"
    )
    step = await client.reasoning.add_step(trace.id, thought="Look up Anthem")
    await client.reasoning.record_tool_call(
        step.id,
        tool_name="lookup_client",
        arguments={"name": "Anthem"},
        touched_entities=[EntityRef(name="Anthem", type="Client")],
    )

    rows = await client.graph.execute_read(
        "MATCH (e:Entity {name: 'Anthem'}) RETURN e.id AS id"
    )
    return {
        "user_id": "eval-user@demo",
        "active_pref_id": str(pref.id),
        "anthem_id": rows[0]["id"],
        "step_id": str(step.id),
    }


async def main() -> None:
    async with MemoryClient(build_settings()) as client:
        labels = await seed(client)

        # Define expectations: audit must have the step touching Anthem,
        # and the preference layer must surface only the active preference.
        suite = EvalSuite(
            audit=[
                AuditCase(
                    entity_id=labels["anthem_id"],
                    expected_step_ids={labels["step_id"]},
                ),
            ],
            preference=[
                PreferenceCase(
                    user_identifier=labels["user_id"],
                    expected_active_pref_ids={labels["active_pref_id"]},
                ),
            ],
        )

        report = await client.eval.run(suite)
        print("=== Eval report ===")
        print(f"Overall: {report.overall_score:.2f}")
        if report.audit:
            print(f"Audit:    cases={report.audit.cases} score={report.audit.score:.2f}")
        if report.preference:
            print(
                f"Pref:     cases={report.preference.cases} "
                f"score={report.preference.score:.2f}"
            )


if __name__ == "__main__":
    asyncio.run(main())
