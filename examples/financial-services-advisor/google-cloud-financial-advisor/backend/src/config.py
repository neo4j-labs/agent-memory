"""Configuration for Google Cloud Financial Advisor.

This module provides settings management for the application using Pydantic Settings.
Configuration can be provided via environment variables or a .env file.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class VertexAISettings(BaseSettings):
    """Vertex AI configuration for LLM and embeddings."""

    model_config = SettingsConfigDict(
        env_prefix="VERTEX_AI_",
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_id: str | None = Field(
        default=None,
        description="Google Cloud Project ID. Falls back to GOOGLE_CLOUD_PROJECT.",
    )
    location: str = Field(
        default="us-central1",
        description="Google Cloud region for Vertex AI",
    )
    model_id: str = Field(
        default="gemini-2.5-flash",
        description=(
            "Gemini model for agent reasoning. Threaded through to every ADK "
            "agent factory and to the entity-extraction LLM."
        ),
    )
    embedding_model: str = Field(
        default="gemini-embedding-001",
        description="Vertex AI embedding model (text-embedding-004 was retired 2026-01-14)",
    )
    embedding_dimensions: int = Field(
        default=768,
        description=(
            "Output dimensionality requested from the embedding model. "
            "gemini-embedding-001 is natively 3072-dimensional and supports "
            "truncation; 768 keeps the Neo4j vector indexes the same shape as "
            "graphs built against the retired text-embedding-004."
        ),
    )

    def get_project_id(self) -> str:
        """Get the project ID, falling back to GOOGLE_CLOUD_PROJECT."""
        if self.project_id:
            return self.project_id
        return os.environ.get("GOOGLE_CLOUD_PROJECT", "")

    def use_vertex_ai(self) -> bool:
        """Whether google-genai / ADK should route through Vertex AI.

        google-genai selects Vertex AI from ``GOOGLE_GENAI_USE_VERTEXAI`` plus
        ``GOOGLE_CLOUD_PROJECT``/``GOOGLE_CLOUD_LOCATION``; this mirrors that
        decision so the extraction LLM provider string matches the agents.
        """
        flag = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower()
        return flag in {"1", "true", "yes"}

    def get_api_key(self) -> str | None:
        """Gemini API key for the Google AI Studio path (not Vertex AI)."""
        return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    def llm_provider_string(self) -> str | None:
        """Provider string for the entity-extraction LLM, or None if unusable.

        Resolves to the same Gemini model the agents use:

        * ``vertex_ai/<model>`` when the Vertex AI path is selected and a
          project id is available;
        * ``gemini/<model>`` when a Google AI Studio key is present;
        * ``None`` when neither credential is configured — the caller then
          disables extraction explicitly instead of silently falling back to
          a no-op extractor.
        """
        if self.use_vertex_ai() and self.get_project_id():
            return f"vertex_ai/{self.model_id}"
        if self.get_api_key():
            return f"gemini/{self.model_id}"
        return None


class Neo4jSettings(BaseSettings):
    """Neo4j Aura configuration."""

    model_config = SettingsConfigDict(
        env_prefix="NEO4J_",
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    uri: str = Field(
        default="bolt://localhost:7687",
        description="Neo4j connection URI",
    )
    user: str = Field(
        default="neo4j",
        description="Neo4j username",
    )
    password: SecretStr = Field(
        description="Neo4j password (set NEO4J_PASSWORD env var)",
    )
    database: str = Field(
        default="neo4j",
        description="Neo4j database name",
    )


class MemoryFeatureSettings(BaseSettings):
    """Neo4j Agent Memory feature configuration."""

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_",
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enable_extraction: bool = Field(
        default=True, description="Enable entity extraction from conversations"
    )
    enable_deduplication: bool = Field(
        default=True, description="Enable entity deduplication on ingest"
    )
    dedup_auto_merge_threshold: float = Field(
        default=0.95, description="Similarity threshold for automatic entity merge"
    )
    dedup_flag_threshold: float = Field(
        default=0.85, description="Similarity threshold for flagging potential duplicates"
    )


class Settings(BaseSettings):
    """Main application settings.

    Load configuration from environment variables and .env file.
    Nested settings are loaded from prefixed environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Nested settings
    vertex_ai: Annotated[VertexAISettings, Field(default_factory=VertexAISettings)]
    neo4j: Annotated[Neo4jSettings, Field(default_factory=Neo4jSettings)]
    memory_features: Annotated[MemoryFeatureSettings, Field(default_factory=MemoryFeatureSettings)]

    # Application settings
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )
    cors_origins: str = Field(
        default="http://localhost:5173",
        description="Comma-separated list of allowed CORS origins",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode",
    )

    def get_cors_origins_list(self) -> list[str]:
        """Parse CORS origins into a list."""
        return [origin.strip() for origin in self.cors_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Returns:
        Singleton Settings instance.
    """
    return Settings()
