"""Judges: models that answer closed questions about a piece of state.

Much of what a memory has to decide is not writing but judging: does this
statement replace that one, does this instruction apply to this question, is
this exchange relevant. A :class:`Judge` answers such questions. It is given one
*state* (any JSON-like value) and a set of named questions, each either a
:class:`YesNo` or a :class:`OneOf`, and returns a :class:`Judgement` with a
probability for each.

Two judges are provided:

* :class:`TypeSafeJudge` asks `Jev <https://docs.typesafe.ai>`_, a hosted
  decision model that returns calibrated probabilities, takes many questions
  about one state in a single request, and answers in a few hundred
  milliseconds. It needs the ``typesafe-sdk`` package and an API key.
* :class:`LLMJudge` asks the configured LLM provider through structured output.
  Its answers are hard: every probability is 0 or 1.

Questions refer to parts of the state by path, in backticks: with the state
``{"denial": "...", "statements": ["...", "..."]}`` a question can ask whether
```statements[1]``` conflicts with ```denial```.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from neo4j_agent_memory.llm.protocol import LLMProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class YesNo:
    """A question with a yes or no answer.

    ``yes`` and ``no`` optionally describe what each answer means. Phrase the
    question so that yes is the case you act on: judges answer a positively
    phrased question more reliably than its negation.
    """

    question: str
    yes: str | None = None
    no: str | None = None


@dataclass(frozen=True)
class OneOf:
    """A question answered by picking one of several named options.

    ``options`` maps each option's name to a description of it (or None). Put
    the option that means "do nothing" first: where a judge is unsure, it leans
    toward the first option.
    """

    question: str
    options: Mapping[str, str | None]


Question = YesNo | OneOf


@dataclass(frozen=True)
class Judgement:
    """A judge's answer to one question.

    For a :class:`YesNo`, ``probability`` is the probability of yes. For a
    :class:`OneOf`, ``choice`` is the option picked, ``probabilities`` gives
    every option's probability and ``probability`` is that of the choice.
    """

    probability: float
    choice: str | None = None
    probabilities: Mapping[str, float] = field(default_factory=dict)

    def of(self, option: str) -> float:
        """The probability of ``option`` (0.0 for an option not offered)."""
        return float(self.probabilities.get(option, 0.0))


@runtime_checkable
class Judge(Protocol):
    """Answers closed questions about a state."""

    name: str

    async def judge(self, state: Any, questions: Mapping[str, Question]) -> dict[str, Judgement]:
        """Answer every question about ``state``.

        Returns one judgement per question name. A judge that cannot answer
        raises; callers decide whether to do without.
        """
        ...


class TypeSafeJudge:
    """A :class:`Judge` backed by TypeSafe's Jev decision model.

    Args:
        api_key: The TypeSafe API key; defaults to ``TYPESAFE_API_KEY``.
        model: The model id. Pin a version (``jev-1.14.0``) for reproducible
            judgements; ``jev-latest`` follows new releases.
        max_concurrency: Requests in flight at once, across all callers.
        timeout: Seconds allowed per request.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "jev-1.14.0",
        max_concurrency: int = 16,
        timeout: float = 30.0,
    ) -> None:
        try:
            from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        except ImportError as exc:  # pragma: no cover - exercised without the extra
            raise ImportError(
                "TypeSafeJudge needs the typesafe-sdk package: "
                "pip install 'neo4j-agent-memory[typesafe]'"
            ) from exc
        key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not key:
            raise ValueError("TypeSafeJudge needs an API key (api_key= or TYPESAFE_API_KEY).")
        self.name = model
        self._client = AsyncTypeSafeClient(
            api_key=key,
            model=model,
            timeout=timeout,
            retry=RetryPolicy(max_retries=4, backoff_initial=0.5, backoff_max=8.0),
        )
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        #: Requests made, questions asked and input tokens billed so far.
        self.usage: dict[str, int] = {"requests": 0, "questions": 0, "input_tokens": 0}

    async def judge(self, state: Any, questions: Mapping[str, Question]) -> dict[str, Judgement]:
        if not questions:
            return {}
        payload: dict[str, Any] = {}
        for name, question in questions.items():
            if isinstance(question, YesNo):
                item: dict[str, Any] = {"type": "noul", "instructions": question.question}
                criteria = {
                    key: text
                    for key, text in (("true", question.yes), ("false", question.no))
                    if text
                }
                if criteria:
                    item["criteria"] = criteria
            else:
                item = {
                    "type": "choice",
                    "instructions": question.question,
                    "criteria": dict(question.options),
                }
            payload[name] = item
        async with self._semaphore:
            response = await self._client.system_one(state=state, questions=payload)
        self.usage["requests"] += 1
        self.usage["questions"] += len(payload)
        if response.usage is not None and response.usage.input_tokens:
            self.usage["input_tokens"] += int(response.usage.input_tokens)
        answers: dict[str, Judgement] = {}
        for name, question in questions.items():
            answer: Any = response.answers[name]
            if isinstance(question, YesNo):
                answers[name] = Judgement(probability=float(answer.noul))
            else:
                probabilities = {k: float(v) for k, v in answer.probabilities.items()}
                answers[name] = Judgement(
                    probability=probabilities.get(answer.choice, 0.0),
                    choice=answer.choice,
                    probabilities=probabilities,
                )
        return answers

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


_LLM_JUDGE_PROMPT = """\
You are given a STATE as JSON and a list of numbered QUESTIONS about it. A \
question refers to a part of the state by its path, in backticks, for example \
`statements[2]`. Answer every question on its own, from the state alone. For a \
yes/no question answer true or false; otherwise answer with exactly one of the \
options listed for it.
"""


class LLMJudge:
    """A :class:`Judge` that asks an LLM provider through structured output.

    Every answer is hard: a yes is probability 1.0, a no is 0.0, and the option
    picked gets 1.0. Use it where no decision model is available, or as a
    control when measuring what one adds.
    """

    def __init__(
        self, llm: LLMProvider, *, name: str | None = None, max_concurrency: int = 16
    ) -> None:
        self._llm: Any = llm
        self.name = name or f"llm:{getattr(llm, 'model', None) or type(llm).__name__}"
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self.usage: dict[str, int] = {"requests": 0, "questions": 0}

    async def judge(self, state: Any, questions: Mapping[str, Question]) -> dict[str, Judgement]:
        if not questions:
            return {}
        from pydantic import create_model

        from neo4j_agent_memory.llm.types import ChatMessage

        names = list(questions)
        fields: dict[str, Any] = {}
        lines: list[str] = []
        for number, name in enumerate(names, start=1):
            question = questions[name]
            if isinstance(question, YesNo):
                fields[f"q{number}"] = (bool, ...)
                line = f"Q{number} (true or false): {question.question}"
                if question.yes:
                    line += f"\n   true: {question.yes}"
                if question.no:
                    line += f"\n   false: {question.no}"
            else:
                options = tuple(question.options)
                fields[f"q{number}"] = (Literal[options], ...)
                line = f"Q{number} (one of {', '.join(options)}): {question.question}"
                for option, described in question.options.items():
                    if described:
                        line += f"\n   {option}: {described}"
            lines.append(line)
        model: Any = create_model("Answers", **fields)
        prompt = [
            ChatMessage(role="system", content=_LLM_JUDGE_PROMPT),
            ChatMessage(
                role="user",
                content="STATE:\n"
                + json.dumps(state, ensure_ascii=False, indent=1, default=str)
                + "\n\nQUESTIONS:\n"
                + "\n".join(lines),
            ),
        ]
        async with self._semaphore:
            answered: Any = await self._llm.complete_structured(prompt, model, temperature=0.0)
        self.usage["requests"] += 1
        self.usage["questions"] += len(names)
        answers: dict[str, Judgement] = {}
        for number, name in enumerate(names, start=1):
            value = getattr(answered, f"q{number}")
            question = questions[name]
            if isinstance(question, YesNo):
                answers[name] = Judgement(probability=1.0 if value else 0.0)
            else:
                answers[name] = Judgement(
                    probability=1.0,
                    choice=str(value),
                    probabilities={o: (1.0 if o == value else 0.0) for o in question.options},
                )
        return answers


async def judge_safely(
    judge: Judge,
    state: Any,
    questions: Mapping[str, Question],
    *,
    what: str,
    retries: int = 2,
) -> dict[str, Judgement] | None:
    """Ask ``judge``, retrying failures. None when it never answers.

    The memory keeps working without a judgement, so a failed request is logged
    and reported as None instead of raised.
    """
    for attempt in range(1, retries + 2):
        try:
            return await judge.judge(state, questions)
        except Exception as exc:  # noqa: BLE001 - provider errors vary
            if attempt > retries:
                logger.warning("Judgement %s failed after %d attempts: %s", what, attempt, exc)
                return None
            await asyncio.sleep(1.0 * attempt)
    return None
