"""Factory function for constructing provider instances from strings.

:func:`from_provider` is the canonical entry point for the string-shorthand
API (PRD Section 8.1). It implements *native-first resolution*: when both
a native adapter and the LiteLLM universal adapter are installed, the
native adapter is preferred for that provider because native adapters can
take advantage of provider-specific features (OpenAI strict mode,
Anthropic prompt caching, Bedrock Converse streaming) that LiteLLM
normalizes away or lags on.

Users who want to override and force LiteLLM for a provider that has a
native adapter installed can pass ``prefer_litellm=True`` or construct
a :class:`LiteLLMProvider` directly.

All adapter imports happen *inside* :func:`from_provider`. This file is
importable without any extra installed.
"""

from __future__ import annotations

import logging
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, Literal, overload

if TYPE_CHECKING:
    from neo4j_agent_memory.llm.protocol import EmbeddingProvider, LLMProvider


logger = logging.getLogger(__name__)


# Native LLM adapters we ship. Order is the dispatch order: when a user
# passes a provider string matching one of these and the corresponding
# extra is installed, the native adapter is used.
_NATIVE_LLM_PROVIDERS: frozenset[str] = frozenset({"openai", "anthropic", "bedrock"})

# Native embedding adapters we ship.
_NATIVE_EMBEDDING_PROVIDERS: frozenset[str] = frozenset(
    {"openai", "vertex_ai", "bedrock", "sentence-transformers"}
)


def _has(extra: str) -> bool:
    """Return True if the package providing ``extra`` is importable.

    Uses :func:`importlib.util.find_spec` for a side-effect-free check
    that does not actually import the module.
    """
    package_map = {
        "openai": "openai",
        "anthropic": "anthropic",
        "bedrock": "boto3",
        "litellm": "litellm",
        "instructor": "instructor",
        "sentence-transformers": "sentence_transformers",
        "vertex_ai": "vertexai",
    }
    pkg = package_map.get(extra, extra)
    try:
        return find_spec(pkg) is not None
    except (ImportError, ValueError):
        return False


def _looks_like_huggingface_id(model: str) -> bool:
    """Heuristic: does this look like a HuggingFace model id?

    HuggingFace IDs have the form ``"<org>/<model>"`` where ``<org>`` is
    an organisation name (not a provider prefix). We detect by exclusion:
    if the first component is one of our known provider prefixes, it's not
    a HuggingFace id.
    """
    if "/" not in model:
        return False
    org, _, _ = model.partition("/")
    known_prefixes = {
        "openai",
        "anthropic",
        "bedrock",
        "vertex_ai",
        "gemini",
        "azure",
        "cohere",
        "mistral",
        "groq",
        "together_ai",
        "ollama",
        "openrouter",
        "voyage",
        "deepseek",
        "fireworks_ai",
        "perplexity",
        "replicate",
        "anyscale",
        "palm",
    }
    return org not in known_prefixes


@overload
def from_provider(
    model: str,
    *,
    kind: Literal["llm"] = ...,
    prefer_litellm: bool = ...,
    **kwargs: Any,
) -> LLMProvider: ...


@overload
def from_provider(
    model: str,
    *,
    kind: Literal["embedding"],
    prefer_litellm: bool = ...,
    **kwargs: Any,
) -> EmbeddingProvider: ...


def from_provider(
    model: str,
    *,
    kind: Literal["llm", "embedding"] = "llm",
    prefer_litellm: bool = False,
    **kwargs: Any,
) -> LLMProvider | EmbeddingProvider:
    """Resolve a provider string to a concrete adapter instance.

    Args:
        model: Provider string. Common forms:

            * ``"openai/gpt-4o"`` — provider-prefixed
            * ``"anthropic/claude-3-5-sonnet-latest"``
            * ``"bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0"``
            * ``"groq/llama-3.1-8b-instant"`` (via LiteLLM)
            * ``"ollama/llama3.2"`` (via LiteLLM, requires ``api_base=``)
            * ``"BAAI/bge-small-en-v1.5"`` (HuggingFace, embedding only)

            Bare model names (``"gpt-4o"``) are also accepted and resolved
            to the default provider for the given ``kind``.

        kind: ``"llm"`` (default) or ``"embedding"``. Determines which
            adapter family is consulted.

        prefer_litellm: If True, route through LiteLLM even when a native
            adapter is available for the provider. Useful for testing or
            when a user wants consistent behavior across providers.

        **kwargs: Passed verbatim to the adapter constructor. Common keys:
            ``api_key``, ``api_base``, ``aws_region``, ``aws_profile``,
            ``timeout``, ``dimensions`` (embedding only).

    Returns:
        A configured :class:`LLMProvider` or :class:`EmbeddingProvider`.

    Raises:
        ImportError: No adapter is available for the requested provider.
            The error message includes the install hint.
    """
    provider_prefix, _, _ = model.partition("/")
    has_prefix = "/" in model

    # Normalize: when the user passes a bare model name, default the provider.
    if not has_prefix:
        if kind == "llm":
            # LLMs default to openai (unprefixed "gpt-4o" → "openai/gpt-4o")
            provider_prefix = "openai"
            model = f"openai/{model}"
        else:
            # Embeddings: if it looks like an OpenAI model, prefix with openai;
            # otherwise treat as a HuggingFace id (sentence-transformers).
            if model.startswith("text-embedding-") or model.startswith("embedding-"):
                provider_prefix = "openai"
                model = f"openai/{model}"
            else:
                provider_prefix = "sentence-transformers"

    # HuggingFace IDs always route to sentence-transformers (embedding only)
    if kind == "embedding" and _looks_like_huggingface_id(model):
        provider_prefix = "sentence-transformers"

    if kind == "llm":
        return _resolve_llm(model, provider_prefix, prefer_litellm, kwargs)
    if kind == "embedding":
        return _resolve_embedding(model, provider_prefix, prefer_litellm, kwargs)
    raise ValueError(f"kind must be 'llm' or 'embedding', got {kind!r}")


def _resolve_llm(
    model: str,
    provider_prefix: str,
    prefer_litellm: bool,
    kwargs: dict[str, Any],
) -> LLMProvider:
    """Resolve an LLM model string to an LLMProvider instance."""
    # Native-first dispatch (unless explicitly overridden)
    if not prefer_litellm and provider_prefix in _NATIVE_LLM_PROVIDERS:
        if provider_prefix == "openai" and _has("openai"):
            from neo4j_agent_memory.llm.adapters.openai import OpenAIProvider

            logger.debug("from_provider: routing %r to native OpenAIProvider", model)
            return OpenAIProvider(model, **kwargs)
        if provider_prefix == "anthropic" and _has("anthropic"):
            from neo4j_agent_memory.llm.adapters.anthropic import AnthropicProvider

            logger.debug("from_provider: routing %r to native AnthropicProvider", model)
            return AnthropicProvider(model, **kwargs)
        if provider_prefix == "bedrock" and _has("bedrock"):
            from neo4j_agent_memory.llm.adapters.bedrock import BedrockProvider

            logger.debug("from_provider: routing %r to native BedrockProvider", model)
            return BedrockProvider(model, **kwargs)

    # LiteLLM universal fallback
    if _has("litellm"):
        from neo4j_agent_memory.llm.adapters.litellm import LiteLLMProvider

        logger.debug("from_provider: routing %r to LiteLLMProvider", model)
        return LiteLLMProvider(model, **kwargs)

    raise ImportError(_install_hint(provider_prefix, "llm"))


def _resolve_embedding(
    model: str,
    provider_prefix: str,
    prefer_litellm: bool,
    kwargs: dict[str, Any],
) -> EmbeddingProvider:
    """Resolve an embedding model string to an EmbeddingProvider instance."""
    if not prefer_litellm and provider_prefix in _NATIVE_EMBEDDING_PROVIDERS:
        if provider_prefix == "openai" and _has("openai"):
            from neo4j_agent_memory.llm.adapters.openai import OpenAIEmbeddingProvider

            logger.debug("from_provider: routing %r to OpenAIEmbeddingProvider", model)
            return OpenAIEmbeddingProvider(model, **kwargs)
        if provider_prefix == "sentence-transformers" and _has("sentence-transformers"):
            from neo4j_agent_memory.llm.adapters.sentence_transformers import (
                SentenceTransformersProvider,
            )

            logger.debug("from_provider: routing %r to SentenceTransformersProvider", model)
            return SentenceTransformersProvider(model, **kwargs)
        if provider_prefix == "vertex_ai" and _has("vertex_ai"):
            from neo4j_agent_memory.llm.adapters.vertex_ai import VertexAIEmbeddingProvider

            logger.debug("from_provider: routing %r to VertexAIEmbeddingProvider", model)
            return VertexAIEmbeddingProvider(model, **kwargs)
        if provider_prefix == "bedrock" and _has("bedrock"):
            from neo4j_agent_memory.llm.adapters.bedrock import BedrockEmbeddingProvider

            logger.debug("from_provider: routing %r to BedrockEmbeddingProvider", model)
            return BedrockEmbeddingProvider(model, **kwargs)

    # LiteLLM universal fallback for embeddings
    if _has("litellm"):
        from neo4j_agent_memory.llm.adapters.litellm import LiteLLMEmbeddingProvider

        logger.debug("from_provider: routing %r to LiteLLMEmbeddingProvider", model)
        return LiteLLMEmbeddingProvider(model, **kwargs)

    raise ImportError(_install_hint(provider_prefix, "embedding"))


def _install_hint(provider_prefix: str, kind: str) -> str:
    """Build the install-hint message attached to a missing-adapter ImportError."""
    if kind not in {"llm", "embedding"}:
        raise ValueError(f"Unsupported adapter kind: {kind!r}")

    native_extras_by_kind: dict[str, dict[str, str]] = {
        "llm": {
            "openai": "openai",
            "anthropic": "anthropic",
            "bedrock": "bedrock",
        },
        "embedding": {
            "openai": "openai",
            "bedrock": "bedrock",
            "vertex_ai": "vertex-ai",
            "sentence-transformers": "sentence-transformers",
        },
    }
    native_extras = native_extras_by_kind.get(kind, {})
    if provider_prefix in native_extras:
        hint_native = f"neo4j-agent-memory[{native_extras[provider_prefix]}]"
    else:
        hint_native = None

    hint_litellm = "neo4j-agent-memory[litellm]"

    pieces = [
        f"No {kind} adapter available for provider {provider_prefix!r}.",
        "",
        "Install one of the following:",
    ]
    if hint_native is not None:
        pieces.append(f"  pip install '{hint_native}'      # native adapter")
    pieces.append(f"  pip install '{hint_litellm}'  # universal adapter (recommended)")
    return "\n".join(pieces)


__all__ = [
    "from_provider",
]
