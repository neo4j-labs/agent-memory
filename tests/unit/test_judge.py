"""Unit tests for judges (``neo4j_agent_memory.judge``).

The TypeSafe client and the LLM are faked: these tests cover what each judge
sends and how it reads the answer.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from neo4j_agent_memory.judge import (
    Judge,
    Judgement,
    JudgeUnavailable,
    LLMJudge,
    OneOf,
    TypeSafeJudge,
    YesNo,
    judge_safely,
)

QUESTIONS = {
    "conflict": YesNo("Does `statements[0]` conflict with `denial`?", yes="It does.", no=None),
    "relation": OneOf(
        "How does `new` relate to `earlier[0]`?",
        {"unrelated": "Different things.", "same": None, "update": "A new value."},
    ),
}


class TestJudgement:
    def test_of_gives_an_options_probability(self) -> None:
        judgement = Judgement(0.7, "update", {"update": 0.7, "same": 0.2, "unrelated": 0.1})
        assert judgement.of("same") == 0.2
        assert judgement.of("missing") == 0.0


class FakeStructuredLLM:
    model = "fake/model"

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.prompts: list[str] = []
        self.models: list[type] = []

    async def complete_structured(self, messages, response_model, **_: Any):
        self.prompts.append(messages[-1].content)
        self.models.append(response_model)
        return response_model(**self.answers)


class TestLLMJudge:
    @pytest.mark.asyncio
    async def test_answers_are_hard(self) -> None:
        llm = FakeStructuredLLM({"q1": True, "q2": "update"})
        judge = LLMJudge(llm)  # type: ignore[arg-type]
        answers = await judge.judge({"denial": "never"}, QUESTIONS)
        assert answers["conflict"].probability == 1.0
        assert answers["relation"].choice == "update"
        assert answers["relation"].probabilities == {"unrelated": 0.0, "same": 0.0, "update": 1.0}
        assert judge.name == "llm:fake/model"
        assert judge.usage == {"requests": 1, "questions": 2}

    @pytest.mark.asyncio
    async def test_the_prompt_shows_the_state_and_every_option(self) -> None:
        llm = FakeStructuredLLM({"q1": False, "q2": "unrelated"})
        answers = await LLMJudge(llm).judge({"denial": "never"}, QUESTIONS)  # type: ignore[arg-type]
        prompt = llm.prompts[0]
        assert '"denial": "never"' in prompt
        assert "Q1 (true or false): Does `statements[0]` conflict with `denial`?" in prompt
        assert "true: It does." in prompt
        assert "Q2 (one of unrelated, same, update)" in prompt
        assert "update: A new value." in prompt
        assert answers["conflict"].probability == 0.0

    @pytest.mark.asyncio
    async def test_an_option_outside_the_list_is_refused_by_the_schema(self) -> None:
        llm = FakeStructuredLLM({"q1": True, "q2": "contradiction"})
        with pytest.raises(ValueError):
            await LLMJudge(llm).judge({}, QUESTIONS)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_no_questions_no_call(self) -> None:
        llm = FakeStructuredLLM({})
        assert await LLMJudge(llm).judge({}, {}) == {}  # type: ignore[arg-type]
        assert llm.prompts == []

    def test_is_a_judge(self) -> None:
        assert isinstance(LLMJudge(FakeStructuredLLM({})), Judge)  # type: ignore[arg-type]


class _Answer:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


class FakeTypeSafeClient:
    """Stands in for ``typesafe_sdk.AsyncTypeSafeClient``."""

    last: FakeTypeSafeClient | None = None

    def __init__(self, **options: Any) -> None:
        self.options = options
        self.calls: list[dict[str, Any]] = []
        FakeTypeSafeClient.last = self

    async def system_one(self, *, state: Any, questions: dict[str, Any]) -> Any:
        self.calls.append({"state": state, "questions": questions})
        answers = {
            "conflict": _Answer(noul=0.83),
            "relation": _Answer(
                choice="update", probabilities={"unrelated": 0.1, "same": 0.2, "update": 0.7}
            ),
        }
        return _Answer(
            answers={name: answers[name] for name in questions},
            usage=_Answer(input_tokens=321, output_tokens=5),
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def typesafe_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("typesafe_sdk")
    module.AsyncTypeSafeClient = FakeTypeSafeClient  # type: ignore[attr-defined]
    module.RetryPolicy = lambda **options: options  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)


class TestTypeSafeJudge:
    @pytest.mark.asyncio
    async def test_questions_become_nouls_and_choices(self, typesafe_sdk: None) -> None:
        judge = TypeSafeJudge(api_key="k", model="jev-test")
        answers = await judge.judge({"denial": "never"}, QUESTIONS)
        client = FakeTypeSafeClient.last
        assert client is not None and client.options["model"] == "jev-test"
        sent = client.calls[0]
        assert sent["state"] == {"denial": "never"}
        assert sent["questions"]["conflict"] == {
            "type": "noul",
            "instructions": "Does `statements[0]` conflict with `denial`?",
            "criteria": {"true": "It does."},
        }
        assert sent["questions"]["relation"]["type"] == "choice"
        assert list(sent["questions"]["relation"]["criteria"]) == ["unrelated", "same", "update"]
        assert answers["conflict"].probability == 0.83
        assert answers["relation"].choice == "update"
        assert answers["relation"].probability == 0.7
        assert answers["relation"].of("same") == 0.2

    @pytest.mark.asyncio
    async def test_usage_is_counted(self, typesafe_sdk: None) -> None:
        judge = TypeSafeJudge(api_key="k")
        await judge.judge({}, QUESTIONS)
        await judge.judge({}, {"conflict": QUESTIONS["conflict"]})
        assert judge.usage == {"requests": 2, "questions": 3, "input_tokens": 642}
        assert judge.name == "jev-1.14.0"

    def test_a_key_is_required(self, typesafe_sdk: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key"):
            TypeSafeJudge()

    def test_the_key_is_read_from_the_environment(
        self, typesafe_sdk: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TYPESAFE_API_KEY", "from-env")
        TypeSafeJudge()
        assert FakeTypeSafeClient.last is not None
        assert FakeTypeSafeClient.last.options["api_key"] == "from-env"


class FlakyJudge:
    name = "flaky"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def judge(self, state: Any, questions: Any) -> dict[str, Judgement]:
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("rate limited")
        return {name: Judgement(1.0) for name in questions}


class TestJudgeSafely:
    @pytest.mark.asyncio
    async def test_a_failure_is_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def no_sleep(_: float) -> None:
            return None

        monkeypatch.setattr("neo4j_agent_memory.judge.asyncio.sleep", no_sleep)
        judge = FlakyJudge(failures=1)
        answers = await judge_safely(judge, {}, {"q": YesNo("?")}, what="test")
        assert answers is not None and answers["q"].probability == 1.0
        assert judge.calls == 2

    @pytest.mark.asyncio
    async def test_giving_up_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def no_sleep(_: float) -> None:
            return None

        monkeypatch.setattr("neo4j_agent_memory.judge.asyncio.sleep", no_sleep)
        judge = FlakyJudge(failures=10)
        assert await judge_safely(judge, {}, {"q": YesNo("?")}, what="test", retries=1) is None
        assert judge.calls == 2


class TestClientResolution:
    """``MemorySettings.judge`` as the client reads it."""

    @staticmethod
    def resolve(judge: Any, llm: Any = None) -> Any:
        from neo4j_agent_memory import MemoryClient

        client = types.SimpleNamespace(_settings=types.SimpleNamespace(judge=judge))
        return MemoryClient._resolve_judge(client, llm)  # type: ignore[arg-type]

    def test_no_judge_by_default(self) -> None:
        assert self.resolve(None) is None
        assert self.resolve("") is None

    def test_typesafe_by_name_with_an_optional_model(
        self, typesafe_sdk: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        assert self.resolve("typesafe").name == "jev-1.14.0"
        assert self.resolve("typesafe/jev-latest").name == "jev-latest"

    def test_the_llm_as_judge(self) -> None:
        llm = FakeStructuredLLM({})
        assert isinstance(self.resolve("llm", llm), LLMJudge)
        with pytest.raises(ValueError, match="needs MemorySettings.llm"):
            self.resolve("llm", None)

    def test_an_instance_is_used_as_it_is(self) -> None:
        judge = FlakyJudge(failures=0)
        assert self.resolve(judge) is judge

    def test_anything_else_is_refused(self) -> None:
        with pytest.raises(ValueError, match="Unknown judge"):
            self.resolve("oracle")
        with pytest.raises(ValueError, match="Judge instance or a string"):
            self.resolve(42)


class NoCredits(Exception):
    status = 402


class TestUnavailable:
    @pytest.mark.asyncio
    async def test_an_account_without_credits_is_unavailable(self, typesafe_sdk: None) -> None:
        judge = TypeSafeJudge(api_key="k")

        async def refuse(**_: Any) -> Any:
            raise NoCredits("no available credits")

        judge._client.system_one = refuse  # type: ignore[method-assign]
        with pytest.raises(JudgeUnavailable, match="no available credits"):
            await judge.judge({}, QUESTIONS)

    @pytest.mark.asyncio
    async def test_unavailable_is_raised_at_once(self) -> None:
        class Unavailable:
            name = "down"
            calls = 0

            async def judge(self, state: Any, questions: Any) -> dict[str, Judgement]:
                self.calls += 1
                raise JudgeUnavailable("no credits")

        judge = Unavailable()
        with pytest.raises(JudgeUnavailable):
            await judge_safely(judge, {}, {"q": YesNo("?")}, what="test")
        assert judge.calls == 1  # not retried
        assert await judge_safely(judge, {}, {"q": YesNo("?")}, what="test", degrade=True) is None
