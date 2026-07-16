"""Pass-through helpers for framework-native model objects.

When a user has already configured an LLM with their agent framework
(LangChain ``ChatAnthropic``, Pydantic AI ``AnthropicModel``, LlamaIndex
``Anthropic``, …) we should not make them re-declare the same model for
agent-memory. These helpers introspect a framework-native model object
and translate it to a :class:`LLMProvider` instance via
:func:`from_provider`.

The helpers are intentionally best-effort:

* They probe a small set of well-known attribute names (``model``,
  ``model_name``, ``name``, ``api_key``, ``api_base``).
* They map known framework class names to canonical provider prefixes.
* They never raise on missing introspection — fall back to LiteLLM with
  whatever model id we could discover.

This means downstream users can rely on the call to *some* provider being
returned; if the introspection guess is wrong they can override by
constructing the adapter directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j_agent_memory.llm.protocol import LLMProvider

logger = logging.getLogger(__name__)


# Map framework class-name fragments to canonical provider prefixes.
# Used as a fallback when ``model_name`` doesn't already include a
# ``"<provider>/"`` prefix.
_CLASS_PROVIDER_HINTS: tuple[tuple[str, str], ...] = (
    ("anthropic", "anthropic"),
    ("openai", "openai"),
    ("bedrock", "bedrock"),
    ("vertex", "vertex_ai"),
    ("gemini", "gemini"),
    ("cohere", "cohere"),
    ("groq", "groq"),
    ("ollama", "ollama"),
    ("mistral", "mistral"),
    ("together", "together_ai"),
    ("azure", "azure"),
)


def _detect_provider_prefix(model: Any) -> str | None:
    """Guess a provider prefix from the model's class name.

    Returns ``None`` when nothing matches; callers should then default to
    routing through LiteLLM with no prefix.
    """
    class_name = type(model).__name__.lower()
    module = type(model).__module__.lower()
    blob = f"{module} {class_name}"
    for needle, prefix in _CLASS_PROVIDER_HINTS:
        if needle in blob:
            return prefix
    return None


def _extract_model_id(model: Any) -> str | None:
    """Pull a model identifier off the framework object.

    Tries the common attribute names used across frameworks: ``model``,
    ``model_name``, ``name``, ``deployment_name``. Returns ``None`` when
    nothing usable is found.
    """
    for attr in ("model", "model_name", "name", "deployment_name"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_optional_kwargs(model: Any) -> dict[str, Any]:
    """Pull api_key / api_base / region overrides off the framework object."""
    kwargs: dict[str, Any] = {}
    for attr, kw in (
        ("api_key", "api_key"),
        ("openai_api_key", "api_key"),
        ("anthropic_api_key", "api_key"),
        ("api_base", "api_base"),
        ("base_url", "api_base"),
        ("openai_api_base", "api_base"),
        ("region_name", "aws_region"),
        ("aws_region", "aws_region"),
    ):
        value = getattr(model, attr, None)
        # SecretStr-like wrappers expose .get_secret_value(); accept str directly.
        if value is None or kw in kwargs:
            continue
        if hasattr(value, "get_secret_value"):
            try:
                value = value.get_secret_value()
            except Exception:  # pragma: no cover - defensive
                continue
        if isinstance(value, str) and value:
            kwargs[kw] = value
    return kwargs


def llm_provider_from_framework_model(model: Any) -> LLMProvider:
    """Translate a framework-native chat model into an :class:`LLMProvider`.

    Strategy:

    1. Pull the model id off common attribute names.
    2. Pull api_key / api_base / region overrides similarly.
    3. Detect the provider prefix from the class name and prepend if the
       discovered model id is not already provider-prefixed.
    4. Hand off to :func:`from_provider`.

    Args:
        model: Any framework-native model object (LangChain BaseChatModel,
            Pydantic AI Model, LlamaIndex LLM, etc.).

    Returns:
        A configured :class:`LLMProvider`. When introspection cannot
        determine a model id at all, raises :class:`ValueError`.

    Raises:
        ValueError: When the model id cannot be introspected.
        ImportError: When no adapter is available for the resolved
            provider (re-raised from :func:`from_provider`).
    """
    from neo4j_agent_memory.llm import from_provider

    model_id = _extract_model_id(model)
    if model_id is None:
        raise ValueError(
            f"Could not introspect a model id from {type(model).__name__!r}. "
            f"Construct the LLMProvider directly with "
            f"neo4j_agent_memory.llm.from_provider('<provider>/<model>')."
        )

    kwargs = _extract_optional_kwargs(model)

    if "/" not in model_id:
        prefix = _detect_provider_prefix(model)
        if prefix is not None:
            model_id = f"{prefix}/{model_id}"

    logger.debug(
        "llm_provider_from_framework_model: %s -> %s (kwargs keys: %s)",
        type(model).__name__,
        model_id,
        sorted(kwargs.keys()),
    )
    # kwargs carries only api_key/api_base/region (never kind), so pass
    # kind="llm" explicitly: it resolves from_provider's overload to
    # LLMProvider (the **kwargs spread would otherwise erase it).
    return from_provider(model_id, kind="llm", **kwargs)


__all__ = [
    "llm_provider_from_framework_model",
]
