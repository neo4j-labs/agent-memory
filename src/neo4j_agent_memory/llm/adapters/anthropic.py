"""Native Anthropic adapter for the Provider Protocol.

Preferred over LiteLLM for ``anthropic/*`` models because:

* The native SDK supports Anthropic's prompt caching via the
  ``cache_control`` header — opt-in via ``cache_system=True``.
* :meth:`AnthropicProvider.complete_structured` uses *forced tool use*
  (the most reliable structured-output mode on Claude) instead of
  prompt-based JSON instruction. The model is asked to call a single
  named tool whose input schema is the response model.

Falls back to :func:`~neo4j_agent_memory.llm.structured.schema_aligned_extract`
if the tool-use response is malformed.

.. note::
   The Anthropic SDK 1.x removed ``temperature`` / ``top_p`` / ``top_k`` from
   ``messages.create()`` (passing one is a :class:`TypeError`), mirroring the
   API, which rejects sampling parameters on current Claude models. This adapter
   therefore accepts ``temperature`` for protocol compatibility but does not
   send it; a non-default value is logged once. Use Anthropic's
   ``output_config.effort`` knob for the equivalent control.

Install with::

    pip install 'neo4j-agent-memory[anthropic]'
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from neo4j_agent_memory.llm.errors import (
    ProviderAuthError,
    ProviderInvalidRequestError,
    ProviderRateLimitError,
    ProviderServiceError,
    ProviderTimeoutError,
)
from neo4j_agent_memory.llm.structured import schema_aligned_extract
from neo4j_agent_memory.llm.types import ChatMessage, Completion, Usage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from anthropic import AsyncAnthropic


logger = logging.getLogger(__name__)


T = TypeVar("T", bound=BaseModel)


_DEFAULT_MAX_TOKENS = 4096

_sampling_warning_emitted = False


def _note_dropped_temperature(temperature: float) -> None:
    """Log once when a caller asks for a temperature Anthropic no longer accepts.

    ``messages.create()`` has no ``temperature`` parameter on the anthropic 1.x
    line, and the API rejects sampling parameters outright on current Claude
    models, so the value cannot be forwarded. Silence would hide the difference
    between what the caller asked for and what was sent, so say it once.
    """
    global _sampling_warning_emitted
    if temperature == 0.0 or _sampling_warning_emitted:
        return
    _sampling_warning_emitted = True
    logger.warning(
        "Anthropic no longer accepts sampling parameters (temperature=%s ignored); "
        "current Claude models reject them. Use output_config.effort instead.",
        temperature,
    )


def _strip_provider_prefix(model: str) -> str:
    if model.startswith("anthropic/"):
        return model[len("anthropic/") :]
    return model


def _split_system_message(
    messages: Sequence[ChatMessage],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Extract the first system message; return ``(system, remaining)``.

    Anthropic requires the system instruction to be a top-level ``system=``
    parameter, not a message with ``role='system'``. The remaining
    messages are converted to Anthropic's expected dict shape.
    """
    system: str | None = None
    remaining: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system" and system is None:
            system = msg.content
            continue
        # Anthropic accepts 'user' | 'assistant' roles. 'tool' messages
        # should be folded into 'user' messages with tool_use_result content
        # — for our purposes (extraction + summarization), bare text is enough.
        role = msg.role if msg.role in ("user", "assistant") else "user"
        remaining.append({"role": role, "content": msg.content})
    return system, remaining


def _translate_usage(usage: Any) -> Usage | None:
    """Translate an anthropic ``Usage`` object to our :class:`Usage`."""
    if usage is None:
        return None
    prompt = getattr(usage, "input_tokens", 0) or 0
    completion = getattr(usage, "output_tokens", 0) or 0
    # Anthropic prompt caching exposes ``cache_read_input_tokens`` and
    # ``cache_creation_input_tokens``. We surface the read tokens as cached.
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        cached_tokens=cached,
    )


def _translate_anthropic_exception(exc: Exception) -> Exception:
    """Map an :mod:`anthropic` exception to a :class:`ProviderError` subclass."""
    try:
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            RateLimitError,
        )
    except ImportError:
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


class AnthropicProvider:
    """Native Anthropic LLM provider.

    Implements both :class:`~neo4j_agent_memory.llm.protocol.LLMProvider`
    and :class:`~neo4j_agent_memory.llm.protocol.StructuredExtractor`.

    Example::

        from neo4j_agent_memory.llm.adapters.anthropic import AnthropicProvider

        provider = AnthropicProvider(
            "anthropic/claude-opus-5",
            api_key="sk-ant-...",
            cache_system=True,  # opt-in prompt caching
        )
        completion = await provider.complete([ChatMessage(role="user", content="hi")])
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        return_raw: bool = False,
        cache_system: bool = False,
        default_max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        bare = _strip_provider_prefix(model)
        self.model = f"anthropic/{bare}"
        self._bare_model = bare
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout
        self._max_retries = max_retries
        self._return_raw = return_raw
        self._cache_system = cache_system
        self._default_max_tokens = default_max_tokens
        self._client: AsyncAnthropic | None = None

    def _ensure_client(self) -> AsyncAnthropic:
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:
                raise ImportError(
                    "Anthropic SDK not installed. "
                    "Install with: pip install 'neo4j-agent-memory[anthropic]'"
                ) from exc
            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "timeout": self._timeout,
                "max_retries": self._max_retries,
            }
            if self._api_base is not None:
                kwargs["base_url"] = self._api_base
            self._client = AsyncAnthropic(**kwargs)
        return self._client

    def _build_system_param(self, system: str | None) -> Any:
        """Build the ``system=`` parameter, honoring ``cache_system``.

        When prompt caching is enabled and there's a system message, send
        it as a list with ``cache_control={"type": "ephemeral"}`` so
        Anthropic caches the system prefix across calls.
        """
        if system is None:
            return None
        if not self._cache_system:
            return system
        return [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]

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
        _note_dropped_temperature(temperature)
        system, anth_messages = _split_system_message(messages)
        kwargs: dict[str, Any] = {
            "model": self._bare_model,
            "messages": anth_messages,
            "max_tokens": max_tokens if max_tokens is not None else self._default_max_tokens,
        }
        system_param = self._build_system_param(system)
        if system_param is not None:
            kwargs["system"] = system_param
        if stop is not None:
            kwargs["stop_sequences"] = list(stop)
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            translated = _translate_anthropic_exception(exc)
            if translated is exc:
                raise
            raise translated from exc

        # Anthropic returns a list of content blocks. Concatenate text blocks.
        text_parts: list[str] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", ""))
        return Completion(
            content="".join(text_parts),
            model=response.model,
            usage=_translate_usage(response.usage),
            finish_reason=response.stop_reason,
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
        """Use Anthropic forced tool use for structured extraction.

        Converts ``response_model`` into a tool spec, forces the model to
        call that single tool, then validates the tool's input arguments
        against the Pydantic schema. Falls back to
        :func:`schema_aligned_extract` on tool-use failure.
        """
        client = self._ensure_client()
        _note_dropped_temperature(temperature)
        system, anth_messages = _split_system_message(messages)
        schema = response_model.model_json_schema()

        tool_spec = {
            "name": "submit_extraction",
            "description": (
                f"Submit a structured extraction matching the {response_model.__name__} schema."
            ),
            "input_schema": schema,
        }

        kwargs: dict[str, Any] = {
            "model": self._bare_model,
            "messages": anth_messages,
            "max_tokens": self._default_max_tokens,
            "tools": [tool_spec],
            "tool_choice": {"type": "tool", "name": "submit_extraction"},
        }
        system_param = self._build_system_param(system)
        if system_param is not None:
            kwargs["system"] = system_param
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            translated = _translate_anthropic_exception(exc)
            if translated is exc:
                raise
            raise translated from exc

        # Find the tool_use block and parse its input
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                tool_input = getattr(block, "input", {}) or {}
                try:
                    return response_model.model_validate(tool_input)
                except ValidationError as exc:
                    logger.debug(
                        "Anthropic tool-use response failed validation; falling back to SAP: %s",
                        exc,
                    )
                    return await schema_aligned_extract(
                        self,
                        messages,
                        response_model,
                        temperature=temperature,
                        max_retries=max_retries,
                        timeout=timeout,
                    )

        # No tool_use block — model didn't actually call the tool.
        logger.debug("Anthropic response contained no tool_use block; falling back to SAP")
        return await schema_aligned_extract(
            self,
            messages,
            response_model,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
        )


__all__ = [
    "AnthropicProvider",
]
