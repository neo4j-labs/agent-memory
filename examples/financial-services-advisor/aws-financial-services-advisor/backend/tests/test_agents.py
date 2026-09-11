"""Tests for bind_tool utility and agent wiring."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
from unittest.mock import MagicMock

import pytest

from src.tools import bind_tool


class TestBindTool:
    """Test the bind_tool utility that hides neo4j_service from LLM signatures."""

    def test_bind_tool_removes_neo4j_service_from_signature(self):
        async def my_tool(customer_id: str, *, neo4j_service) -> dict:
            return {"id": customer_id}

        bound = bind_tool(my_tool, neo4j_service=MagicMock())
        sig = inspect.signature(bound)
        param_names = list(sig.parameters.keys())
        assert "customer_id" in param_names
        assert "neo4j_service" not in param_names

    def test_bind_tool_preserves_function_name(self):
        async def verify_identity(customer_id: str, *, neo4j_service) -> dict:
            return {}

        bound = bind_tool(verify_identity, neo4j_service=MagicMock())
        assert bound.__name__ == "verify_identity"

    def test_bind_tool_preserves_docstring(self):
        async def verify_identity(customer_id: str, *, neo4j_service) -> dict:
            """Verify customer identity."""
            return {}

        bound = bind_tool(verify_identity, neo4j_service=MagicMock())
        assert "Verify customer identity" in bound.__doc__

    @pytest.mark.asyncio
    async def test_bind_tool_injects_neo4j_service(self):
        captured = {}

        async def my_tool(customer_id: str, *, neo4j_service) -> dict:
            captured["neo4j_service"] = neo4j_service
            return {"id": customer_id}

        mock_svc = MagicMock()
        bound = bind_tool(my_tool, neo4j_service=mock_svc)
        result = await bound("CUST-001")
        assert captured["neo4j_service"] is mock_svc
        assert result["id"] == "CUST-001"

    @pytest.mark.asyncio
    async def test_bind_tool_with_multiple_params(self):
        async def my_tool(customer_id: str, days: int = 90, *, neo4j_service) -> dict:
            return {"id": customer_id, "days": days}

        bound = bind_tool(my_tool, neo4j_service=MagicMock())
        sig = inspect.signature(bound)

        # Should have customer_id and days, but not neo4j_service
        param_names = list(sig.parameters.keys())
        assert param_names == ["customer_id", "days"]

        result = await bound("CUST-001", days=30)
        assert result["days"] == 30

    def test_bind_tool_signature_has_correct_defaults(self):
        async def my_tool(customer_id: str, days: int = 90, *, neo4j_service) -> dict:
            return {}

        bound = bind_tool(my_tool, neo4j_service=MagicMock())
        sig = inspect.signature(bound)
        assert sig.parameters["days"].default == 90


class TestToolRegistration:
    """The tools must reach the Strands registry, not just have a nice signature.

    ``ToolRegistry.process_tools`` accepts strings, dicts, modules, iterables,
    ``ToolProvider``s and ``AgentTool`` instances. A plain function matches none
    of those: it is logged as an ``unrecognized tool specification`` and dropped,
    so every sub-agent silently runs with zero tools. A signature assertion
    cannot see that — only a registration assertion can.
    """

    def test_bound_tool_registers_under_its_own_name(self):
        from strands.tools.registry import ToolRegistry

        from src.tools.kyc_tools import verify_identity

        registered = ToolRegistry().process_tools([bind_tool(verify_identity, MagicMock())])
        assert registered == ["verify_identity"]

    def test_every_domain_tool_registers(self):
        from strands.tools.registry import ToolRegistry

        from src.tools.aml_tools import (
            analyze_velocity,
            detect_patterns,
            flag_suspicious_transaction,
            scan_transactions,
        )
        from src.tools.compliance_tools import (
            assess_regulatory_requirements,
            check_sanctions,
            generate_sar_report,
            verify_pep_status,
        )
        from src.tools.kyc_tools import (
            assess_customer_risk,
            check_adverse_media,
            check_documents,
            verify_identity,
        )
        from src.tools.relationship_tools import (
            analyze_network_risk,
            detect_shell_companies,
            find_connections,
            map_beneficial_ownership,
        )

        tools = [
            verify_identity,
            check_documents,
            assess_customer_risk,
            check_adverse_media,
            scan_transactions,
            detect_patterns,
            flag_suspicious_transaction,
            analyze_velocity,
            find_connections,
            analyze_network_risk,
            detect_shell_companies,
            map_beneficial_ownership,
            check_sanctions,
            verify_pep_status,
            generate_sar_report,
            assess_regulatory_requirements,
        ]
        service = MagicMock()
        registered = ToolRegistry().process_tools([bind_tool(t, service) for t in tools])
        assert sorted(registered) == sorted(t.__name__ for t in tools)

    def test_bound_tool_hides_collaborators_from_the_schema(self):
        from src.tools.compliance_tools import check_sanctions

        bound = bind_tool(check_sanctions, MagicMock(), memory_service=MagicMock())
        properties = bound.tool_spec["inputSchema"]["json"]["properties"]
        assert "neo4j_service" not in properties
        assert "memory_service" not in properties
        assert "entity_name" in properties
        # Docstring Args sections become parameter descriptions in the schema.
        assert properties["entity_name"]["description"]

    def test_bind_tool_only_binds_collaborators_the_tool_declares(self):
        captured: dict[str, object] = {}

        async def my_tool(customer_id: str, *, neo4j_service) -> dict:
            captured["neo4j_service"] = neo4j_service
            return {}

        service = MagicMock()
        # Passing memory_service to a tool that does not take one is a no-op,
        # so the supervisor can bind both collaborators uniformly.
        bound = bind_tool(my_tool, service, memory_service=MagicMock())
        assert "memory_service" not in inspect.signature(bound).parameters
        asyncio.run(bound("CUST-001"))
        assert captured["neo4j_service"] is service


class TestAgentWiring:
    """Test supervisor agent caching and configuration."""

    def test_reset_supervisor_agent_clears_the_cache(self):
        from src.agents.supervisor import _AGENT_CACHE, reset_supervisor_agent

        _AGENT_CACHE["sentinel-session"] = (MagicMock(), 0.0)
        reset_supervisor_agent()
        assert _AGENT_CACHE == {}

    def test_agents_are_cached_per_session(self, monkeypatch):
        """One agent per session: a shared instance merges transcripts and
        raises ``ConcurrencyException`` on the second concurrent request."""
        import src.agents.supervisor as supervisor_module

        built: list[str] = []

        def fake_create(neo4j_service, *, settings=None):
            built.append("built")
            return MagicMock()

        monkeypatch.setattr(supervisor_module, "create_supervisor_agent", fake_create)
        supervisor_module.reset_supervisor_agent()

        service = MagicMock()
        first = supervisor_module.get_supervisor_agent(service, "session-a")
        again = supervisor_module.get_supervisor_agent(service, "session-a")
        other = supervisor_module.get_supervisor_agent(service, "session-b")

        assert first is again
        assert other is not first
        assert len(built) == 2

    def test_agent_cache_is_bounded(self, monkeypatch):
        import src.agents.supervisor as supervisor_module

        monkeypatch.setattr(
            supervisor_module, "create_supervisor_agent", lambda *a, **k: MagicMock()
        )
        monkeypatch.setattr(supervisor_module, "_CACHE_MAX_SESSIONS", 3)
        supervisor_module.reset_supervisor_agent()

        for index in range(10):
            supervisor_module.get_supervisor_agent(MagicMock(), f"session-{index}")

        assert len(supervisor_module._AGENT_CACHE) == 3

    def test_supervisor_prompt_contains_key_terms(self):
        from src.agents.prompts import SUPERVISOR_SYSTEM_PROMPT

        prompt_lower = SUPERVISOR_SYSTEM_PROMPT.lower()
        assert any(
            term in prompt_lower
            for term in ["financial", "compliance", "delegate", "supervisor", "agent"]
        )

    def test_kyc_prompt_exists(self):
        from src.agents.prompts import KYC_AGENT_SYSTEM_PROMPT

        assert len(KYC_AGENT_SYSTEM_PROMPT) > 50

    def test_aml_prompt_exists(self):
        from src.agents.prompts import AML_AGENT_SYSTEM_PROMPT

        assert len(AML_AGENT_SYSTEM_PROMPT) > 50

    def test_relationship_prompt_exists(self):
        from src.agents.prompts import RELATIONSHIP_AGENT_SYSTEM_PROMPT

        assert len(RELATIONSHIP_AGENT_SYSTEM_PROMPT) > 50

    def test_compliance_prompt_exists(self):
        from src.agents.prompts import COMPLIANCE_AGENT_SYSTEM_PROMPT

        assert len(COMPLIANCE_AGENT_SYSTEM_PROMPT) > 50


class TestStreamingEventShape:
    """Pin the Strands event shapes ``/api/chat/stream`` maps onto the wire.

    The SSE route reads ``data`` deltas, ``current_tool_use`` (``toolUseId`` /
    ``name`` / ``input``), the ``toolResult`` blocks inside a ``message`` event,
    and the terminal ``result``. If a Strands upgrade renames any of those, the
    stream silently stops emitting tool events — this test fails instead.
    """

    @staticmethod
    def _stub_model():
        from strands.models import Model

        class StubModel(Model):
            """Two turns: one tool use, then a text answer."""

            def __init__(self) -> None:
                self.calls = 0

            def update_config(self, **kwargs):  # pragma: no cover - unused
                pass

            def get_config(self):  # pragma: no cover - unused
                return {}

            async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
                raise NotImplementedError
                yield  # pragma: no cover

            async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
                self.calls += 1
                yield {"messageStart": {"role": "assistant"}}
                if self.calls == 1:
                    yield {
                        "contentBlockStart": {
                            "start": {"toolUse": {"toolUseId": "tu-1", "name": "echo_customer"}}
                        }
                    }
                    yield {
                        "contentBlockDelta": {
                            "delta": {"toolUse": {"input": '{"customer_id": "CUST-003"}'}}
                        }
                    }
                    yield {"contentBlockStop": {}}
                    yield {"messageStop": {"stopReason": "tool_use"}}
                else:
                    yield {"contentBlockDelta": {"delta": {"text": "Risk level: CRITICAL."}}}
                    yield {"contentBlockStop": {}}
                    yield {"messageStop": {"stopReason": "end_turn"}}

        return StubModel()

    @pytest.mark.asyncio
    async def test_stream_async_event_keys(self):
        from strands import Agent

        async def echo_customer(customer_id: str, *, neo4j_service) -> dict:
            """Echo a customer id.

            Args:
                customer_id: Customer identifier
            """
            return {"customer_id": customer_id}

        agent = Agent(
            model=self._stub_model(),
            tools=[bind_tool(echo_customer, MagicMock())],
            system_prompt="test",
        )

        tool_uses: list[dict] = []
        tool_results: list[dict] = []
        deltas: list[str] = []
        final: str | None = None
        async for event in agent.stream_async("go"):
            if "data" in event:
                deltas.append(str(event["data"]))
            if event.get("current_tool_use"):
                # Strands mutates one dict in place across deltas, so snapshot
                # it; reading it after the loop would show only the final value.
                # The route snapshots the same way, via ``_tool_input``.
                tool_uses.append(copy.deepcopy(event["current_tool_use"]))
            for block in (event.get("message") or {}).get("content", []) or []:
                if block.get("toolResult"):
                    tool_results.append(block["toolResult"])
            if "result" in event:
                final = str(event["result"])

        assert tool_uses, "no current_tool_use events - the SSE tool_call mapping is broken"
        assert tool_uses[-1]["toolUseId"] == "tu-1"
        assert tool_uses[-1]["name"] == "echo_customer"
        # ``input`` accumulates as a JSON *string* while the tool-use block
        # streams, and is a parsed dict once the block closes — which is why the
        # route's ``_tool_input`` helper accepts both shapes.
        inputs = [use["input"] for use in tool_uses]
        assert any(isinstance(value, str) for value in inputs)
        parsed = [
            value if isinstance(value, dict) else json.loads(value)
            for value in inputs
            if value not in ("", None)
        ]
        assert {"customer_id": "CUST-003"} in parsed
        assert tool_results and tool_results[0]["toolUseId"] == "tu-1"
        assert deltas == ["Risk level: CRITICAL."]
        assert final is not None and "CRITICAL" in final
