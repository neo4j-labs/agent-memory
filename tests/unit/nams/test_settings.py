"""Phase 1 unit tests: NamsConfig + MemorySettings.backend resolution.

Covers the foundation contract:

* ``NamsConfig`` defaults and field validation.
* ``MemorySettings(backend="nams"|"bolt"|None)`` construction.
* The ``_resolve_backend`` model validator:
    - explicit ``backend=`` wins,
    - else ``MEMORY_API_KEY`` env → NAMS (with key lifted into ``nams.api_key``),
    - else default → bolt.
* ``MEMORY_ENDPOINT`` env alias.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from neo4j_agent_memory.config.settings import MemorySettings, NamsConfig

# -----------------------------------------------------------------------------
# NamsConfig
# -----------------------------------------------------------------------------


class TestNamsConfigDefaults:
    """NamsConfig should be instantiable with no args."""

    def test_default_endpoint(self):
        config = NamsConfig()
        assert config.endpoint == "https://memory.neo4jlabs.com/v1"

    def test_default_api_key_is_none(self):
        config = NamsConfig()
        assert config.api_key is None

    def test_default_timeout(self):
        config = NamsConfig()
        assert config.timeout == 30.0

    def test_default_max_retries(self):
        config = NamsConfig()
        assert config.max_retries == 3

    def test_default_retry_backoff(self):
        config = NamsConfig()
        assert config.retry_backoff_seconds == 0.5

    def test_default_headers_empty(self):
        config = NamsConfig()
        assert config.headers == {}

    def test_default_validate_on_connect(self):
        config = NamsConfig()
        assert config.validate_on_connect is True

    def test_default_transport_mode(self):
        config = NamsConfig()
        assert config.transport_mode == "auto"


class TestNamsConfigValidation:
    """NamsConfig should reject invalid input."""

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValidationError):
            NamsConfig(timeout=0)
        with pytest.raises(ValidationError):
            NamsConfig(timeout=-1)

    def test_max_retries_must_be_non_negative(self):
        # 0 is allowed (no retries)
        NamsConfig(max_retries=0)
        with pytest.raises(ValidationError):
            NamsConfig(max_retries=-1)

    def test_retry_backoff_must_be_positive(self):
        with pytest.raises(ValidationError):
            NamsConfig(retry_backoff_seconds=0)

    def test_transport_mode_choices(self):
        NamsConfig(transport_mode="auto")
        NamsConfig(transport_mode="rest")
        NamsConfig(transport_mode="bridge")
        with pytest.raises(ValidationError):
            NamsConfig(transport_mode="grpc")  # type: ignore[arg-type]

    def test_rejects_unknown_field(self):
        # _STRICT_CONFIG → extra="forbid"
        with pytest.raises(ValidationError):
            NamsConfig(unknown_field="value")  # type: ignore[call-arg]

    def test_custom_endpoint(self):
        config = NamsConfig(endpoint="https://nams.internal/v2")
        assert config.endpoint == "https://nams.internal/v2"


class TestNamsConfigEndpointNormalization:
    """The hosted NAMS service is REST-only and lives under ``/v1``.

    A user who copies the bare base URL from the docs
    (``https://memory.neo4jlabs.com``) must not silently fall into TCK
    bridge mode. NamsConfig appends the ``/v1`` suffix for the hosted
    host when no ``/v<N>`` segment is present. See issue #129.

    Normalization is scoped to the hosted host so that version-less
    on-prem / localhost endpoints keep their (bridge) behavior.
    """

    def test_bare_hosted_url_gets_v1_suffix(self):
        config = NamsConfig(endpoint="https://memory.neo4jlabs.com")
        assert config.endpoint == "https://memory.neo4jlabs.com/v1"

    def test_bare_hosted_url_with_trailing_slash_gets_v1_suffix(self):
        config = NamsConfig(endpoint="https://memory.neo4jlabs.com/")
        assert config.endpoint == "https://memory.neo4jlabs.com/v1"

    def test_normalized_hosted_url_selects_rest_protocol(self):
        # The whole point: after normalization, auto-detection is REST.
        from neo4j_agent_memory.nams import detect_protocol

        config = NamsConfig(endpoint="https://memory.neo4jlabs.com")
        assert detect_protocol(config.endpoint) == "rest"

    def test_hosted_url_already_versioned_is_unchanged(self):
        config = NamsConfig(endpoint="https://memory.neo4jlabs.com/v1")
        assert config.endpoint == "https://memory.neo4jlabs.com/v1"

    def test_hosted_url_with_higher_version_is_unchanged(self):
        config = NamsConfig(endpoint="https://memory.neo4jlabs.com/v2")
        assert config.endpoint == "https://memory.neo4jlabs.com/v2"

    def test_localhost_without_version_is_untouched(self):
        # On-prem / TCK bridge must keep working — no forced /v1.
        config = NamsConfig(endpoint="http://localhost:8000")
        assert config.endpoint == "http://localhost:8000"

    def test_other_host_without_version_is_untouched(self):
        config = NamsConfig(endpoint="https://nams.internal")
        assert config.endpoint == "https://nams.internal"

    def test_api_key_is_secret(self):
        config = NamsConfig(api_key=SecretStr("nams_test_key"))
        assert config.api_key is not None
        assert config.api_key.get_secret_value() == "nams_test_key"
        # SecretStr doesn't leak via repr
        assert "nams_test_key" not in repr(config)


# -----------------------------------------------------------------------------
# MemorySettings.backend resolution
# -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip MEMORY_* and NAM_* env vars so per-test setup is hermetic."""
    for key in list(__import__("os").environ.keys()):
        if key.startswith(("MEMORY_", "NAM_")):
            monkeypatch.delenv(key, raising=False)
    yield


class TestExplicitBackend:
    """When the user pins ``backend=``, the validator must not override it."""

    def test_explicit_bolt(self):
        settings = MemorySettings(backend="bolt")
        assert settings.backend == "bolt"

    def test_explicit_nams(self):
        settings = MemorySettings(
            backend="nams",
            nams=NamsConfig(api_key=SecretStr("nams_test")),
        )
        assert settings.backend == "nams"
        assert settings.nams.api_key is not None
        assert settings.nams.api_key.get_secret_value() == "nams_test"

    def test_explicit_bolt_with_api_key_env_still_bolt(self, monkeypatch):
        """Explicit backend wins over MEMORY_API_KEY env hint."""
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test")
        settings = MemorySettings(backend="bolt")
        assert settings.backend == "bolt"
        # api_key still lifted from env (user may need it later) — that's OK,
        # the contract is only about backend selection.

    def test_explicit_nams_rejects_invalid_value(self):
        with pytest.raises(ValidationError):
            MemorySettings(backend="grpc")  # type: ignore[arg-type]


class TestEnvFallback:
    """When ``backend`` is unset, env decides."""

    def test_no_env_defaults_to_bolt(self):
        # _clean_env has stripped everything
        settings = MemorySettings()
        assert settings.backend == "bolt"
        assert settings.nams.api_key is None

    def test_memory_api_key_env_selects_nams(self, monkeypatch):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_from_env")
        settings = MemorySettings()
        assert settings.backend == "nams"
        assert settings.nams.api_key is not None
        assert settings.nams.api_key.get_secret_value() == "nams_from_env"

    def test_memory_endpoint_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test")
        monkeypatch.setenv("MEMORY_ENDPOINT", "https://nams.sandbox/v1")
        settings = MemorySettings()
        assert settings.backend == "nams"
        assert settings.nams.endpoint == "https://nams.sandbox/v1"

    def test_bare_hosted_endpoint_via_env_is_normalized(self, monkeypatch):
        # Same bug as issue #129 via the MEMORY_ENDPOINT alias: a bare
        # hosted URL set through env must also gain the /v1 suffix.
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test")
        monkeypatch.setenv("MEMORY_ENDPOINT", "https://memory.neo4jlabs.com")
        settings = MemorySettings()
        assert settings.nams.endpoint == "https://memory.neo4jlabs.com/v1"

    def test_explicit_endpoint_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_test")
        monkeypatch.setenv("MEMORY_ENDPOINT", "https://from-env/v1")
        settings = MemorySettings(
            nams=NamsConfig(endpoint="https://from-config/v1"),
        )
        assert settings.nams.endpoint == "https://from-config/v1"

    def test_explicit_api_key_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("MEMORY_API_KEY", "from-env")
        settings = MemorySettings(
            nams=NamsConfig(api_key=SecretStr("from-config")),
        )
        assert settings.backend == "nams"
        assert settings.nams.api_key is not None
        assert settings.nams.api_key.get_secret_value() == "from-config"


class TestBackwardCompatibility:
    """Existing v0.3.x code must keep working unchanged."""

    def test_bare_memory_settings_still_works(self):
        # The default Neo4j password is empty — the historic v0.2 default.
        settings = MemorySettings()
        assert settings.backend == "bolt"
        assert settings.neo4j.uri == "bolt://localhost:7687"

    def test_classic_neo4j_only_config(self):
        from neo4j_agent_memory.config.settings import Neo4jConfig

        settings = MemorySettings(
            neo4j=Neo4jConfig(password=SecretStr("secret")),
        )
        assert settings.backend == "bolt"
        assert settings.neo4j.password.get_secret_value() == "secret"

    def test_nams_field_default_is_safe(self):
        """``nams=NamsConfig()`` default must not flip backend to NAMS."""
        settings = MemorySettings()
        assert settings.nams.api_key is None
        assert settings.backend == "bolt"
