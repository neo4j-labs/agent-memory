"""Unit tests for the Strands SessionManager integration."""

from __future__ import annotations

import pytest

pytest.importorskip("strands")


class TestRetrievalConfig:
    def test_defaults(self) -> None:
        from neo4j_agent_memory.integrations.strands.session_manager import (
            Neo4jRetrievalConfig,
        )

        cfg = Neo4jRetrievalConfig()
        assert cfg.top_k == 10
        assert cfg.min_score == 0.2
        assert cfg.include_entities is True
        assert cfg.include_preferences is True
        assert cfg.include_facts is False
        assert cfg.context_tag == "user_context"
