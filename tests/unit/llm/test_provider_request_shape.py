"""Request-shape tests for the native OpenAI and Anthropic adapters.

These tests exist because the two SDKs are *generated*: their
``create()`` methods take explicit keyword parameters and no ``**kwargs``
catch-all, so a request keyword the installed major no longer declares is a
``TypeError`` at call time rather than a server-side error. The adapters build
their request as a ``dict`` and splat it, which a mocked client happily
accepts — so a mock alone cannot catch that class of drift.

Each test therefore does two things:

1. drives the adapter against a fake client that records the request, and
2. asserts every recorded keyword is a parameter the *installed* SDK's
   ``create()`` actually declares.

The notable assertion is the absence of ``temperature`` on the Anthropic path:
the anthropic 1.x line removed ``temperature`` / ``top_p`` / ``top_k`` from
``messages.create()`` (mirroring the API, which rejects sampling parameters on
current Claude models), so the adapter accepts the protocol's ``temperature``
argument without forwarding it.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import BaseModel

from neo4j_agent_memory.llm.types import ChatMessage


class _Extraction(BaseModel):
    value: str


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Recorder:
    """Collects the kwargs each fake ``create()`` call received."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def last(self) -> dict[str, Any]:
        assert self.calls, "expected the adapter to issue a request"
        return self.calls[-1]


class _Block:
    def __init__(self, type_: str, **fields: Any) -> None:
        self.type = type_
        for key, value in fields.items():
            setattr(self, key, value)


class _AnthropicUsage:
    input_tokens = 11
    output_tokens = 7
    cache_read_input_tokens = 0


class _AnthropicResponse:
    def __init__(self, content: list[_Block]) -> None:
        self.content = content
        self.model = "claude-opus-5"
        self.usage = _AnthropicUsage()
        self.stop_reason = "end_turn"

    def model_dump(self) -> dict[str, Any]:
        return {}


class _FakeAnthropicMessages:
    def __init__(self, recorder: _Recorder, content: list[_Block]) -> None:
        self._recorder = recorder
        self._content = content

    async def create(self, **kwargs: Any) -> _AnthropicResponse:
        self._recorder.calls.append(kwargs)
        return _AnthropicResponse(self._content)


class _FakeAnthropicClient:
    def __init__(self, recorder: _Recorder, content: list[_Block]) -> None:
        self.messages = _FakeAnthropicMessages(recorder, content)


class _OpenAIUsage:
    prompt_tokens = 5
    completion_tokens = 3
    total_tokens = 8
    prompt_tokens_details = None


class _OpenAIChoice:
    def __init__(self, content: str) -> None:
        self.message = _Block("message", content=content)
        self.finish_reason = "stop"


class _OpenAIResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_OpenAIChoice(content)]
        self.model = "gpt-5-mini"
        self.usage = _OpenAIUsage()

    def model_dump(self) -> dict[str, Any]:
        return {}


class _FakeOpenAICompletions:
    def __init__(self, recorder: _Recorder, content: str) -> None:
        self._recorder = recorder
        self._content = content

    async def create(self, **kwargs: Any) -> _OpenAIResponse:
        self._recorder.calls.append(kwargs)
        return _OpenAIResponse(self._content)


class _FakeOpenAIEmbeddings:
    def __init__(self, recorder: _Recorder, dimensions: int) -> None:
        self._recorder = recorder
        self._dimensions = dimensions

    async def create(self, **kwargs: Any) -> Any:
        self._recorder.calls.append(kwargs)
        inputs = kwargs["input"]
        data = [
            _Block("embedding", index=i, embedding=[0.1] * self._dimensions)
            for i in range(len(inputs))
        ]
        return _Block("response", data=data)


class _FakeOpenAIClient:
    def __init__(self, recorder: _Recorder, *, content: str = "hi", dimensions: int = 1536) -> None:
        self.chat = _Block("chat", completions=_FakeOpenAICompletions(recorder, content))
        self.embeddings = _FakeOpenAIEmbeddings(recorder, dimensions)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accepted_parameters(method: Any) -> set[str]:
    """Parameter names the installed SDK method declares."""
    parameters = inspect.signature(method).parameters
    return {name for name in parameters if name != "self"}


def _assert_kwargs_accepted(sent: dict[str, Any], method: Any) -> None:
    accepted = _accepted_parameters(method)
    unknown = sorted(set(sent) - accepted)
    assert not unknown, (
        f"adapter sent keyword(s) the installed SDK rejects: {unknown}; "
        f"accepted: {sorted(accepted)}"
    )


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


async def test_anthropic_complete_omits_sampling_parameters() -> None:
    from neo4j_agent_memory.llm.adapters.anthropic import AnthropicProvider

    recorder = _Recorder()
    provider = AnthropicProvider("anthropic/claude-opus-5", api_key="x")
    provider._client = _FakeAnthropicClient(recorder, [_Block("text", text="hello")])  # type: ignore[assignment]

    completion = await provider.complete(
        [ChatMessage(role="system", content="be terse"), ChatMessage(role="user", content="hi")],
        temperature=0.7,
        max_tokens=64,
        stop=["END"],
    )

    assert completion.content == "hello"
    sent = recorder.last
    assert "temperature" not in sent
    assert "top_p" not in sent
    assert "top_k" not in sent
    # The parameters that *are* still supported must survive.
    assert sent["max_tokens"] == 64
    assert sent["stop_sequences"] == ["END"]
    assert sent["system"] == "be terse"
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


async def test_anthropic_structured_omits_sampling_parameters() -> None:
    from neo4j_agent_memory.llm.adapters.anthropic import AnthropicProvider

    recorder = _Recorder()
    provider = AnthropicProvider("anthropic/claude-opus-5", api_key="x")
    provider._client = _FakeAnthropicClient(  # type: ignore[assignment]
        recorder,
        [_Block("tool_use", input={"value": "ok"}, name="submit_extraction")],
    )

    result = await provider.complete_structured(
        [ChatMessage(role="user", content="extract")],
        _Extraction,
        temperature=0.3,
    )

    assert result.value == "ok"
    sent = recorder.last
    assert "temperature" not in sent
    assert sent["tool_choice"] == {"type": "tool", "name": "submit_extraction"}


async def test_anthropic_request_keywords_exist_in_installed_sdk() -> None:
    anthropic = pytest.importorskip("anthropic")
    from neo4j_agent_memory.llm.adapters.anthropic import AnthropicProvider

    recorder = _Recorder()
    provider = AnthropicProvider("anthropic/claude-opus-5", api_key="x", cache_system=True)
    provider._client = _FakeAnthropicClient(recorder, [_Block("text", text="hello")])  # type: ignore[assignment]

    await provider.complete(
        [ChatMessage(role="system", content="s"), ChatMessage(role="user", content="u")],
        stop=["END"],
        timeout=5.0,
    )

    _assert_kwargs_accepted(recorder.last, anthropic.AsyncAnthropic(api_key="x").messages.create)


def test_anthropic_temperature_warning_is_emitted_once(caplog: pytest.LogCaptureFixture) -> None:
    from neo4j_agent_memory.llm.adapters import anthropic as adapter

    adapter._sampling_warning_emitted = False
    try:
        with caplog.at_level("WARNING", logger=adapter.__name__):
            adapter._note_dropped_temperature(0.0)
            assert not caplog.records  # the default value is not worth a warning
            adapter._note_dropped_temperature(0.9)
            adapter._note_dropped_temperature(0.9)
        assert len(caplog.records) == 1
        assert "sampling parameters" in caplog.records[0].message
    finally:
        adapter._sampling_warning_emitted = False


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


async def test_openai_request_keywords_exist_in_installed_sdk() -> None:
    openai = pytest.importorskip("openai")
    from neo4j_agent_memory.llm.adapters.openai import OpenAIProvider

    recorder = _Recorder()
    provider = OpenAIProvider("openai/gpt-5-mini", api_key="x")
    provider._client = _FakeOpenAIClient(recorder)  # type: ignore[assignment]

    client = openai.AsyncOpenAI(api_key="x")

    await provider.complete(
        [ChatMessage(role="user", content="hi")],
        temperature=0.2,
        max_tokens=32,
        stop=["END"],
        timeout=5.0,
    )
    _assert_kwargs_accepted(recorder.last, client.chat.completions.create)

    structured_recorder = _Recorder()
    structured = OpenAIProvider("openai/gpt-5-mini", api_key="x")
    structured._client = _FakeOpenAIClient(  # type: ignore[assignment]
        structured_recorder, content='{"value": "ok"}'
    )
    extraction = await structured.complete_structured(
        [ChatMessage(role="user", content="extract")],
        _Extraction,
        temperature=0.0,
    )
    assert extraction.value == "ok"
    sent = structured_recorder.last
    _assert_kwargs_accepted(sent, client.chat.completions.create)
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True


async def test_openai_embedding_request_keywords_exist_in_installed_sdk() -> None:
    openai = pytest.importorskip("openai")
    from neo4j_agent_memory.llm.adapters.openai import OpenAIEmbeddingProvider

    recorder = _Recorder()
    provider = OpenAIEmbeddingProvider("openai/text-embedding-3-small", dimensions=256)
    provider._client = _FakeOpenAIClient(recorder, dimensions=256)  # type: ignore[assignment]

    vectors = await provider.embed(["a", "b"])
    assert [len(v) for v in vectors] == [256, 256]

    sent = recorder.last
    assert sent["dimensions"] == 256
    _assert_kwargs_accepted(sent, openai.AsyncOpenAI(api_key="x").embeddings.create)
