"""Integration tests: Neo4jSessionManager against a real bolt backend."""

from __future__ import annotations

import uuid
import warnings

import pytest
from pydantic import SecretStr

pytest.importorskip("strands", reason="strands-agents not installed")

from neo4j_agent_memory import MemorySettings, Neo4jConfig  # noqa: E402
from neo4j_agent_memory.config.settings import (  # noqa: E402
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
)


@pytest.fixture
def strands_memory_settings(neo4j_connection_info):
    """Settings for Strands session-manager integration tests.

    Uses EmbeddingProvider.BEDROCK so that ``_create_embedder()`` returns
    ``None`` (no embedder configured) — avoids the OpenAI key requirement.
    ``ExtractorType.NONE`` skips the extraction pipeline for the same reason.
    This mirrors the approach taken by ``test_memory_client_no_llm.py``.
    """
    with warnings.catch_warnings():
        # Suppress the DeprecationWarning for legacy EmbeddingConfig usage;
        # we are intentionally using the old shape to force provider=BEDROCK
        # (no NONE enum exists) so the internal _create_embedder returns None.
        warnings.simplefilter("ignore", DeprecationWarning)
        return MemorySettings(
            neo4j=Neo4jConfig(
                uri=neo4j_connection_info["uri"],
                username=neo4j_connection_info["username"],
                password=SecretStr(neo4j_connection_info["password"]),
            ),
            embedding=EmbeddingConfig(provider=EmbeddingProvider.BEDROCK),
            extraction=ExtractionConfig(
                extractor_type=ExtractorType.NONE,
                enable_llm_fallback=False,
            ),
        )


@pytest.mark.integration
class TestSessionManagerRoundTrip:
    def test_persist_and_restore_across_manager_instances(self, strands_memory_settings) -> None:
        from types import SimpleNamespace

        from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

        session_id = f"strands-it-{uuid.uuid4().hex[:8]}"

        # First "process": persist two turns.
        with Neo4jSessionManager(
            session_id, settings=strands_memory_settings, extract_entities=False
        ) as m1:
            agent = SimpleNamespace(messages=[], agent_id="a1")
            m1.initialize(agent)
            m1.append_message({"role": "user", "content": [{"text": "I love Cypher"}]}, agent)
            m1.append_message({"role": "assistant", "content": [{"text": "Noted!"}]}, agent)
            # close() flushes the buffered second message.

        # Second "process": restore.
        with Neo4jSessionManager(
            session_id, settings=strands_memory_settings, extract_entities=False
        ) as m2:
            agent2 = SimpleNamespace(messages=[], agent_id="a1")
            m2.initialize(agent2)
            assert agent2.messages == [
                {"role": "user", "content": [{"text": "I love Cypher"}]},
                {"role": "assistant", "content": [{"text": "Noted!"}]},
            ]

    def test_redaction_before_flush_never_stores_original(self, strands_memory_settings) -> None:
        from types import SimpleNamespace

        from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

        session_id = f"strands-it-{uuid.uuid4().hex[:8]}"
        with Neo4jSessionManager(
            session_id, settings=strands_memory_settings, extract_entities=False
        ) as m:
            agent = SimpleNamespace(messages=[], agent_id="a1")
            m.initialize(agent)
            m.append_message({"role": "user", "content": [{"text": "SSN 123-45-6789"}]}, agent)
            m.redact_latest_message({"role": "user", "content": [{"text": "[REDACTED]"}]}, agent)

        with Neo4jSessionManager(
            session_id, settings=strands_memory_settings, extract_entities=False
        ) as m2:
            agent2 = SimpleNamespace(messages=[], agent_id="a1")
            m2.initialize(agent2)
            assert agent2.messages == [{"role": "user", "content": [{"text": "[REDACTED]"}]}]
