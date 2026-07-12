"""Optional Instructor adapter for the Provider Protocol.

Thin wrapper around the `Instructor library <https://python.useinstructor.com/>`_
for users who already use Instructor in their stack and want to plug it
into neo4j-agent-memory's extraction pipeline.

This is intentionally a Silver-tier-only adapter: it implements
:class:`~neo4j_agent_memory.llm.protocol.StructuredExtractor` but not
:class:`~neo4j_agent_memory.llm.protocol.LLMProvider`. Plain chat
completions are better served by the native or LiteLLM adapter — what
Instructor uniquely offers is its structured-output quality.

Install with::

    pip install 'neo4j-agent-memory[instructor]'
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeVar, cast

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neo4j_agent_memory.llm.types import ChatMessage


logger = logging.getLogger(__name__)


T = TypeVar("T", bound=BaseModel)


class InstructorProvider:
    """Structured-output provider backed by Instructor.

    Implements :class:`~neo4j_agent_memory.llm.protocol.StructuredExtractor`
    only. ``isinstance(provider, LLMProvider)`` returns False — by design.

    This adapter always uses an async Instructor client because
    :meth:`complete_structured` is a coroutine. Passing a sync client
    (``async_client=False``) would raise at call time — it is not supported.

    Example::

        from neo4j_agent_memory.llm.adapters.instructor import InstructorProvider

        provider = InstructorProvider("openai/gpt-4o-mini")
        result = await provider.complete_structured(messages, ResponseModel)
    """

    def __init__(
        self,
        model: str,
        **provider_kwargs: Any,
    ) -> None:
        try:
            import instructor
        except ImportError as exc:
            raise ImportError(
                "Instructor not installed. "
                "Install with: pip install 'neo4j-agent-memory[instructor]'"
            ) from exc
        self.model = model
        # ``async_client`` was removed as a public parameter (breaking change):
        # the adapter is unconditionally async because ``complete_structured`` is
        # a coroutine. Detect a stale caller explicitly rather than letting it hit
        # the confusing "multiple values for keyword argument 'async_client'"
        # TypeError from the ``from_provider`` call below.
        if "async_client" in provider_kwargs:
            raise TypeError(
                "InstructorProvider no longer accepts an 'async_client' argument; "
                "the adapter is always async (complete_structured is a coroutine). "
                "Remove it. See CHANGELOG for this breaking change."
            )
        # ``instructor.from_provider`` resolves provider strings of the same
        # ``"provider/model"`` shape we use, so the integration is seamless.
        # We always request the async client (Literal[True]) because
        # ``complete_structured`` is a coroutine; a sync client cannot be
        # awaited and would raise at call time.
        self._client = instructor.from_provider(model, async_client=True, **provider_kwargs)

    async def complete_structured(
        self,
        messages: Sequence[ChatMessage],
        response_model: type[T],
        *,
        temperature: float = 0.0,
        max_retries: int = 2,
        timeout: float | None = None,
    ) -> T:
        kwargs: dict[str, Any] = {
            "response_model": response_model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "max_retries": max_retries,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        # instructor.AsyncInstructor.create() is typed as returning ``T | Any``
        # (the union widens to Any). cast(T, ...) is safe here: the library
        # validates the response against ``response_model`` before returning it.
        return cast(T, await self._client.create(**kwargs))


__all__ = [
    "InstructorProvider",
]
