"""Shared settings for the three Aura-backed memory tutorials."""

from aura_connection import aura_config

from neo4j_agent_memory import MemorySettings


def settings():
    return MemorySettings(
        backend="bolt",
        neo4j=aura_config(),
        embedding="openai/text-embedding-3-small",
        llm=None,
        extraction={"extractor_type": "none"},
        resolution={"strategy": "none"},
        geocoding={"enabled": False},
        enrichment={"enabled": False},
    )
