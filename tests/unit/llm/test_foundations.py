"""Foundational unit tests for the llm package.

Covers protocols, types, errors, defaults, factory, and SAP. Adapter-level
behavior lives in the contract harness (``test_adapter_contract.py``); this
file only exercises code that has no SDK dependency.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from neo4j_agent_memory.llm import (
    ChatMessage,
    Completion,
    EmbeddingDimensionMismatchError,
    EmbeddingProvider,
    LLMProvider,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    StructuredExtractionError,
    StructuredExtractor,
    Usage,
    from_provider,
    schema_aligned_extract,
)
from neo4j_agent_memory.llm.defaults import (
    EMBEDDING_DIMENSIONS,
    lookup_embedding_dimensions,
)
from neo4j_agent_memory.llm.structured import _format_retry_prompt, _tolerant_json_parse

from ._mocks import CannedEmbeddingProvider, CannedLLMProvider, SimplePayload

# ---------------------------------------------------------------------------
# Protocol conformance — runtime_checkable structural typing
# ---------------------------------------------------------------------------


def test_canned_llm_provider_satisfies_llm_protocol():
    provider = CannedLLMProvider(responses=["hi"])
    assert isinstance(provider, LLMProvider)


def test_canned_llm_provider_does_not_satisfy_structured_extractor():
    # No `complete_structured` method — should fail structural check.
    provider = CannedLLMProvider(responses=["hi"])
    assert not isinstance(provider, StructuredExtractor)


def test_canned_embedding_provider_satisfies_embedding_protocol():
    embedder = CannedEmbeddingProvider()
    assert isinstance(embedder, EmbeddingProvider)


def test_plain_object_does_not_satisfy_llm_protocol():
    assert not isinstance(object(), LLMProvider)


# ---------------------------------------------------------------------------
# Type round-trip
# ---------------------------------------------------------------------------


def test_chat_message_is_frozen():
    msg = ChatMessage(role="user", content="hello")
    with pytest.raises(ValidationError):
        # Pydantic raises ValidationError when assigning to a frozen model
        # field via Pydantic v2's frozen=True semantics.
        msg.content = "world"  # type: ignore[misc]


def test_chat_message_serializes_round_trip():
    msg = ChatMessage(role="assistant", content="hi", name="bot")
    blob = msg.model_dump_json()
    restored = ChatMessage.model_validate_json(blob)
    assert restored == msg


def test_completion_serializes_round_trip():
    completion = Completion(
        content="hi",
        model="test/canned",
        usage=Usage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
        finish_reason="stop",
    )
    blob = completion.model_dump_json()
    restored = Completion.model_validate_json(blob)
    assert restored == completion


def test_usage_negative_counts_rejected():
    with pytest.raises(ValidationError):
        Usage(prompt_tokens=-1)


# ---------------------------------------------------------------------------
# Defaults table
# ---------------------------------------------------------------------------


def test_every_default_dimension_is_positive():
    for model, dim in EMBEDDING_DIMENSIONS.items():
        assert isinstance(dim, int) and dim > 0, model


def test_lookup_hits_prefixed_model():
    assert lookup_embedding_dimensions("openai/text-embedding-3-small") == 1536


def test_lookup_hits_without_provider_prefix():
    # When a model name is unique within the table we should still resolve.
    assert lookup_embedding_dimensions("text-embedding-3-small") == 1536


def test_lookup_misses_unknown_model():
    assert lookup_embedding_dimensions("vendor-x/unknown-v9") is None


def test_sentence_transformers_requires_dimensions_for_unknown_model():
    from neo4j_agent_memory.llm.adapters.sentence_transformers import (
        SentenceTransformersProvider,
    )

    with pytest.raises(ValueError, match="Pass dimensions=<positive-int> explicitly"):
        SentenceTransformersProvider("vendor-x/unknown-v9")


def test_sentence_transformers_accepts_explicit_dimensions_for_unknown_model():
    from neo4j_agent_memory.llm.adapters.sentence_transformers import (
        SentenceTransformersProvider,
    )

    provider = SentenceTransformersProvider("vendor-x/unknown-v9", dimensions=384)
    assert provider.dimensions == 384


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


def test_all_provider_errors_subclass_provider_error():
    for cls in (
        ProviderAuthError,
        ProviderRateLimitError,
        StructuredExtractionError,
        EmbeddingDimensionMismatchError,
    ):
        assert issubclass(cls, ProviderError)


def test_rate_limit_error_carries_retry_after():
    err = ProviderRateLimitError("slow down", retry_after=3.5)
    assert err.retry_after == 3.5


def test_dimension_mismatch_error_carries_diagnostic_fields():
    err = EmbeddingDimensionMismatchError(
        "mismatch",
        expected_dimensions=384,
        actual_dimensions=1536,
        index_name="message_embedding_idx",
    )
    assert err.expected_dimensions == 384
    assert err.actual_dimensions == 1536
    assert err.index_name == "message_embedding_idx"


# ---------------------------------------------------------------------------
# Factory resolution
# ---------------------------------------------------------------------------


def test_from_provider_raises_when_no_extra_installed(monkeypatch):
    # Force every extra to look uninstalled. The factory should raise
    # ImportError with the install hint.
    import neo4j_agent_memory.llm.factory as factory

    monkeypatch.setattr(factory, "_has", lambda _extra: False)
    with pytest.raises(ImportError) as excinfo:
        from_provider("openai/gpt-4o")
    msg = str(excinfo.value)
    assert "openai" in msg
    assert "litellm" in msg  # universal-fallback hint always present


def test_from_provider_routes_to_native_openai(monkeypatch):
    """When the openai extra is reported installed, prefer the native adapter."""
    import neo4j_agent_memory.llm.factory as factory

    monkeypatch.setattr(factory, "_has", lambda extra: extra == "openai")
    # Stub OpenAIProvider so we don't import the SDK in this test environment.
    import sys
    import types

    fake_module = types.ModuleType("neo4j_agent_memory.llm.adapters.openai")

    class FakeOpenAIProvider:
        def __init__(self, model, **kwargs):
            self.model = model
            self.kwargs = kwargs

    fake_module.OpenAIProvider = FakeOpenAIProvider  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "neo4j_agent_memory.llm.adapters.openai", fake_module)

    provider = from_provider("openai/gpt-4o-mini")
    assert provider.__class__.__name__ == "FakeOpenAIProvider"
    assert provider.model == "openai/gpt-4o-mini"


def test_from_provider_falls_back_to_litellm_for_unknown_provider(monkeypatch):
    import sys
    import types

    import neo4j_agent_memory.llm.factory as factory

    # Only litellm is installed; openai/anthropic/bedrock are not.
    monkeypatch.setattr(factory, "_has", lambda extra: extra == "litellm")
    fake_module = types.ModuleType("neo4j_agent_memory.llm.adapters.litellm")

    class FakeLiteLLM:
        def __init__(self, model, **kwargs):
            self.model = model

    fake_module.LiteLLMProvider = FakeLiteLLM  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "neo4j_agent_memory.llm.adapters.litellm", fake_module)

    provider = from_provider("groq/llama-3.1-8b-instant")
    assert provider.__class__.__name__ == "FakeLiteLLM"


def test_from_provider_prefer_litellm_overrides_native(monkeypatch):
    import sys
    import types

    import neo4j_agent_memory.llm.factory as factory

    monkeypatch.setattr(
        factory,
        "_has",
        lambda extra: extra in {"openai", "litellm"},
    )

    fake_litellm = types.ModuleType("neo4j_agent_memory.llm.adapters.litellm")

    class FakeLiteLLM:
        def __init__(self, model, **kwargs):
            self.model = model

    fake_litellm.LiteLLMProvider = FakeLiteLLM  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "neo4j_agent_memory.llm.adapters.litellm", fake_litellm)

    provider = from_provider("openai/gpt-4o", prefer_litellm=True)
    assert provider.__class__.__name__ == "FakeLiteLLM"


def test_from_provider_unknown_kind_raises():
    with pytest.raises(ValueError):
        from_provider("openai/gpt-4o", kind="vision")  # type: ignore[arg-type]


def test_from_provider_vertex_ai_llm_hint_is_not_embedding_extra(monkeypatch):
    import neo4j_agent_memory.llm.factory as factory

    monkeypatch.setattr(factory, "_has", lambda _extra: False)
    with pytest.raises(ImportError) as excinfo:
        from_provider("vertex_ai/gemini-2.0-flash", kind="llm")
    msg = str(excinfo.value)
    assert "vertex-ai" not in msg
    assert "litellm" in msg


def test_from_provider_vertex_ai_embedding_hint_suggests_vertex_extra(monkeypatch):
    import neo4j_agent_memory.llm.factory as factory

    monkeypatch.setattr(factory, "_has", lambda _extra: False)
    with pytest.raises(ImportError) as excinfo:
        from_provider("vertex_ai/gemini-embedding-001", kind="embedding")
    msg = str(excinfo.value)
    assert "vertex-ai" in msg


# ---------------------------------------------------------------------------
# Tolerant JSON parser
# ---------------------------------------------------------------------------


def test_tolerant_parse_plain_json():
    assert _tolerant_json_parse('{"foo": "bar"}') == {"foo": "bar"}


def test_tolerant_parse_markdown_fence():
    text = '```json\n{"foo": "bar"}\n```'
    assert _tolerant_json_parse(text) == {"foo": "bar"}


def test_tolerant_parse_chain_of_thought_prefix():
    text = 'Let me think...\n\n{"answer": 42}'
    assert _tolerant_json_parse(text) == {"answer": 42}


def test_tolerant_parse_smart_quotes():
    text = "“foo”"  # smart-quoted bareword — invalid JSON
    # On its own this is not valid JSON; embed inside a JSON object.
    wrapped = "{“foo”: “bar”}"
    assert _tolerant_json_parse(wrapped) == {"foo": "bar"}


def test_tolerant_parse_trailing_comma():
    # Plain `json.loads` rejects trailing commas; the tolerant parser strips them.
    text = '{"foo": "bar",}'
    assert _tolerant_json_parse(text) == {"foo": "bar"}


def test_tolerant_parse_truncated_raises():
    with pytest.raises(json.JSONDecodeError):
        _tolerant_json_parse('{"foo": "ba')


def test_tolerant_parse_list_at_top_level_raises():
    # The contract is "return a dict"; a top-level list must trigger retry.
    with pytest.raises(json.JSONDecodeError):
        _tolerant_json_parse("[1, 2, 3]")


# ---------------------------------------------------------------------------
# Retry-prompt formatting
# ---------------------------------------------------------------------------


def test_retry_prompt_cites_validation_paths():
    try:
        SimplePayload.model_validate({"name": "x"})
    except ValidationError as exc:
        prompt = _format_retry_prompt(exc, SimplePayload)
    assert "age" in prompt  # missing-required field name surfaces in the message
    assert "SimplePayload" in prompt


def test_retry_prompt_for_json_decode_error():
    try:
        json.loads("not json")
    except json.JSONDecodeError as exc:
        prompt = _format_retry_prompt(exc, SimplePayload)
    assert "JSON" in prompt
    assert "SimplePayload" in prompt


# ---------------------------------------------------------------------------
# Schema-aligned extraction loop
# ---------------------------------------------------------------------------


async def test_sap_returns_validated_payload_on_first_try():
    provider = CannedLLMProvider(responses=['{"name": "Alice", "age": 30}'])
    result = await schema_aligned_extract(
        provider,
        messages=[ChatMessage(role="user", content="extract")],
        response_model=SimplePayload,
    )
    assert isinstance(result, SimplePayload)
    assert result.name == "Alice"
    assert result.age == 30
    # Only one call required.
    assert len(provider.calls) == 1


async def test_sap_retries_with_feedback_on_validation_error():
    provider = CannedLLMProvider(
        responses=[
            '{"name": "Alice"}',  # missing required 'age' → ValidationError
            '{"name": "Alice", "age": 30}',  # corrected
        ]
    )
    result = await schema_aligned_extract(
        provider,
        messages=[ChatMessage(role="user", content="extract")],
        response_model=SimplePayload,
        max_retries=2,
    )
    assert result.age == 30
    # Two calls; second call's conversation includes the feedback message.
    assert len(provider.calls) == 2
    feedback_call = provider.calls[1]
    # The last user message in the second call should reference the
    # validation feedback. (System + user + assistant + user.)
    assert any("validation" in m.content.lower() for m in feedback_call if m.role == "user")


async def test_sap_raises_after_exhausting_retries():
    provider = CannedLLMProvider(responses=["not json", "still not json", "really not json"])
    with pytest.raises(StructuredExtractionError) as excinfo:
        await schema_aligned_extract(
            provider,
            messages=[ChatMessage(role="user", content="extract")],
            response_model=SimplePayload,
            max_retries=2,
        )
    # All three attempts surface in the error so callers can diagnose.
    assert len(excinfo.value.last_attempts) == 3
