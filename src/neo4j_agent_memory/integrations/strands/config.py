"""Strands-specific configuration for neo4j-agent-memory integration.

This module provides configuration helpers for integrating Neo4j Agent Memory
with AWS Strands Agents SDK.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory import MemorySettings
    from neo4j_agent_memory.nams.endpoints import TransportMode


@dataclass
class StrandsConfig:
    """Configuration for Strands Agents integration.

    This class provides a convenient way to configure the Context Graph tools
    for use with Strands agents. It supports loading configuration from
    environment variables with sensible defaults.

    Attributes:
        neo4j_uri: Neo4j connection URI.
        neo4j_user: Neo4j username.
        neo4j_password: Neo4j password.
        neo4j_database: Neo4j database name.
        embedding_provider: Embedding provider (bedrock, openai, vertex_ai).
        embedding_model: Optional embedding model override.
        aws_region: AWS region for Bedrock.
        aws_profile: AWS credentials profile.

    Example:
        from neo4j_agent_memory.integrations.strands import StrandsConfig, context_graph_tools

        # Load from environment
        config = StrandsConfig.from_env()

        # Or configure explicitly
        config = StrandsConfig(
            neo4j_uri="neo4j+s://xxx.databases.neo4j.io",
            neo4j_password="password",
            aws_region="us-west-2",
        )

        tools = context_graph_tools(**config.to_dict())
    """

    neo4j_uri: str
    neo4j_password: str
    neo4j_user: str = "neo4j"
    neo4j_database: str = "neo4j"
    embedding_provider: str = "bedrock"
    embedding_model: str | None = None
    aws_region: str | None = None
    aws_profile: str | None = None
    extra_config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        prefix: str = "",
        **overrides: Any,
    ) -> StrandsConfig:
        """Create configuration from environment variables.

        Environment variables (with optional prefix):
        - NEO4J_URI: Neo4j connection URI (required)
        - NEO4J_USER: Neo4j username (default: neo4j)
        - NEO4J_PASSWORD: Neo4j password (required)
        - NEO4J_DATABASE: Neo4j database (default: neo4j)
        - EMBEDDING_PROVIDER: Provider (default: bedrock)
        - EMBEDDING_MODEL: Model override
        - AWS_REGION: AWS region for Bedrock
        - AWS_PROFILE: AWS credentials profile

        Args:
            prefix: Optional prefix for environment variables (e.g., "APP_").
            **overrides: Override specific values.

        Returns:
            Configured StrandsConfig instance.

        Raises:
            ValueError: If required environment variables are missing.

        Example:
            # Uses NEO4J_URI, NEO4J_PASSWORD, etc.
            config = StrandsConfig.from_env()

            # Uses MYAPP_NEO4J_URI, MYAPP_NEO4J_PASSWORD, etc.
            config = StrandsConfig.from_env(prefix="MYAPP_")
        """

        def get_env(key: str, default: str | None = None) -> str | None:
            return os.environ.get(f"{prefix}{key}", default)

        neo4j_uri = overrides.get("neo4j_uri") or get_env("NEO4J_URI")
        neo4j_password = overrides.get("neo4j_password") or get_env("NEO4J_PASSWORD")

        if not neo4j_uri:
            raise ValueError(
                f"NEO4J_URI environment variable is required. "
                f"Set {prefix}NEO4J_URI or provide neo4j_uri parameter."
            )
        if not neo4j_password:
            raise ValueError(
                f"NEO4J_PASSWORD environment variable is required. "
                f"Set {prefix}NEO4J_PASSWORD or provide neo4j_password parameter."
            )

        return cls(
            neo4j_uri=neo4j_uri,
            neo4j_password=neo4j_password,
            neo4j_user=overrides.get("neo4j_user") or get_env("NEO4J_USER", "neo4j") or "neo4j",
            neo4j_database=overrides.get("neo4j_database")
            or get_env("NEO4J_DATABASE", "neo4j")
            or "neo4j",
            embedding_provider=overrides.get("embedding_provider")
            or get_env("EMBEDDING_PROVIDER", "bedrock")
            or "bedrock",
            embedding_model=overrides.get("embedding_model") or get_env("EMBEDDING_MODEL"),
            aws_region=overrides.get("aws_region") or get_env("AWS_REGION"),
            aws_profile=overrides.get("aws_profile") or get_env("AWS_PROFILE"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert configuration to a dictionary for context_graph_tools().

        Returns:
            Dictionary of configuration values.
        """
        result = {
            "neo4j_uri": self.neo4j_uri,
            "neo4j_user": self.neo4j_user,
            "neo4j_password": self.neo4j_password,
            "neo4j_database": self.neo4j_database,
            "embedding_provider": self.embedding_provider,
        }

        if self.embedding_model:
            result["embedding_model"] = self.embedding_model
        if self.aws_region:
            result["aws_region"] = self.aws_region
        if self.aws_profile:
            result["aws_profile"] = self.aws_profile

        result.update(self.extra_config)
        return result


# ---------------------------------------------------------------------------
# NAMS connection helpers (shared by session_manager and tools)
# ---------------------------------------------------------------------------

#: Default hosted NAMS endpoint, shared by tools and the session manager.
DEFAULT_NAMS_ENDPOINT = "https://memory.neo4jlabs.com/v1"


def resolve_nams_connection(
    endpoint: str | None = None,
    api_key: str | None = None,
) -> tuple[str, str]:
    """Resolve NAMS endpoint + api key from args or env (MEMORY_ENDPOINT / MEMORY_API_KEY).

    Raises:
        ValueError: If no API key is provided or found in the environment.
    """
    endpoint = endpoint or os.environ.get("MEMORY_ENDPOINT") or DEFAULT_NAMS_ENDPOINT
    api_key = api_key or os.environ.get("MEMORY_API_KEY")
    if not api_key:
        raise ValueError("api_key is required. Pass api_key= or set MEMORY_API_KEY env var.")
    return endpoint, api_key


def build_nams_settings(
    endpoint: str,
    api_key: str,
    transport_mode: TransportMode = "auto",
    *,
    validate_on_connect: bool = False,
) -> MemorySettings:
    """Build NAMS-backed MemorySettings (validate_on_connect off by default —
    Strands drives short synchronous bursts; skipping the probe saves a round-trip)."""
    from pydantic import SecretStr

    from neo4j_agent_memory import MemorySettings, NamsConfig

    return MemorySettings(
        backend="nams",
        nams=NamsConfig(
            endpoint=endpoint,
            api_key=SecretStr(api_key),
            validate_on_connect=validate_on_connect,
            transport_mode=transport_mode,
        ),
    )


# ---------------------------------------------------------------------------
# Default Bedrock models for different use cases
# ---------------------------------------------------------------------------

# Default Bedrock models for different use cases
BEDROCK_EMBEDDING_MODELS = {
    "titan-v2": "amazon.titan-embed-text-v2:0",  # Recommended, 1024 dimensions
    "titan-v1": "amazon.titan-embed-text-v1",  # 1536 dimensions
    "cohere-english": "cohere.embed-english-v3",  # 1024 dimensions
    "cohere-multilingual": "cohere.embed-multilingual-v3",  # 1024 dimensions
}

BEDROCK_LLM_MODELS = {
    "claude-sonnet": "anthropic.claude-sonnet-4-20250514-v1:0",
    "claude-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "claude-opus": "anthropic.claude-3-opus-20240229-v1:0",
}
