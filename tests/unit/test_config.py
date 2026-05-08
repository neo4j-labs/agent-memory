"""Unit tests for configuration."""

import pytest
from pydantic import SecretStr, ValidationError

from neo4j_agent_memory.config.settings import (
    EmbeddingConfig,
    EmbeddingProvider,
    ExtractionConfig,
    ExtractorType,
    GeocodingConfig,
    GeocodingProvider,
    LLMConfig,
    MemorySettings,
    Neo4jConfig,
    ResolutionConfig,
    ResolverStrategy,
)


class TestNeo4jConfig:
    """Tests for Neo4j configuration."""

    def test_default_values(self):
        """Test default configuration values."""
        config = Neo4jConfig(password=SecretStr("test"))

        assert config.uri == "bolt://localhost:7687"
        assert config.username == "neo4j"
        assert config.database == "neo4j"
        assert config.max_connection_pool_size == 50

    def test_custom_values(self):
        """Test custom configuration values."""
        config = Neo4jConfig(
            uri="bolt://custom:7688",
            username="admin",
            password=SecretStr("secret"),
            database="mydb",
        )

        assert config.uri == "bolt://custom:7688"
        assert config.username == "admin"
        assert config.password.get_secret_value() == "secret"
        assert config.database == "mydb"


class TestEmbeddingConfig:
    """Tests for embedding configuration."""

    def test_default_values(self):
        """Test default embedding config."""
        config = EmbeddingConfig()

        assert config.provider == EmbeddingProvider.OPENAI
        assert config.model == "text-embedding-3-small"
        assert config.dimensions == 1536

    def test_sentence_transformers_config(self):
        """Test sentence transformers config."""
        config = EmbeddingConfig(
            provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
            model="all-MiniLM-L6-v2",
            dimensions=384,
            device="cuda",
        )

        assert config.provider == EmbeddingProvider.SENTENCE_TRANSFORMERS
        assert config.device == "cuda"


class TestExtractionConfig:
    """Tests for extraction configuration."""

    def test_default_values(self):
        """Test default extraction config."""
        config = ExtractionConfig()

        # Default is now PIPELINE (multi-stage extraction)
        assert config.extractor_type == ExtractorType.PIPELINE
        assert "PERSON" in config.entity_types
        assert config.extract_relations is True
        # Pipeline settings
        assert config.enable_spacy is True
        assert config.enable_gliner is True
        assert config.enable_llm_fallback is True

    def test_gliner_config(self):
        """Test GLiNER extraction config."""
        config = ExtractionConfig(
            extractor_type=ExtractorType.GLINER,
            gliner_model="urchade/gliner_base",
            gliner_threshold=0.6,
        )

        assert config.extractor_type == ExtractorType.GLINER
        assert config.gliner_threshold == 0.6


class TestResolutionConfig:
    """Tests for resolution configuration."""

    def test_default_values(self):
        """Test default resolution config."""
        config = ResolutionConfig()

        assert config.strategy == ResolverStrategy.COMPOSITE
        assert config.fuzzy_threshold == 0.85
        assert config.semantic_threshold == 0.8

    def test_custom_thresholds(self):
        """Test custom thresholds."""
        config = ResolutionConfig(
            strategy=ResolverStrategy.FUZZY,
            fuzzy_threshold=0.9,
        )

        assert config.strategy == ResolverStrategy.FUZZY
        assert config.fuzzy_threshold == 0.9


class TestGeocodingConfig:
    """Tests for geocoding configuration."""

    def test_default_values(self):
        """Test default geocoding config."""
        config = GeocodingConfig()

        assert config.enabled is False
        assert config.provider == GeocodingProvider.NOMINATIM
        assert config.api_key is None
        assert config.cache_results is True
        assert config.rate_limit_per_second == 1.0
        assert config.user_agent == "neo4j-agent-memory"

    def test_google_provider(self):
        """Test Google provider configuration."""
        config = GeocodingConfig(
            enabled=True,
            provider=GeocodingProvider.GOOGLE,
            api_key=SecretStr("test-api-key"),
        )

        assert config.provider == GeocodingProvider.GOOGLE
        assert config.api_key is not None
        assert config.api_key.get_secret_value() == "test-api-key"

    def test_nominatim_with_custom_rate_limit(self):
        """Test Nominatim with custom rate limit."""
        config = GeocodingConfig(
            enabled=True,
            provider=GeocodingProvider.NOMINATIM,
            rate_limit_per_second=0.5,
            user_agent="my-app/1.0",
        )

        assert config.rate_limit_per_second == 0.5
        assert config.user_agent == "my-app/1.0"


class TestMemorySettings:
    """Tests for main settings class."""

    def test_minimal_settings(self):
        """Test creating settings with minimal config."""
        settings = MemorySettings(neo4j=Neo4jConfig(password=SecretStr("test")))

        assert settings.neo4j.password.get_secret_value() == "test"
        assert settings.embedding.provider == EmbeddingProvider.OPENAI

    def test_full_settings(self):
        """Test creating settings with full config."""
        settings = MemorySettings(
            neo4j=Neo4jConfig(
                uri="bolt://custom:7687",
                password=SecretStr("secret"),
            ),
            embedding=EmbeddingConfig(
                provider=EmbeddingProvider.SENTENCE_TRANSFORMERS,
                model="all-MiniLM-L6-v2",
            ),
            extraction=ExtractionConfig(
                extractor_type=ExtractorType.NONE,
            ),
            resolution=ResolutionConfig(
                strategy=ResolverStrategy.EXACT,
            ),
        )

        assert settings.neo4j.uri == "bolt://custom:7687"
        assert settings.embedding.provider == EmbeddingProvider.SENTENCE_TRANSFORMERS
        assert settings.extraction.extractor_type == ExtractorType.NONE
        assert settings.resolution.strategy == ResolverStrategy.EXACT

    def test_from_dict(self):
        """Test creating settings from dictionary."""
        config_dict = {
            "neo4j": {
                "uri": "bolt://localhost:7687",
                "password": "test",
            }
        }

        settings = MemorySettings.from_dict(config_dict)

        assert settings.neo4j.uri == "bolt://localhost:7687"

    def test_geocoding_config_default(self):
        """Test that geocoding config has sensible defaults."""
        settings = MemorySettings(neo4j=Neo4jConfig(password=SecretStr("test")))

        assert settings.geocoding.enabled is False
        assert settings.geocoding.provider == GeocodingProvider.NOMINATIM

    def test_settings_with_geocoding(self):
        """Test creating settings with geocoding enabled."""
        settings = MemorySettings(
            neo4j=Neo4jConfig(password=SecretStr("test")),
            geocoding=GeocodingConfig(
                enabled=True,
                provider=GeocodingProvider.GOOGLE,
                api_key=SecretStr("google-api-key"),
            ),
        )

        assert settings.geocoding.enabled is True
        assert settings.geocoding.provider == GeocodingProvider.GOOGLE
        assert settings.geocoding.api_key.get_secret_value() == "google-api-key"


class TestMemorySettingsOptionalLLM:
    """Tests for the optional `llm` field on MemorySettings."""

    def test_llm_none_with_spacy_only_extractor(self):
        """T1: explicit llm=None with a non-LLM extractor is accepted."""
        settings = MemorySettings(
            neo4j=Neo4jConfig(password=SecretStr("test")),
            llm=None,
            extraction=ExtractionConfig(
                extractor_type=ExtractorType.SPACY,
                enable_llm_fallback=False,
            ),
        )
        assert settings.llm is None

    def test_default_settings_auto_fill_llm(self):
        """T2: omitting `llm` preserves the historical default LLMConfig.

        The default ExtractionConfig has enable_llm_fallback=True, so the
        validator's lenient branch fills in a default LLMConfig.
        """
        settings = MemorySettings(neo4j=Neo4jConfig(password=SecretStr("test")))
        assert isinstance(settings.llm, LLMConfig)

    def test_default_llm_skipped_when_not_needed(self):
        """T2b: omitting `llm` and using a non-LLM extractor leaves llm=None.

        No auto-fill when no LLM stage is enabled — avoids surprise OpenAI
        client construction in air-gapped/no-key environments.
        """
        settings = MemorySettings(
            neo4j=Neo4jConfig(password=SecretStr("test")),
            extraction=ExtractionConfig(
                extractor_type=ExtractorType.SPACY,
                enable_llm_fallback=False,
            ),
        )
        assert settings.llm is None

    def test_llm_none_with_llm_extractor_raises(self):
        """T3: llm=None + extractor_type=LLM is a ValidationError."""
        with pytest.raises(ValidationError) as excinfo:
            MemorySettings(
                neo4j=Neo4jConfig(password=SecretStr("test")),
                llm=None,
                extraction=ExtractionConfig(extractor_type=ExtractorType.LLM),
            )
        msg = str(excinfo.value)
        assert "llm" in msg
        assert "extractor_type" in msg or "LLM" in msg

    def test_llm_none_with_fallback_enabled_raises(self):
        """T4: llm=None + enable_llm_fallback=True is a ValidationError."""
        with pytest.raises(ValidationError) as excinfo:
            MemorySettings(
                neo4j=Neo4jConfig(password=SecretStr("test")),
                llm=None,
                extraction=ExtractionConfig(
                    extractor_type=ExtractorType.PIPELINE,
                    enable_llm_fallback=True,
                ),
            )
        msg = str(excinfo.value)
        assert "llm" in msg
        assert "enable_llm_fallback" in msg

    def test_llm_none_with_pipeline_no_fallback_ok(self):
        """T4b: pipeline with LLM fallback disabled accepts llm=None."""
        settings = MemorySettings(
            neo4j=Neo4jConfig(password=SecretStr("test")),
            llm=None,
            extraction=ExtractionConfig(
                extractor_type=ExtractorType.PIPELINE,
                enable_spacy=True,
                enable_gliner=True,
                enable_llm_fallback=False,
            ),
        )
        assert settings.llm is None
        assert settings.extraction.enable_llm_fallback is False

    def test_explicit_llm_config_passes_through(self):
        """User-provided LLMConfig is preserved verbatim."""
        custom = LLMConfig(model="gpt-4o", api_key=SecretStr("sk-test"))
        settings = MemorySettings(
            neo4j=Neo4jConfig(password=SecretStr("test")),
            llm=custom,
        )
        assert settings.llm is custom


class TestStrictExtraFields:
    """Tests for `extra="forbid"` on MemorySettings and child configs.

    Pre-0.2 the configuration models silently dropped misspelled fields,
    leading to issues like ``MemorySettings(schema=SchemaConfig(...))``
    constructing a default schema_config without the user noticing. v0.2
    made the configuration strict.
    """

    def test_misspelled_top_level_field_rejected(self):
        """``schema=`` is the canonical typo bug — verify it's caught."""
        from neo4j_agent_memory.config.settings import SchemaConfig

        with pytest.raises(ValidationError) as excinfo:
            MemorySettings(
                neo4j=Neo4jConfig(password=SecretStr("test")),
                schema=SchemaConfig(),  # type: ignore[call-arg]
            )
        assert "schema" in str(excinfo.value)

    def test_unknown_top_level_field_rejected(self):
        with pytest.raises(ValidationError):
            MemorySettings(
                neo4j=Neo4jConfig(password=SecretStr("test")),
                completely_made_up=42,  # type: ignore[call-arg]
            )

    def test_unknown_field_rejected_on_neo4j_config(self):
        with pytest.raises(ValidationError):
            Neo4jConfig(password=SecretStr("test"), bogus=True)  # type: ignore[call-arg]

    def test_unknown_field_rejected_on_extraction_config(self):
        with pytest.raises(ValidationError):
            ExtractionConfig(enable_gliner_relations=True)  # type: ignore[call-arg]

    def test_unknown_field_rejected_on_geocoding_config(self):
        with pytest.raises(ValidationError):
            GeocodingConfig(provider_name="nominatim")  # type: ignore[call-arg]


class TestDotEnvFiltering:
    """Tests for `.env` keys outside ``NAM_`` prefix being silently ignored.

    pydantic-settings 2.x leaks unmatched ``.env`` keys into the validation
    payload even when ``env_prefix`` is set, which collides with
    ``extra="forbid"``. ``MemorySettings.settings_customise_sources`` wraps
    the dotenv source to filter to known top-level fields only.
    """

    def test_unprefixed_env_keys_ignored(self, tmp_path, monkeypatch):
        """Plain ``NEO4J_URI`` / ``OPENAI_API_KEY`` in .env must not raise."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "NEO4J_URI=neo4j://leaked:7687\n"
            "NEO4J_USERNAME=leaked\n"
            "NEO4J_PASSWORD=leaked\n"
            "OPENAI_API_KEY=sk-leaked\n"
        )
        monkeypatch.chdir(tmp_path)

        settings = MemorySettings(neo4j=Neo4jConfig(password=SecretStr("kwarg")))

        # Unprefixed values must NOT bleed into the loaded settings.
        assert settings.neo4j.uri == "bolt://localhost:7687"
        assert settings.neo4j.username == "neo4j"
        assert settings.neo4j.password.get_secret_value() == "kwarg"

    def test_prefixed_env_keys_still_loaded(self, tmp_path, monkeypatch):
        """Filter must not break the normal NAM_-prefixed nested load path."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "NAM_NEO4J__URI=neo4j://example:7687\n"
            "NAM_NEO4J__PASSWORD=fromenv\n"
            "NEO4J_URI=should-be-ignored\n"
        )
        monkeypatch.chdir(tmp_path)

        settings = MemorySettings()

        assert settings.neo4j.uri == "neo4j://example:7687"
        assert settings.neo4j.password.get_secret_value() == "fromenv"

    def test_kwarg_typo_still_rejected_with_envfile_present(self, tmp_path, monkeypatch):
        """Filter must not weaken the existing typo guard for code-level kwargs."""
        from neo4j_agent_memory.config.settings import SchemaConfig

        env_file = tmp_path / ".env"
        env_file.write_text("NEO4J_URI=neo4j://noise:7687\n")
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValidationError) as excinfo:
            MemorySettings(
                neo4j=Neo4jConfig(password=SecretStr("test")),
                schema=SchemaConfig(),  # type: ignore[call-arg]
            )
        assert "schema" in str(excinfo.value)

    def test_subclass_field_survives_filter(self, tmp_path, monkeypatch):
        """Subclasses adding new top-level fields must still receive prefixed env values."""
        from pydantic_settings import SettingsConfigDict

        class ExtendedSettings(MemorySettings):
            model_config = SettingsConfigDict(
                env_prefix="NAM_",
                env_nested_delimiter="__",
                env_file=".env",
                env_file_encoding="utf-8",
                extra="forbid",
            )
            my_app: str | None = None

        env_file = tmp_path / ".env"
        env_file.write_text(
            "NAM_MY_APP=hello\nMY_APP=ignored-without-prefix\nOPENAI_API_KEY=sk-ignored\n"
        )
        monkeypatch.chdir(tmp_path)

        settings = ExtendedSettings(neo4j=Neo4jConfig(password=SecretStr("test")))
        assert settings.my_app == "hello"

    def test_custom_env_file_path_respected(self, tmp_path, monkeypatch):
        """_env_file= override must be forwarded to the wrapped DotEnv source."""
        # No .env in cwd — if the wrong file were used we'd get defaults.
        monkeypatch.chdir(tmp_path)

        # Custom env file with NAM_-prefixed keys
        custom_env = tmp_path / "custom.env"
        custom_env.write_text(
            "NAM_NEO4J__URI=neo4j://custom:7687\nNAM_NEO4J__PASSWORD=custompass\n"
        )

        # Do NOT pass neo4j= as a kwarg — init_settings would take precedence
        # over dotenv_settings, masking whether the custom file was actually read.
        settings = MemorySettings(_env_file=custom_env)
        assert settings.neo4j.uri == "neo4j://custom:7687"
        assert settings.neo4j.password.get_secret_value() == "custompass"

    def test_env_file_none_disables_dotenv(self, tmp_path, monkeypatch):
        """_env_file=None must prevent dotenv loading even if .env exists in cwd."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "NAM_NEO4J__URI=neo4j://should-not-load:7687\nNAM_NEO4J__PASSWORD=blocked\n"
        )
        monkeypatch.chdir(tmp_path)

        settings = MemorySettings(_env_file=None)
        # URI must fall back to default, not read from the .env on disk.
        assert settings.neo4j.uri == "bolt://localhost:7687"
        assert settings.neo4j.password.get_secret_value() == ""
