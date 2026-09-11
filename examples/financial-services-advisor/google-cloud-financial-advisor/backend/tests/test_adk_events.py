"""Tests for the shared ADK event consumer.

The fake events below are built from the **real** ADK ``Event`` model, so if a
future ADK release changes the event surface these tests fail rather than the
app quietly reporting ``agents_consulted: []``.
"""

from __future__ import annotations

import pytest
from google.adk.events import Event, EventActions
from google.genai import types

from src.services.adk_events import collect_run, consume_run, truncate_result


class _FakeRunner:
    """Minimal stand-in for ``google.adk.runners.Runner``."""

    def __init__(self, events: list[Event]) -> None:
        self._events = events
        self.closed = False

    async def run_async(self, **_kwargs):
        for event in self._events:
            yield event

    async def close(self) -> None:
        self.closed = True


def _text_event(author: str, text: str) -> Event:
    return Event(
        author=author,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


def _call_event(author: str, name: str, args: dict) -> Event:
    return Event(
        author=author,
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
        ),
    )


def _response_event(author: str, name: str, response: dict) -> Event:
    return Event(
        author=author,
        content=types.Content(
            role="user",
            parts=[
                types.Part(function_response=types.FunctionResponse(name=name, response=response))
            ],
        ),
    )


def test_event_model_has_no_tool_calls_or_agent_name() -> None:
    """Pins the bug the old loops had: these attributes do not exist."""
    assert "tool_calls" not in Event.model_fields
    assert "agent_name" not in Event.model_fields
    assert "author" in Event.model_fields


async def test_collect_run_reports_agents_and_tool_calls() -> None:
    runner = _FakeRunner(
        [
            _text_event("financial_advisor_supervisor", "Delegating to KYC."),
            Event(
                author="financial_advisor_supervisor",
                actions=EventActions(transfer_to_agent="kyc_agent"),
            ),
            _call_event("kyc_agent", "verify_identity", {"customer_id": "CUST-001"}),
            _response_event("kyc_agent", "verify_identity", {"verified": True}),
            _text_event("kyc_agent", "Identity verified."),
        ]
    )

    summary = await collect_run(runner, user_id="user", session_id="s1", new_message=None)

    assert summary.agents_consulted == ["financial_advisor_supervisor", "kyc_agent"]
    assert [call.tool for call in summary.tool_calls] == ["verify_identity"]
    assert summary.tool_calls[0].args == {"customer_id": "CUST-001"}
    assert summary.tool_calls[0].agent == "kyc_agent"
    assert "Identity verified." in summary.response_text
    assert summary.step_count == 2


async def test_internal_transfer_functions_are_not_tool_calls() -> None:
    runner = _FakeRunner(
        [
            _call_event(
                "financial_advisor_supervisor",
                "transfer_to_agent",
                {"agent_name": "aml_agent"},
            ),
            _response_event("financial_advisor_supervisor", "transfer_to_agent", {"ok": True}),
        ]
    )
    summary = await collect_run(runner, user_id="user", session_id="s1", new_message=None)
    assert summary.tool_calls == []


async def test_consume_run_emits_delegation_events() -> None:
    runner = _FakeRunner(
        [
            Event(
                author="financial_advisor_supervisor",
                actions=EventActions(transfer_to_agent="aml_agent"),
            ),
        ]
    )
    kinds = [
        event.kind
        async for event in consume_run(runner, user_id="user", session_id="s1", new_message=None)
    ]
    assert "agent_delegate" in kinds
    assert kinds[0] == "agent_start"
    assert kinds[-1] == "agent_complete"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("hello", "hello"),
        ([1, 2, 3], "[1, 2, 3]"),
    ],
)
def test_truncate_result_shapes(value, expected) -> None:
    assert truncate_result(value) == expected


def test_truncate_result_serialises_dicts() -> None:
    rendered = truncate_result({"key": "value"})
    assert isinstance(rendered, str)
    assert "key" in rendered and "value" in rendered


def test_truncate_result_truncates() -> None:
    rendered = truncate_result("x" * 600, max_len=500)
    assert rendered is not None
    assert len(rendered) == 503
    assert rendered.endswith("...")
