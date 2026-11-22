"""Native OpenAI adapter for the Provider Protocol.

Preferred over the LiteLLM path for any ``openai/*`` model because the
native SDK supports OpenAI's strict-mode structured output
(``response_format={"type": "json_schema", "strict": True}``), which has
materially better extraction quality than LiteLLM's normalized
``response_format`` mode.

Falls back to :func:`~neo4j_agent_memory.llm.structured.schema_aligned_extract`
for older OpenAI models that do not support strict mode.

Install with::

    pip install 'neo4j-agent-memory[openai]'
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from neo4j_agent_memory.llm import usage as usage_ledger
from neo4j_agent_memory.llm.defaults import lookup_embedding_dimensions
from neo4j_agent_memory.llm.errors import (
    ProviderAuthError,
    ProviderInvalidRequestError,
    ProviderRateLimitError,
    ProviderServiceError,
    ProviderTimeoutError,
    StructuredExtractionError,
)
from neo4j_agent_memory.llm.structured import schema_aligned_extract
from neo4j_agent_memory.llm.types import ChatMessage, Completion, Usage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from openai import AsyncOpenAI


logger = logging.getLogger(__name__)


T = TypeVar("T", bound=BaseModel)


def _strip_provider_prefix(model: str) -> str:
    """Strip the ``"openai/"`` provider prefix if present."""
    if model.startswith("openai/"):
        return model[len("openai/") :]
    return model


#: Model families that reason before they answer. They take no sampling
#: temperature, and their output limit covers the reasoning as well.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")

#: Output tokens allowed on top of a caller's limit for a reasoning model, so
#: that a limit sized for the visible answer does not cut the reasoning short
#: and leave the answer empty.
_REASONING_HEADROOM = 8192

#: Seconds allowed per request to a reasoning model unless the caller asks for
#: more: these models can take minutes over one answer.
_REASONING_TIMEOUT = 600.0


def is_reasoning_model(model: str) -> bool:
    """Whether ``model`` (with or without the ``openai/`` prefix) is a reasoning model."""
    return _strip_provider_prefix(model).startswith(_REASONING_PREFIXES)


def _translate_usage(usage: Any) -> Usage | None:
    """Translate an openai ``CompletionUsage`` to our :class:`Usage`."""
    if usage is None:
        return None
    cached = 0
    # New OpenAI models report cached prompt tokens inside ``prompt_tokens_details``
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    return Usage(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        cached_tokens=cached,
    )


class _OpenAISDKMixin:
    """Shared SDK initialization and exception translation for OpenAI adapters."""

    _client: AsyncOpenAI | None = None
    _api_key: str | None = None
    _api_base: str | None = None
    _organization: str | None = None
    _timeout: float = 60.0
    _max_retries: int = 3
    _default_headers: dict[str, str] | None = None

    def _ensure_client(self) -> AsyncOpenAI:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise ImportError(
                    "OpenAI SDK not installed. "
                    "Install with: pip install 'neo4j-agent-memory[openai]'"
                ) from exc
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._api_base,
                organization=self._organization,
                timeout=self._timeout,
                max_retries=self._max_retries,
                default_headers=self._default_headers,
            )
        return self._client


def _translate_openai_exception(exc: Exception) -> Exception:
    """Map an :mod:`openai` exception to a :class:`ProviderError` subclass.

    Returns the *translated* exception. Caller is responsible for raising
    it (so the original exception can be chained via ``from``).
    """
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            RateLimitError,
        )
    except ImportError:
        # If openai isn't installed we can't translate — re-raise the
        # original exception unchanged.
        return exc

    if isinstance(exc, AuthenticationError):
        return ProviderAuthError(str(exc))
    if isinstance(exc, RateLimitError):
        retry_after: float | None = None
        response = getattr(exc, "response", None)
        if response is not None:
            headers = getattr(response, "headers", {}) or {}
            raw = headers.get("retry-after") or headers.get("Retry-After")
            if raw is not None:
                try:
                    retry_after = float(raw)
                except (TypeError, ValueError):
                    retry_after = None
        return ProviderRateLimitError(str(exc), retry_after=retry_after)
    if isinstance(exc, APITimeoutError):
        return ProviderTimeoutError(str(exc))
    if isinstance(exc, BadRequestError):
        return ProviderInvalidRequestError(str(exc))
    if isinstance(exc, APIConnectionError):
        return ProviderServiceError(str(exc))
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None) or 0
        if status >= 500:
            return ProviderServiceError(str(exc))
        return ProviderInvalidRequestError(str(exc))
    return exc


class OpenAIProvider(_OpenAISDKMixin):
    """Native OpenAI LLM provider.

    Implements both :class:`~neo4j_agent_memory.llm.protocol.LLMProvider`
    and :class:`~neo4j_agent_memory.llm.protocol.StructuredExtractor`.

    Example::

        from neo4j_agent_memory.llm.adapters.openai import OpenAIProvider

        provider = OpenAIProvider(
            "openai/gpt-4o-mini",
            api_key="sk-...",
        )
        completion = await provider.complete([ChatMessage(role="user", content="hi")])
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        organization: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        return_raw: bool = False,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        # Canonical identifier always includes the openai/ prefix so the
        # value matches what users would pass to ``from_provider``. The SDK
        # itself is fed the bare model name.
        bare = _strip_provider_prefix(model)
        self.model = f"openai/{bare}"
        self._bare_model = bare
        self._api_key = api_key
        self._api_base = api_base
        self._organization = organization
        self._reasoning = is_reasoning_model(bare)
        self._timeout = max(timeout, _REASONING_TIMEOUT) if self._reasoning else timeout
        self._max_retries = max_retries
        self._default_headers = default_headers
        self._return_raw = return_raw

    def _sampling(self, temperature: float, max_tokens: int | None = None) -> dict[str, Any]:
        """Request arguments for the temperature and output limit asked for.

        A reasoning model accepts only its default temperature, so none is
        sent, and its limit is ``max_completion_tokens``, raised to leave room
        for the reasoning.
        """
        if not self._reasoning:
            arguments: dict[str, Any] = {"temperature": temperature}
            if max_tokens is not None:
                arguments["max_tokens"] = max_tokens
            return arguments
        if max_tokens is None:
            return {}
        return {"max_completion_tokens": max_tokens + _REASONING_HEADROOM}

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> Completion:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self._bare_model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            **self._sampling(temperature, max_tokens),
        }
        if stop is not None:
            kwargs["stop"] = list(stop)
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            translated = _translate_openai_exception(exc)
            if translated is exc:
                raise
            raise translated from exc

        usage_ledger.record_response(
            getattr(response, "model", None) or self._bare_model, getattr(response, "usage", None)
        )
        choice = response.choices[0]
        return Completion(
            content=choice.message.content or "",
            model=response.model,
            usage=_translate_usage(response.usage),
            finish_reason=choice.finish_reason,
            raw=response.model_dump() if self._return_raw else None,
        )

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        *,
        temperature: float = 0.0,
        max_retries: int = 2,
        timeout: float | None = None,
    ) -> T:
        """Use OpenAI strict-mode structured outputs when supported.

        Falls back to :func:`schema_aligned_extract` for models that do
        not support the strict ``response_format`` mode (older 3.5-class
        models, fine-tuned models without the JSON schema capability).
        """
        # Try OpenAI's strict structured output path first
        try:
            return await self._strict_structured(
                messages, response_model, temperature=temperature, timeout=timeout
            )
        except (ProviderInvalidRequestError, StructuredExtractionError):
            # Strict mode rejected for this model — fall back to SAP.
            logger.debug(
                "OpenAI strict mode unavailable for %s; falling back to SAP",
                self._bare_model,
            )
            return await schema_aligned_extract(
                self,
                messages,
                response_model,
                temperature=temperature,
                max_retries=max_retries,
                timeout=timeout,
            )

    async def _strict_structured(
        self,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        *,
        temperature: float,
        timeout: float | None,
    ) -> T:
        """One-shot strict-mode call. Raises on validation/schema errors."""
        client = self._ensure_client()
        schema = response_model.model_json_schema()
        # OpenAI's strict mode requires ``additionalProperties: false`` at
        # every object level. Pydantic-generated schemas don't include this
        # automatically; we patch the top-level schema here as a best-effort.
        # Adapters that need deeper guarantees should override.
        if isinstance(schema, dict) and schema.get("type") == "object":
            schema.setdefault("additionalProperties", False)

        kwargs: dict[str, Any] = {
            "model": self._bare_model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            **self._sampling(temperature),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            translated = _translate_openai_exception(exc)
            if translated is exc:
                raise
            raise translated from exc

        usage_ledger.record_response(
            getattr(response, "model", None) or self._bare_model, getattr(response, "usage", None)
        )
        content = response.choices[0].message.content or ""
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise StructuredExtractionError(
                f"OpenAI strict mode returned unparseable JSON: {exc}",
                last_attempts=[content],
            ) from exc
        try:
            return response_model.model_validate(payload)
        except ValidationError as exc:
            raise StructuredExtractionError(
                f"OpenAI strict mode response failed Pydantic validation: {exc}",
                last_attempts=[content],
                validation_errors=[exc],
            ) from exc


class OpenAIEmbeddingProvider(_OpenAISDKMixin):
    """Native OpenAI embedding provider.

    Auto-populates :attr:`dimensions` from the defaults table for known
    models (``text-embedding-3-small`` → 1536, ``text-embedding-3-large``
    → 3072, ``text-embedding-ada-002`` → 1536). For unknown models the
    caller must pass an explicit ``dimensions=N``.

    Honors OpenAI's ``dimensions`` parameter for the ``text-embedding-3-*``
    family (which supports dimension reduction): if the user passes a
    ``dimensions`` smaller than the model's native dimension, the call
    will request truncated embeddings.

    Implements :class:`~neo4j_agent_memory.llm.protocol.EmbeddingProvider`.
    """

    def __init__(
        self,
        model: str = "openai/text-embedding-3-small",
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        organization: str | None = None,
        dimensions: int | None = None,
        batch_size: int = 100,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        bare = _strip_provider_prefix(model)
        self.model = f"openai/{bare}"
        self._bare_model = bare
        self._api_key = api_key
        self._api_base = api_base
        self._organization = organization
        self._timeout = timeout
        self._max_retries = max_retries
        self._batch_size = batch_size

        # Track whether the user explicitly requested a dimension reduction
        # (so we can pass ``dimensions=`` to the OpenAI API).
        self._requested_dimensions = dimensions
        if dimensions is not None:
            self.dimensions = dimensions
        else:
            known = lookup_embedding_dimensions(self.model)
            if known is None:
                raise ValueError(
                    f"Could not determine dimensions for embedding model {self.model!r}. "
                    f"Pass dimensions=N explicitly or use a model in the defaults table "
                    f"(see neo4j_agent_memory.llm.defaults.EMBEDDING_DIMENSIONS)."
                )
            self.dimensions = known

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._ensure_client()
        all_embeddings: list[list[float]] = []

        # OpenAI accepts up to 2048 inputs per call but we batch by
        # ``batch_size`` for predictable memory characteristics.
        texts_list = list(texts)
        for start in range(0, len(texts_list), self._batch_size):
            batch = texts_list[start : start + self._batch_size]
            kwargs: dict[str, Any] = {"model": self._bare_model, "input": batch}
            if self._requested_dimensions is not None:
                kwargs["dimensions"] = self._requested_dimensions
            try:
                response = await client.embeddings.create(**kwargs)
            except Exception as exc:
                translated = _translate_openai_exception(exc)
                if translated is exc:
                    raise
                raise translated from exc
            usage_ledger.record_response(
                getattr(response, "model", None) or self._bare_model,
                getattr(response, "usage", None),
            )
            # OpenAI does not guarantee response order, so sort by index.
            sorted_data = sorted(response.data, key=lambda d: d.index)
            all_embeddings.extend(d.embedding for d in sorted_data)

        return all_embeddings

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]


__all__ = [
    "is_reasoning_model",
    "OpenAIProvider",
    "OpenAIEmbeddingProvider",
]
