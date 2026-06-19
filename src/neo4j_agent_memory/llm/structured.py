"""Schema-aligned structured extraction with retry-on-validation-error.

This is the "Option B" implementation of the structured-output strategy
discussed in the PRD (Section 10). It owns the entity-extraction quality
of the library — every adapter without native structured-output support
delegates here.

The algorithm:

1. Build a system message containing the JSON schema (derived from the
   Pydantic ``response_model``) and instructions to return only valid JSON.
2. Call the provider's :meth:`LLMProvider.complete`.
3. Tolerant-parse the response (strip markdown fences, smart-quotes, trailing
   commas; find first balanced ``{...}`` block).
4. Validate the parsed JSON against ``response_model``.
5. On success: return.
6. On failure: append the failed attempt and a validation-error feedback
   message to the conversation, then retry from step 2.
7. After ``max_retries + 1`` total attempts: raise
   :class:`StructuredExtractionError` carrying every attempt and validation
   error for diagnosability.

Adapters with native structured output (OpenAI strict mode, Anthropic
forced tool use) override :meth:`StructuredExtractor.complete_structured`
and fall back to this function when the model does not support the native
mode. This function is the safety net.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel, ValidationError

from neo4j_agent_memory.llm.errors import StructuredExtractionError
from neo4j_agent_memory.llm.types import ChatMessage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from neo4j_agent_memory.llm.protocol import LLMProvider


logger = logging.getLogger(__name__)


T = TypeVar("T", bound=BaseModel)


SYSTEM_TEMPLATE = """You are a structured data extraction system.

Return ONLY a JSON object that validates against this schema:

{schema}

Rules:
- Output JSON only. No prose, no markdown fences, no preamble.
- All required fields must be present.
- Use null for unknown optional fields.
- Do not include any explanation outside the JSON object.
"""


# Greedy by design: the LLM is asked for a single JSON object, so the
# largest balanced ``{...}`` span is almost always the right one.
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


async def schema_aligned_extract(
    provider: LLMProvider,
    messages: Sequence[ChatMessage],
    response_model: type[T],
    *,
    temperature: float = 0.0,
    max_retries: int = 2,
    timeout: float | None = None,
) -> T:
    """Extract a validated Pydantic model from an LLM via retry-with-feedback.

    Args:
        provider: An :class:`LLMProvider` to make the completion calls.
        messages: User-supplied messages; the system message containing the
            schema is prepended automatically.
        response_model: Pydantic model class describing the expected output.
        temperature: Sampling temperature (passed to every attempt).
        max_retries: Maximum retry attempts after the initial attempt. The
            total number of LLM calls is ``max_retries + 1``.
        timeout: Per-call timeout in seconds.

    Returns:
        A validated instance of ``response_model``.

    Raises:
        StructuredExtractionError: All ``max_retries + 1`` attempts failed,
            either at the JSON-parse stage or the Pydantic-validation stage.
        ProviderError: Any of the standard provider errors during the
            underlying ``complete`` calls.
    """
    schema_json = json.dumps(response_model.model_json_schema(), indent=2)
    system = ChatMessage(role="system", content=SYSTEM_TEMPLATE.format(schema=schema_json))
    convo: list[ChatMessage] = [system, *messages]

    attempts: list[str] = []
    validation_errors: list[ValidationError] = []

    last_exception: Exception | None = None
    for attempt in range(max_retries + 1):
        completion = await provider.complete(convo, temperature=temperature, timeout=timeout)
        attempts.append(completion.content)

        try:
            payload = _tolerant_json_parse(completion.content)
            return response_model.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_exception = exc
            if isinstance(exc, ValidationError):
                validation_errors.append(exc)

            if attempt >= max_retries:
                # Last attempt — fall through to the raise below.
                break

            logger.info(
                "SAP retry %d/%d for %s due to %s",
                attempt + 1,
                max_retries,
                response_model.__name__,
                type(exc).__name__,
            )

            # Append the failed assistant response and a feedback message
            # asking the model to correct it.
            convo = [
                *convo,
                ChatMessage(role="assistant", content=completion.content),
                ChatMessage(
                    role="user",
                    content=_format_retry_prompt(exc, response_model),
                ),
            ]

    raise StructuredExtractionError(
        f"Failed to produce valid {response_model.__name__} after "
        f"{max_retries + 1} attempts. Last error: {last_exception}",
        last_attempts=attempts,
        validation_errors=validation_errors,
    ) from last_exception


def _tolerant_json_parse(text: str) -> dict[str, Any]:
    """Extract a JSON object from messy LLM output.

    Handles five common failure modes:

    * Plain JSON — pass through.
    * Markdown-fenced JSON — strip the fence.
    * Chain-of-thought prefix — find the first balanced ``{...}``.
    * Smart quotes (U+201C, U+201D, U+2018, U+2019) — normalize.
    * Trailing commas before ``}`` or ``]`` — strip.

    Truncated JSON (mid-string cutoff) surfaces as :class:`json.JSONDecodeError`,
    which is the right outcome — it must trigger a retry, not silently succeed
    with wrong data.
    """
    text = text.strip()

    # Strip markdown fence if present
    if "```" in text:
        match = _FENCE_RE.search(text)
        if match:
            text = match.group(1).strip()

    # Try direct parse first — the common happy path
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        # If the LLM returned a list at the top level, wrap it. This handles
        # the case where the prompt asked for an object but the model emitted
        # a list — Pydantic validation will then complain meaningfully.
        raise json.JSONDecodeError(f"Expected JSON object, got {type(result).__name__}", text, 0)
    except json.JSONDecodeError:
        pass

    # Find the first balanced {...} block, greedy
    match = _OBJECT_RE.search(text)
    if not match:
        raise json.JSONDecodeError("no JSON object found in response", text, 0)
    candidate = match.group(0)

    # Common cleanups for malformed JSON
    candidate = (
        candidate.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    # Strip trailing commas before closing brace/bracket: ``{"a": 1,}`` → ``{"a": 1}``
    candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)

    result = json.loads(candidate)
    if not isinstance(result, dict):
        raise json.JSONDecodeError(
            f"Expected JSON object, got {type(result).__name__}", candidate, 0
        )
    return result


def _format_retry_prompt(error: Exception, model: type[BaseModel]) -> str:
    """Build a feedback message describing what went wrong on the previous attempt.

    The point of this message is to give the LLM enough signal to correct
    itself on the next attempt. We cite the validation error path explicitly
    because models respond to "the field X is invalid" much better than
    "your response was invalid."
    """
    if isinstance(error, ValidationError):
        issues_lines = []
        for err in error.errors():
            loc = ".".join(str(p) for p in err.get("loc", []))
            msg = err.get("msg", "")
            issues_lines.append(f"- {loc}: {msg}")
        issues = "\n".join(issues_lines) if issues_lines else "(no details available)"
        return (
            f"Your previous response failed schema validation:\n{issues}\n\n"
            f"Return a corrected JSON object that validates against the "
            f"{model.__name__} schema. Output JSON only — no prose, no markdown."
        )
    # JSONDecodeError or anything else
    return (
        f"Your previous response was not valid JSON: {error}\n\n"
        f"Return a valid JSON object that matches the {model.__name__} schema. "
        f"Output JSON only — no prose, no markdown."
    )


__all__ = [
    "schema_aligned_extract",
    "SYSTEM_TEMPLATE",
]
