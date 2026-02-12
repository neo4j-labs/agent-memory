"""Application configuration settings."""

import sys
from functools import lru_cache

from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Neo4j Configuration
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_username: str = Field(default="neo4j")
    neo4j_password: SecretStr = Field(default=SecretStr("password"))

    # OpenAI Configuration (REQUIRED)
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="OpenAI API key - REQUIRED for embeddings and LLM features",
    )

    # Enrichment Configuration
    enrichment_enabled: bool = Field(default=True)  # Enable Wikipedia enrichment
    diffbot_api_key: SecretStr | None = Field(default=None)  # Optional Diffbot API key

    # Server Configuration
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    debug: bool = Field(default=True)
    cors_origins_str: str = Field(default="http://localhost:3000", alias="cors_origins")
    cors_origin_regex: str | None = Field(default=None)

    @field_validator("openai_api_key")
    @classmethod
    def validate_openai_api_key(cls, v: SecretStr) -> SecretStr:
        """Validate that OpenAI API key is set.
        
        The application requires an OpenAI API key for embeddings and LLM features.
        Set the OPENAI_API_KEY environment variable.
        """
        if not v or not v.get_secret_value() or v.get_secret_value().strip() == "":
            error_msg = (
                "\n\n"
                "=" * 70 + "\n"
                "ERROR: OPENAI_API_KEY is required but not set\n"
                "=" * 70 + "\n\n"
                "This application requires an OpenAI API key for:\n"
                "  • Text embeddings (semantic search)\n"
                "  • LLM-based entity extraction and enrichment\n"
                "  • Agent reasoning and tool use\n\n"
                "To fix this:\n"
                "  1. Get an API key from https://platform.openai.com/api-keys\n"
                "  2. Set it in backend/.env:\n"
                "       OPENAI_API_KEY=sk-your-key-here\n"
                "  3. Or set it as an environment variable:\n"
                "       export OPENAI_API_KEY=sk-your-key-here\n\n"
                "=" * 70 + "\n"
            )
            print(error_msg, file=sys.stderr)
            raise ValueError("OPENAI_API_KEY environment variable is required")
        
        # Basic format validation (should start with 'sk-')
        key = v.get_secret_value().strip()
        if not key.startswith("sk-"):
            print(
                "\nWARNING: OPENAI_API_KEY does not start with 'sk-'. "
                "This may not be a valid OpenAI API key.\n",
                file=sys.stderr,
            )
        
        return v

    @computed_field
    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins_str.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
