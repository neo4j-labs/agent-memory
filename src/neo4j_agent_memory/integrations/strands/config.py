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
    Strands drives short synchronous bursts; skipping the probe saves a round-trip).

    Args:
        endpoint: NAMS base URL.
        api_key: NAMS API key.
        transport_mode: MCP / REST transport selection.
        validate_on_connect: Whether to probe the service on connect.
    """
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
#
# Model ids rot. Both maps below are *defaults*, not a catalogue: resolve them
# through `bedrock_llm_model()` / `bedrock_embedding_model()` so a deployment can
# override the id from the environment without a library release.
#
# Verification source for the Claude ids: the Bedrock section of
# `strands.models._defaults._CONTEXT_WINDOW_LIMITS` plus
# `strands.models.bedrock.DEFAULT_BEDROCK_MODEL_ID`, read from the
# strands-agents release this package pins (1.55.1). Those are the ids the SDK
# we hand them to treats as known-good. What a *specific AWS account and
# region* can invoke is narrower and not checkable from here — confirm with
# `aws bedrock list-inference-profiles` / `aws bedrock list-foundation-models`
# before relying on a default.
# ---------------------------------------------------------------------------

#: Env var overriding the resolved Bedrock LLM id (see `bedrock_llm_model`).
BEDROCK_MODEL_ID_ENV = "BEDROCK_MODEL_ID"

#: Env var overriding the resolved Bedrock embedding id.
BEDROCK_EMBEDDING_MODEL_ID_ENV = "BEDROCK_EMBEDDING_MODEL_ID"

#: Env var overriding the cross-region inference-profile prefix.
BEDROCK_INFERENCE_PROFILE_PREFIX_ENV = "BEDROCK_INFERENCE_PROFILE_PREFIX"

#: Cross-region inference-profile prefix applied to the Claude ids below.
#: Current-generation Claude models on Bedrock are invoked through a
#: cross-region inference profile — a ``<prefix>.anthropic.…`` id — rather than
#: the bare foundation-model id. Valid prefixes are region-dependent ("us",
#: "eu", "apac", "global"); strands' own default model id uses "global".
DEFAULT_BEDROCK_INFERENCE_PROFILE_PREFIX = "us"

#: Unprefixed Claude ids by family alias. Keys are stable; values track the
#: current generation and are expected to change between releases.
BEDROCK_CLAUDE_BASE_MODELS = {
    # strands' own DEFAULT_BEDROCK_MODEL_ID family, hence the safest default.
    "claude-sonnet": "anthropic.claude-sonnet-4-6",
    "claude-opus": "anthropic.claude-opus-5",
    "claude-haiku": "anthropic.claude-haiku-4-5-20251001-v1:0",
}

#: Bedrock embedding ids by alias. Only the payload shapes
#: ``neo4j_agent_memory.embeddings.bedrock.BedrockEmbedder`` knows how to build
#: are listed — Titan text and Cohere embed v3. Amazon's Nova multimodal
#: embedding models use a different request/response body and are deliberately
#: absent: listing one here would hand callers an id the embedder cannot drive.
BEDROCK_EMBEDDING_MODELS = {
    "titan-v2": "amazon.titan-embed-text-v2:0",  # Recommended, 1024 dimensions
    "titan-v1": "amazon.titan-embed-text-v1",  # Legacy (v1 generation), 1536 dimensions
    "cohere-english": "cohere.embed-english-v3",  # 1024 dimensions
    "cohere-multilingual": "cohere.embed-multilingual-v3",  # 1024 dimensions
}

#: Default Bedrock LLM ids, inference-profile-prefixed with
#: ``DEFAULT_BEDROCK_INFERENCE_PROFILE_PREFIX``. Prefer `bedrock_llm_model()`,
#: which honours the environment overrides above.
BEDROCK_LLM_MODELS = {
    alias: f"{DEFAULT_BEDROCK_INFERENCE_PROFILE_PREFIX}.{base}"
    for alias, base in BEDROCK_CLAUDE_BASE_MODELS.items()
}


def bedrock_llm_model(alias: str = "claude-sonnet") -> str:
    """Resolve a Bedrock Claude inference-profile id.

    Precedence: ``BEDROCK_MODEL_ID`` (used verbatim, prefix included), then the
    ``BEDROCK_INFERENCE_PROFILE_PREFIX``-prefixed default for ``alias``.

    Args:
        alias: Key in :data:`BEDROCK_CLAUDE_BASE_MODELS`.

    Raises:
        KeyError: If ``alias`` is unknown and no ``BEDROCK_MODEL_ID`` is set.
    """
    override = os.environ.get(BEDROCK_MODEL_ID_ENV)
    if override:
        return override
    base = BEDROCK_CLAUDE_BASE_MODELS[alias]
    prefix = (
        os.environ.get(BEDROCK_INFERENCE_PROFILE_PREFIX_ENV)
        or DEFAULT_BEDROCK_INFERENCE_PROFILE_PREFIX
    )
    return f"{prefix}.{base}"


def bedrock_embedding_model(alias: str = "titan-v2") -> str:
    """Resolve a Bedrock embedding model id.

    Precedence: ``BEDROCK_EMBEDDING_MODEL_ID``, then the default for ``alias``.
    Embedding models are not invoked through inference profiles, so no prefix is
    applied.

    Args:
        alias: Key in :data:`BEDROCK_EMBEDDING_MODELS`.

    Raises:
        KeyError: If ``alias`` is unknown and no ``BEDROCK_EMBEDDING_MODEL_ID``
            is set.
    """
    override = os.environ.get(BEDROCK_EMBEDDING_MODEL_ID_ENV)
    if override:
        return override
    return BEDROCK_EMBEDDING_MODELS[alias]
