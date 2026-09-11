"""Tests for the audit-grade reasoning-trace writer."""

from __future__ import annotations

from src.services.adk_events import RunEvent
from src.services.trace_writer import TraceWriter, entity_refs


def test_entity_refs_from_tool_arguments() -> None:
    refs = entity_refs({"customer_id": "CUST-003", "days": 90})
    assert [(ref.name, ref.type) for ref in refs] == [("CUST-003", "PERSON")]


def test_entity_refs_handles_several_argument_names() -> None:
    refs = entity_refs({"customer_id": "CUST-001", "entity_name": "Global Holdings Ltd"})
    assert {ref.name for ref in refs} == {"CUST-001", "Global Holdings Ltd"}


def test_entity_refs_ignores_non_strings_and_blanks() -> None:
    assert entity_refs({"customer_id": None, "entity_id": "   ", "transaction_id": 7}) == []


async def test_trace_is_linked_to_the_triggering_message(fake_memory_service) -> None:
    writer = TraceWriter(fake_memory_service, "session-1")
    await writer.start("Investigate CUST-003", triggered_by_message_id="msg-1")

    kwargs = fake_memory_service.client.reasoning.start_trace.await_args.kwargs
    assert kwargs["triggered_by_message_id"] == "msg-1"
    assert kwargs["session_id"] == "session-1"
    assert writer.trace_id is not None


async def test_tool_calls_record_touched_entities(fake_memory_service) -> None:
    writer = TraceWriter(fake_memory_service, "session-1")
    await writer.start("Investigate CUST-003")
    await writer.handle(RunEvent("agent_start", 0.0, agent="kyc_agent"))
    await writer.handle(
        RunEvent(
            "tool_result",
            0.0,
            agent="kyc_agent",
            tool="verify_identity",
            args={"customer_id": "CUST-003"},
            result={"verified": False},
        )
    )

    kwargs = fake_memory_service.client.reasoning.record_tool_call.await_args.kwargs
    assert kwargs["tool_name"] == "verify_identity"
    assert [ref.name for ref in kwargs["touched_entities"]] == ["CUST-003"]
    assert writer.step_count == 1
    assert writer.tool_call_count == 1


async def test_completion_uses_a_structured_outcome(fake_memory_service) -> None:
    writer = TraceWriter(fake_memory_service, "session-1")
    await writer.start("Investigate CUST-003")
    await writer.handle(RunEvent("agent_start", 0.0, agent="kyc_agent"))
    await writer.complete(summary="All clear", success=True, duration_ms=1234)

    outcome = fake_memory_service.client.reasoning.complete_trace.await_args.kwargs["outcome"]
    assert outcome.success is True
    assert outcome.summary == "All clear"
    assert outcome.metrics["duration_ms"] == 1234.0
    assert outcome.metrics["steps"] == 1.0


async def test_failures_are_recorded_with_an_error_kind(fake_memory_service) -> None:
    writer = TraceWriter(fake_memory_service, "session-1")
    await writer.start("Investigate CUST-003")
    await writer.complete(summary="boom", success=False, duration_ms=5, error_kind="TimeoutError")

    outcome = fake_memory_service.client.reasoning.complete_trace.await_args.kwargs["outcome"]
    assert outcome.success is False
    assert outcome.error_kind == "TimeoutError"


async def test_a_neo4j_failure_does_not_propagate(fake_memory_service) -> None:
    """The audit trail may degrade; the user's response must not break."""
    fake_memory_service.client.reasoning.start_trace.side_effect = RuntimeError("no db")
    writer = TraceWriter(fake_memory_service, "session-1")
    await writer.start("Investigate CUST-003")
    assert writer.trace_id is None
    # Subsequent calls are no-ops rather than errors.
    await writer.handle(RunEvent("agent_start", 0.0, agent="kyc_agent"))
    await writer.complete(summary="x", success=True, duration_ms=1)
