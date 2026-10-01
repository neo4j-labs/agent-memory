"""Application settings, read from the environment and ``backend/.env``.

The Neo4j credentials have no defaults in code: point the app at a database on
purpose. ``.env.example`` holds the values for the repository's Docker Neo4j.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
#: ``examples/support-desk-agent/data`` — the Arrows ontology and the seed conversations.
DATA_DIR = BACKEND_DIR.parent / "data"

#: ``AGENT_MODEL`` value that selects PydanticAI's keyless ``TestModel``.
TEST_MODEL = "test"


class Settings(BaseSettings):
    """Settings for the API and the seed script."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    neo4j_uri: str
    neo4j_username: str
    neo4j_password: SecretStr
    neo4j_database: str = "neo4j"

    # Any PydanticAI model string. ``test`` selects PydanticAI's TestModel: it
    # calls every tool once with placeholder arguments and returns a JSON dump
    # of the results. Keyless, for smoke tests and CI. It is not a real agent.
    agent_model: str = "openai:gpt-5-mini"
    openai_api_key: SecretStr = Field(default=SecretStr(""))

    # The local embedder for messages, entities and reasoning-trace tasks.
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    cors_origins_raw: str = Field(default="http://localhost:3000", alias="cors_origins")

    @property
    def cors_origins(self) -> list[str]:
        """``CORS_ORIGINS`` as a list (comma-separated in the environment)."""
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]

    @property
    def uses_test_model(self) -> bool:
        """Whether ``AGENT_MODEL`` selects the keyless TestModel."""
        return self.agent_model.strip().lower() == TEST_MODEL


@lru_cache
def get_settings() -> Settings:
    """The process-wide settings (cached; tests call ``cache_clear()``)."""
    # The required fields come from the environment, which mypy cannot see.
    return Settings()  # type: ignore[call-arg]
