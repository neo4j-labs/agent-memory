"""Unit tests for framework integration pass-through helpers.

These verify the introspection logic in
``neo4j_agent_memory.integrations._passthrough`` and the per-framework
wrappers added in WP-INT-PASSTHROUGH. We use lightweight fakes that
match the shape of each framework's model class so we don't need the
underlying SDKs installed.
"""

from __future__ import annotations

from typing import Any

import pytest

from neo4j_agent_memory.integrations._passthrough import (
    llm_provider_from_framework_model,
)


# Stand-in for the resolved provider returned by from_provider so tests
# can introspect what would have been built without invoking real SDKs.
class _FakeResolved:
    def __init__(self, model: str, **kwargs: Any) -> None:
        self.model = model
        self.kwargs = kwargs


@pytest.fixture
def stub_from_provider(monkeypatch: pytest.MonkeyPatch):
    """Replace ``neo4j_agent_memory.llm.from_provider`` with a recorder."""
    import neo4j_agent_memory.llm as llm_pkg

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake(model: str, *, kind: str = "llm", **kwargs: Any) -> _FakeResolved:
        calls.append((model, {"kind": kind, **kwargs}))
        return _FakeResolved(model, kind=kind, **kwargs)

    monkeypatch.setattr(llm_pkg, "from_provider", fake)
    return calls


# ---------------------------------------------------------------------------
# Generic introspection
# ---------------------------------------------------------------------------


class _ChatAnthropicLike:
    """Looks like langchain_anthropic.ChatAnthropic."""

    def __init__(self, model_name: str, anthropic_api_key: str | None = None) -> None:
        self.model_name = model_name
        self.anthropic_api_key = anthropic_api_key


def test_passthrough_detects_anthropic_from_class_name(stub_from_provider):
    model = _ChatAnthropicLike("claude-3-5-sonnet-latest", anthropic_api_key="sk-test")
    llm_provider_from_framework_model(model)
    # Expect model_id is prefixed with "anthropic/" and api_key forwarded.
    assert len(stub_from_provider) == 1
    resolved_model, kwargs = stub_from_provider[0]
    assert resolved_model == "anthropic/claude-3-5-sonnet-latest"
    assert kwargs["api_key"] == "sk-test"


class _OpenAIModelLike:
    """Looks like pydantic_ai.models.openai.OpenAIModel."""

    def __init__(self, model_name: str, api_key: str | None = None) -> None:
        self.model_name = model_name
        if api_key is not None:
            self.api_key = api_key


def test_passthrough_detects_openai_from_class_name(stub_from_provider):
    model = _OpenAIModelLike("gpt-4o-mini", api_key="sk-test")
    llm_provider_from_framework_model(model)
    resolved_model, kwargs = stub_from_provider[0]
    assert resolved_model == "openai/gpt-4o-mini"
    assert kwargs["api_key"] == "sk-test"


class _PrefixedModelLike:
    """Model name already includes provider prefix — must not double-prefix."""

    def __init__(self, model: str) -> None:
        self.model = model


def test_passthrough_does_not_double_prefix(stub_from_provider):
    model = _PrefixedModelLike("anthropic/claude-3-5-sonnet-latest")
    llm_provider_from_framework_model(model)
    resolved_model, _ = stub_from_provider[0]
    assert resolved_model == "anthropic/claude-3-5-sonnet-latest"


class _DeploymentLike:
    """Looks like an Azure OpenAI client with deployment_name."""

    def __init__(self, deployment_name: str, base_url: str | None = None) -> None:
        self.deployment_name = deployment_name
        if base_url is not None:
            self.base_url = base_url


def test_passthrough_reads_deployment_name_and_base_url(stub_from_provider):
    model = _DeploymentLike("gpt-4o-mini-prod", base_url="https://my.azure.endpoint")
    llm_provider_from_framework_model(model)
    resolved_model, kwargs = stub_from_provider[0]
    assert resolved_model.endswith("gpt-4o-mini-prod")
    assert kwargs.get("api_base") == "https://my.azure.endpoint"


class _Unintrospectable:
    pass


def test_passthrough_raises_on_missing_model_id():
    with pytest.raises(ValueError) as excinfo:
        llm_provider_from_framework_model(_Unintrospectable())
    assert "model id" in str(excinfo.value).lower()


class _SecretStrish:
    """Mimics pydantic.SecretStr — exposes get_secret_value()."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


class _SecretKeyModel:
    def __init__(self) -> None:
        self.model_name = "claude-3-5-sonnet-latest"
        # Anthropic-flavoured class name so the prefix is detected.
        self.anthropic_api_key = _SecretStrish("sk-secret")


def _make_class(name: str, attrs: dict[str, Any]) -> Any:
    cls = type(name, (), {"__init__": lambda self, _a=attrs: self.__dict__.update(_a)})
    cls.__module__ = "test.fake"
    return cls()


def test_passthrough_unwraps_secret_str():
    # Use a real class with "Anthropic" in its name so the prefix detector fires.
    model = _SecretKeyModel()
    model.__class__.__name__ = "ChatAnthropicMock"
    import neo4j_agent_memory.llm as llm_pkg

    captured: dict[str, Any] = {}

    def fake(model_id: str, **kwargs: Any) -> Any:
        captured.update({"model_id": model_id, **kwargs})
        return object()

    original = llm_pkg.from_provider
    llm_pkg.from_provider = fake  # type: ignore[assignment]
    try:
        llm_provider_from_framework_model(model)
    finally:
        llm_pkg.from_provider = original  # type: ignore[assignment]

    assert captured["api_key"] == "sk-secret"


# ---------------------------------------------------------------------------
# Per-integration wrappers
# ---------------------------------------------------------------------------


def test_langchain_wrapper_delegates_to_passthrough(stub_from_provider):
    from neo4j_agent_memory.integrations.langchain import (
        llm_provider_from_langchain,
    )

    model = _ChatAnthropicLike("claude-3-5-sonnet-latest")
    llm_provider_from_langchain(model)
    assert stub_from_provider[0][0] == "anthropic/claude-3-5-sonnet-latest"


def test_pydantic_ai_wrapper_delegates_to_passthrough(stub_from_provider):
    from neo4j_agent_memory.integrations.pydantic_ai import (
        llm_provider_from_pydantic_ai,
    )

    model = _OpenAIModelLike("gpt-4o")
    llm_provider_from_pydantic_ai(model)
    assert stub_from_provider[0][0] == "openai/gpt-4o"


def test_llamaindex_wrapper_delegates_to_passthrough(stub_from_provider):
    from neo4j_agent_memory.integrations.llamaindex import (
        llm_provider_from_llamaindex,
    )

    class _LlamaIndexAnthropic:
        def __init__(self) -> None:
            self.model = "claude-3-5-haiku-latest"

    _LlamaIndexAnthropic.__module__ = "llama_index.llms.anthropic"
    llm_provider_from_llamaindex(_LlamaIndexAnthropic())
    assert stub_from_provider[0][0] == "anthropic/claude-3-5-haiku-latest"


def test_crewai_wrapper_passes_through_prefixed_id(stub_from_provider):
    from neo4j_agent_memory.integrations.crewai import llm_provider_from_crewai

    class _CrewAILLM:
        def __init__(self) -> None:
            self.model = "openai/gpt-4o-mini"

    llm_provider_from_crewai(_CrewAILLM())
    assert stub_from_provider[0][0] == "openai/gpt-4o-mini"


def test_openai_agents_wrapper_accepts_bare_string(stub_from_provider):
    from neo4j_agent_memory.integrations.openai_agents import (
        llm_provider_from_openai_agents,
    )

    llm_provider_from_openai_agents("gpt-4o-mini")
    assert stub_from_provider[0][0] == "openai/gpt-4o-mini"


def test_openai_agents_wrapper_passes_through_prefixed_string(stub_from_provider):
    from neo4j_agent_memory.integrations.openai_agents import (
        llm_provider_from_openai_agents,
    )

    llm_provider_from_openai_agents("anthropic/claude-3-5-sonnet-latest")
    assert stub_from_provider[0][0] == "anthropic/claude-3-5-sonnet-latest"


def test_google_adk_wrapper_prefixes_vertex_for_bare_strings(stub_from_provider):
    from neo4j_agent_memory.integrations.google_adk import (
        llm_provider_from_google_adk,
    )

    llm_provider_from_google_adk("gemini-2.5-flash")
    assert stub_from_provider[0][0] == "vertex_ai/gemini-2.5-flash"


def test_strands_wrapper_prefixes_bedrock_for_bare_strings(stub_from_provider):
    from neo4j_agent_memory.integrations.strands import (
        llm_provider_from_strands,
    )

    llm_provider_from_strands("us.anthropic.claude-sonnet-4-6")
    assert stub_from_provider[0][0] == "bedrock/us.anthropic.claude-sonnet-4-6"
