"""Shared-brain demo: two Strands session managers, one graph.

Each agent gets its OWN Neo4jSessionManager (own session), but both
write into the SAME Neo4j database — so entities extracted from agent
A's conversation are retrievable by agent B via long-term search.

Runs without any LLM API key: we drive the SessionManager API directly
instead of invoking a hosted model. With AWS credentials you would pass
the manager to a real Agent:  Agent(model=..., session_manager=manager).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from pydantic import SecretStr

from neo4j_agent_memory import MemorySettings, Neo4jConfig
from neo4j_agent_memory.config.settings import ExtractionConfig, ExtractorType
from neo4j_agent_memory.integrations.strands import (
    Neo4jRetrievalConfig,
    Neo4jSessionManager,
)


def build_settings() -> MemorySettings:
    """Build settings with a local sentence-transformers embedder.

    Notes
    -----
    * ``llm=None`` keeps the example runnable without any LLM API key.
    * ``ExtractorType.NONE`` disables entity extraction, so the demo
      works whether or not spaCy / GLiNER extras are installed.
    * The embedding is set via the v0.3 provider-string shorthand —
      resolves to a local SentenceTransformersProvider (no network calls).
    """
    return MemorySettings(
        neo4j=Neo4jConfig(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USERNAME", "neo4j"),
            password=SecretStr(os.getenv("NEO4J_PASSWORD", "password")),
        ),
        llm=None,
        embedding="sentence-transformers/all-MiniLM-L6-v2",
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
    )


def main() -> None:
    agent_a = SimpleNamespace(messages=[], agent_id="kyc-agent")
    agent_b = SimpleNamespace(messages=[], agent_id="credit-agent")

    # Phase 1: Agent A learns something and persists it.
    with Neo4jSessionManager("kyc-session", settings=build_settings()) as manager_a:
        manager_a.initialize(agent_a)
        manager_a.append_message(
            {
                "role": "user",
                "content": [{"text": "Jane Doe is the beneficial owner of Acme Corp."}],
            },
            agent_a,
        )
        manager_a.append_message(
            {"role": "assistant", "content": [{"text": "Recorded the ownership link."}]},
            agent_a,
        )
    print("Agent A persisted 2 messages to session 'kyc-session'.")

    # Phase 2: Agent B (separate session!) gets agent A's knowledge injected.
    retrieval = Neo4jRetrievalConfig(top_k=5, min_score=0.1)
    with Neo4jSessionManager(
        "credit-session", settings=build_settings(), retrieval_config=retrieval
    ) as manager_b:
        manager_b.initialize(agent_b)
        question = {"role": "user", "content": [{"text": "What do we know about Acme Corp?"}]}
        manager_b.append_message(question, agent_b)
        manager_b._inject_context(question)  # what Agent(...) would do via hooks
        print("Agent B's question, with injected shared-brain context (if any):")
        print(question["content"][0]["text"])

    # Phase 3: Restore demo — a new manager instance restores agent A's history.
    with Neo4jSessionManager("kyc-session", settings=build_settings()) as manager_c:
        restored = SimpleNamespace(messages=[], agent_id="kyc-agent")
        manager_c.initialize(restored)
        print(f"Restored {len(restored.messages)} messages for 'kyc-session'.")


if __name__ == "__main__":
    main()
