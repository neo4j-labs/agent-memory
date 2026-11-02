"""Native OpenAI adapter for the Provider Protocol.

Preferred over the LiteLLM path for any ``openai/*`` model because the
native SDK supports OpenAI's strict-mode structured output
(``response_format={"type": "json_schema", "strict": True}``), which has
materially better extraction quality than LiteLLM's normalized
``response_format`` mode.

Falls back to :func:`~neo4j_agent_memory.llm.structured.schema_aligned_extract`
for older OpenAI models that do not support strict mode.

GPT-5-family and o-series reasoning models reject non-default sampling
parameters (``temperature`` / ``top_p`` / ``logprobs``) and the legacy
``max_tokens`` field, so for those models this adapter accepts the
protocol's ``temperature`` argument without forwarding it and sends the
token budget as ``max_completion_tokens`` instead. Older models keep the
historical request shape unchanged.

Install with::

    pip install 'neo4j-agent-memory[openai]'
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

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


# GPT-5-family models (``gpt-5``, ``gpt-5-mini``, ``gpt-5.1``, ...) and the
# o-series reasoning models (``o1``, ``o3``, ``o4-mini``, ...) only run at
# their default sampling settings: the API answers a request carrying
# ``temperature`` (or ``top_p`` / ``logprobs``) with a 400, and the output
# token budget must be sent as ``max_completion_tokens`` — ``max_tokens``
# is rejected the same way.
_FIXED_SAMPLING_MODEL_RE = re.compile(r"^(?:gpt-5|o\d)(?:[.\-]|$)")


def _rejects_sampling_parameters(bare_model: str) -> bool:
    """Whether the bare model name is a fixed-sampling reasoning model."""
    return _FIXED_SAMPLING_MODEL_RE.match(bare_model) is not None


_sampling_warning_emitted = False


def _note_dropped_temperature(temperature: float, bare_model: str) -> None:
    """Log once when a caller asks for a temperature the model cannot take.

    Silence would hide the difference between what the caller asked for
    and what was sent, so say it once. The protocol default (0.0) is not
    worth a warning: every caller passes it, dropped or not.
    """
    global _sampling_warning_emitted
    if temperature == 0.0 or _sampling_warning_emitted:
        return
    _sampling_warning_emitted = True
    logger.warning(
        "%s does not accept sampling parameters (temperature=%s ignored); "
        "GPT-5-family and o-series models only run at the default temperature.",
        bare_model,
        temperature,
    )


def _completion_sampling_kwargs(
    bare_model: str, temperature: float, max_tokens: int | None
) -> dict[str, Any]:
    """Sampling kwargs for one chat completion request.

    Fixed-sampling models get no ``temperature`` at all and the budget
    under ``max_completion_tokens``; every other model keeps the
    historical ``temperature`` / ``max_tokens`` shape.
    """
    if _rejects_sampling_parameters(bare_model):
        _note_dropped_temperature(temperature, bare_model)
        if max_tokens is None:
            return {}
        return {"max_completion_tokens": max_tokens}
    kwargs: dict[str, Any] = {"temperature": temperature}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return kwargs


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
        self._timeout = timeout
        self._max_retries = max_retries
        self._default_headers = default_headers
        self._return_raw = return_raw

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
        }
        kwargs.update(_completion_sampling_kwargs(self._bare_model, temperature, max_tokens))
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
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if _rejects_sampling_parameters(self._bare_model):
            _note_dropped_temperature(temperature, self._bare_model)
        else:
            kwargs["temperature"] = temperature
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            translated = _translate_openai_exception(exc)
            if translated is exc:
                raise
            raise translated from exc

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
            # OpenAI does not guarantee response order, so sort by index.
            sorted_data = sorted(response.data, key=lambda d: d.index)
            all_embeddings.extend(d.embedding for d in sorted_data)

        return all_embeddings

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]


__all__ = [
    "OpenAIProvider",
    "OpenAIEmbeddingProvider",
]
