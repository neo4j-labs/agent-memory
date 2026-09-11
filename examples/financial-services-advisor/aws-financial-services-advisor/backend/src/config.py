"""Configuration management for Financial Services Advisor."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from neo4j_agent_memory.integrations.strands import bedrock_embedding_model, bedrock_llm_model
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _secret_values(secret_arn: str) -> dict[str, str]:
    """Fetch a JSON secret from AWS Secrets Manager.

    Used by the CDK deployment, which passes ``NEO4J_SECRET_ARN`` instead of the
    individual variables so the password never appears in the function's
    environment. Returns ``{}`` on any failure — the caller then falls back to
    the environment, and the lifespan reports the connection error.
    """
    try:
        import boto3

        client = boto3.client("secretsmanager")
        payload = client.get_secret_value(SecretId=secret_arn)["SecretString"]
        values = json.loads(payload)
        return {str(k): str(v) for k, v in values.items()} if isinstance(values, dict) else {}
    except Exception as exc:  # pragma: no cover - requires AWS
        logger.warning("Could not read secret %s: %s", secret_arn, exc)
        return {}


class Neo4jSettings(BaseSettings):
    """Neo4j database configuration.

    Reads ``NEO4J_URI`` / ``NEO4J_USER`` / ``NEO4J_PASSWORD`` /
    ``NEO4J_DATABASE`` from the environment, or — when ``NEO4J_SECRET_ARN`` is
    set, as it is under the CDK deployment — from that Secrets Manager secret's
    ``uri`` / ``username`` / ``password`` / ``database`` keys.
    """

    model_config = SettingsConfigDict(env_prefix="NEO4J_")

    uri: str = Field(default="bolt://localhost:7687", description="Neo4j connection URI")
    user: str = Field(default="neo4j", description="Neo4j username")
    password: str = Field(default="password", description="Neo4j password")
    database: str = Field(default="neo4j", description="Neo4j database name")

    @model_validator(mode="after")
    def _apply_secret(self) -> Neo4jSettings:
        secret_arn = os.environ.get("NEO4J_SECRET_ARN")
        if not secret_arn:
            return self
        values = _secret_values(secret_arn)
        for field, key in (
            ("uri", "uri"),
            ("user", "username"),
            ("password", "password"),
            ("database", "database"),
        ):
            if values.get(key):
                object.__setattr__(self, field, values[key])
        return self


class BedrockSettings(BaseSettings):
    """Amazon Bedrock configuration.

    Defaults come from the library's Strands helpers rather than literals:
    ``bedrock_llm_model()`` returns a cross-region *inference-profile* id
    (``us.anthropic.claude-sonnet-4-6``), which is how current Claude models are
    invoked on Bedrock — the bare foundation-model id is rejected. Both helpers
    honour ``BEDROCK_MODEL_ID`` / ``BEDROCK_EMBEDDING_MODEL_ID`` (the same
    variables this class reads) and ``BEDROCK_INFERENCE_PROFILE_PREFIX`` picks
    the ``us`` / ``eu`` / ``apac`` / ``global`` profile for your region.

    What a given AWS account and region can actually invoke is narrower than
    any default: confirm with ``aws bedrock list-inference-profiles``.
    """

    model_config = SettingsConfigDict(env_prefix="BEDROCK_")

    model_id: str = Field(
        default_factory=bedrock_llm_model,
        description="Bedrock inference-profile ID for the LLM",
    )
    embedding_model_id: str = Field(
        default_factory=bedrock_embedding_model,
        description="Bedrock model ID for embeddings",
    )
    region: str = Field(default="us-east-1", description="AWS region for Bedrock")


class AWSSettings(BaseSettings):
    """AWS general configuration."""

    model_config = SettingsConfigDict(env_prefix="AWS_")

    region: str = Field(default="us-east-1", description="AWS region")
    profile: str | None = Field(default=None, description="AWS profile name")
    access_key_id: str | None = Field(default=None, description="AWS access key ID")
    secret_access_key: str | None = Field(default=None, description="AWS secret access key")


class CognitoSettings(BaseSettings):
    """Amazon Cognito configuration."""

    model_config = SettingsConfigDict(env_prefix="COGNITO_")

    user_pool_id: str | None = Field(default=None, description="Cognito User Pool ID")
    client_id: str | None = Field(default=None, description="Cognito Client ID")


class S3Settings(BaseSettings):
    """Amazon S3 configuration."""

    model_config = SettingsConfigDict(env_prefix="S3_")

    bucket_name: str = Field(
        default="financial-advisor-documents",
        description="S3 bucket for document storage",
    )
    region: str = Field(default="us-east-1", description="S3 bucket region")


class MemoryFeatureSettings(BaseSettings):
    """Neo4j Agent Memory feature configuration."""

    model_config = SettingsConfigDict(env_prefix="MEMORY_")

    enable_extraction: bool = Field(
        default=True, description="Enable entity extraction from conversations"
    )


class AppSettings(BaseSettings):
    """Application-level settings."""

    log_level: str = Field(default="INFO", description="Logging level")
    allow_degraded_start: bool = Field(
        default=False,
        description=(
            "Start the API even when Neo4j is unreachable. Off by default so a "
            "typo in NEO4J_URI fails loudly instead of producing a server that "
            "reports healthy and 503s every domain route."
        ),
    )
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:3000",
        description="Comma-separated list of allowed CORS origins",
    )
    debug: bool = Field(default=False, description="Enable debug mode")

    # Feature flags
    enable_sanctions_check: bool = Field(default=True, description="Enable sanctions checking")
    enable_pep_check: bool = Field(default=True, description="Enable PEP checking")
    enable_adverse_media: bool = Field(default=True, description="Enable adverse media screening")

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",")]


class Settings(BaseSettings):
    """Main settings container."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
    bedrock: BedrockSettings = Field(default_factory=BedrockSettings)
    aws: AWSSettings = Field(default_factory=AWSSettings)
    cognito: CognitoSettings = Field(default_factory=CognitoSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    app: AppSettings = Field(default_factory=AppSettings)
    memory_features: MemoryFeatureSettings = Field(default_factory=MemoryFeatureSettings)

    def to_strands_config_dict(self) -> dict[str, Any]:
        """Convert settings to Strands integration config format."""
        return {
            "neo4j_uri": self.neo4j.uri,
            "neo4j_user": self.neo4j.user,
            "neo4j_password": self.neo4j.password,
            "neo4j_database": self.neo4j.database,
            "embedding_provider": "bedrock",
            "embedding_model": self.bedrock.embedding_model_id,
            "aws_region": self.aws.region,
        }

    def to_memory_settings_dict(self) -> dict[str, Any]:
        """Convert settings to MemorySettings format."""
        return {
            "neo4j": {
                "uri": self.neo4j.uri,
                "username": self.neo4j.user,
                "password": self.neo4j.password,
                "database": self.neo4j.database,
            },
            "embedding": {
                "provider": "bedrock",
                "model": self.bedrock.embedding_model_id,
                "aws_region": self.aws.region,
            },
        }


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
