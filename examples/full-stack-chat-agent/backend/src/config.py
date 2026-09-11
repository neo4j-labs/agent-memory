"""Application configuration settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

ExtractionMode = Literal["none", "local", "llm"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Neo4j Memory Graph Configuration
    # Defaults match ../docker-compose.yml so a literal quick start connects.
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_username: str = Field(default="neo4j")
    neo4j_password: SecretStr = Field(default=SecretStr("password"))

    # Neo4j News Graph Configuration (the read-only domain graph)
    news_graph_uri: str = Field(default="bolt://localhost:7687")
    news_graph_username: str = Field(default="neo4j")
    news_graph_password: SecretStr = Field(default=SecretStr("password"))
    news_graph_database: str = Field(default="neo4j")

    # Embedding model for news vector search. Must match whatever the
    # `article_embeddings` vector index was built with.
    news_embedding_model: str = Field(default="text-embedding-3-small")

    # Bounds on agent-authored Cypher (the `execute_cypher` tool).
    cypher_timeout_seconds: float = Field(default=15.0, gt=0)
    cypher_max_rows: int = Field(default=100, gt=0)

    # OpenAI Configuration
    openai_api_key: SecretStr = Field(default=SecretStr(""))

    # PydanticAI model string for the chat agent. On PydanticAI 2.x the
    # `openai:` prefix selects the Responses API; `openai-chat:` selects Chat
    # Completions. Other providers need the matching pydantic-ai-slim extra.
    agent_model: str = Field(default="openai:gpt-5-mini")

    # Entity extraction for long-term memory. See .env.example for the modes.
    extraction_mode: ExtractionMode = Field(default="local")

    # Provider Configuration (v0.3+)
    # Override these to swap the memory library's LLM/embedding provider
    # without touching code. Set LLM_MODEL=anthropic/... and
    # EMBEDDING_MODEL=BAAI/bge-small-en-v1.5 plus ANTHROPIC_API_KEY=sk-ant-...
    # to run on Anthropic + local embeddings. Empty defaults fall through to
    # the library's OpenAI defaults.
    llm_model: str = Field(default="")
    embedding_model: str = Field(default="")
    anthropic_api_key: SecretStr | None = Field(default=None)

    # Server Configuration
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    debug: bool = Field(default=True)
    cors_origins_str: str = Field(default="http://localhost:3000", alias="cors_origins")
    cors_origin_regex: str | None = Field(default=None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins_str.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
