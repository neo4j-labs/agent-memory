"""Offline execution of maintained framework recipes against real adapter classes."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest

from neo4j_agent_memory.memory.long_term import LongTermMemory
from neo4j_agent_memory.memory.reasoning import ReasoningMemory, ReasoningStep, ReasoningTrace
from neo4j_agent_memory.memory.short_term import Message, ShortTermMemory

RECIPES = Path(__file__).resolve().parents[2] / "docs/modules/ROOT/examples/integrations"


def load(name):
    spec = importlib.util.spec_from_file_location(name, RECIPES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = load("common")
crew = load("crewai_recipe")
pydantic_recipe = load("pydantic_ai_recipe")
llama = load("llamaindex_recipe")
langchain_recipe = load("langchain_recipe")
openai_recipe = load("openai_agents_recipe")
adk = load("google_adk_recipe")
hybrid = load("hybrid_recipe")
cloud = load("cloud_embeddings_recipe")


class Client:
    def __init__(self):
        self.messages = []
        self.traces = {}
        self.short_term = create_autospec(ShortTermMemory, instance=True)
        self.long_term = create_autospec(LongTermMemory, instance=True)
        self.reasoning = create_autospec(ReasoningMemory, instance=True)
        self.get_context = AsyncMock(return_value="Stored project background")
        self.backend = "bolt"
        self.is_nams = False
        self.short_term.add_message.side_effect = self.add_message
        self.short_term.get_conversation.side_effect = self.conversation
        self.short_term.search_messages.side_effect = self.search
        self.long_term.search_entities.return_value = []
        self.long_term.search_preferences.return_value = []
        self.reasoning.start_trace.side_effect = self.start_trace
        self.reasoning.complete_trace.side_effect = self.complete_trace
        self.reasoning.get_trace_with_steps.side_effect = self.read_trace
        self.reasoning.get_trace.side_effect = self.read_trace
        self.reasoning.add_step.side_effect = self.add_step

    async def add_message(self, session_id, role, content, **kwargs):
        message = Message(role=role, content=content, metadata=kwargs.get("metadata") or {})
        self.messages.append((session_id, message))
        return message

    async def conversation(self, session_id, **kwargs):
        return SimpleNamespace(messages=[m for sid, m in self.messages if sid == session_id])

    async def search(self, query, **kwargs):
        scope = kwargs.get("session_id")
        return [m for sid, m in self.messages if scope in (None, sid)]

    async def start_trace(self, session_id, task, **kwargs):
        trace = ReasoningTrace(session_id=session_id, task=task)
        self.traces[trace.id] = trace
        return trace

    async def complete_trace(self, trace_id, success, outcome, **kwargs):
        self.traces[trace_id].success = success
        self.traces[trace_id].outcome = outcome
        return self.traces[trace_id]

    async def read_trace(self, trace_id, **kwargs):
        return self.traces.get(trace_id)

    async def add_step(self, trace_id, **kwargs):
        trace = self.traces[trace_id]
        step = ReasoningStep(trace_id=trace_id, step_number=len(trace.steps) + 1, **kwargs)
        trace.steps.append(step)
        return step


def test_crewai_boundary_passes_context_and_verifies_actual_public_readback():
    client = Client()
    threads = []
    import threading

    main_thread = threading.get_ident()

    def kickoff(context):
        threads.append(threading.get_ident())
        assert context == "Stored project background"
        return "Checklist ready"

    assert asyncio.run(crew.exercise(client, kickoff)) == "Checklist ready"
    assert threads != [main_thread]
    assert [m.role.value for _, m in client.messages] == ["user", "assistant"]
    assert len(client.traces) == 1
    assert all(trace.success is True for trace in client.traces.values())


def test_crewai_failure_is_recorded_without_assistant_reply():
    client = Client()

    def fail(context):
        raise RuntimeError("framework failed")

    with pytest.raises(RuntimeError, match="framework failed"):
        asyncio.run(crew.exercise(client, fail))
    assert [m.role.value for _, m in client.messages] == ["user"]
    assert len(client.traces) == 1
    assert all(trace.success is False for trace in client.traces.values())


def test_missing_readback_cannot_produce_successful_task_trace():
    client = Client()
    client.short_term.get_conversation.side_effect = None
    client.short_term.get_conversation.return_value = SimpleNamespace(messages=[])
    with pytest.raises(RuntimeError, match="readback failed"):
        asyncio.run(crew.exercise(client, lambda _context: "Checklist"))
    assert len(client.traces) == 1
    assert all(trace.success is False for trace in client.traces.values())


def test_pydantic_real_agent_and_dependency_complete_recipe():
    from pydantic_ai.models.test import TestModel

    client = Client()
    result = asyncio.run(pydantic_recipe.exercise(client, TestModel(call_tools=[])))
    assert result
    assert len(client.messages) == 2
    assert len(client.traces) == 1
    assert all(trace.success is True for trace in client.traces.values())


def test_llamaindex_real_memory_adapter_round_trips_chat_messages():
    client = Client()
    messages = asyncio.run(llama.exercise(client))
    assert len(messages) == 2
    assert "cedar-lantern-47" in messages[0].content
    client.short_term.get_conversation.assert_awaited_once()


def test_llamaindex_storage_failure_propagates():
    client = Client()
    client.short_term.add_message.side_effect = RuntimeError("database failed")
    with pytest.raises(RuntimeError, match="database failed"):
        asyncio.run(llama.exercise(client))


@pytest.mark.parametrize("fail", [False, True])
def test_openai_recipe_uses_actual_adapter_and_correct_task_outcome(fail):
    client = Client()

    async def run(prompt, context):
        assert context == "Stored project background"
        if fail:
            raise RuntimeError("provider failed")
        return "Checklist ready"

    if fail:
        with pytest.raises(RuntimeError, match="provider failed"):
            asyncio.run(openai_recipe.exercise(client, run))
    else:
        assert asyncio.run(openai_recipe.exercise(client, run)) == "Checklist ready"
    assert len(client.traces) == 1
    assert all(trace.success is not fail for trace in client.traces.values())
    assert len(client.messages) == (1 if fail else 2)


def test_openai_main_registers_recall_tool_and_runs_agent(monkeypatch):
    seen = {}

    def function_tool(fn):
        seen["tool"] = fn
        return SimpleNamespace(name=fn.__name__, fn=fn)

    class Agent:
        def __init__(self, *, name, model, instructions, tools):
            self.name, self.model, self.tools = name, model, tools
            seen["agent"] = self

    class Runner:
        @staticmethod
        async def run(agent, prompt):
            assert agent is seen["agent"]
            assert "checklist" in prompt
            assert [tool.name for tool in agent.tools] == ["recall_context"]
            assert await seen["tool"]("checklist") == "Stored project background"
            return SimpleNamespace(final_output="Checklist ready")

    agents = ModuleType("agents")
    agents.Agent, agents.Runner, agents.function_tool = Agent, Runner, function_tool
    monkeypatch.setitem(sys.modules, "agents", agents)
    monkeypatch.setenv("OPENAI_MODEL", "fixture-model")
    client = Client()

    class MemoryClient:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return client

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("neo4j_agent_memory.MemoryClient", MemoryClient)
    monkeypatch.setattr(openai_recipe, "settings", lambda: None)
    asyncio.run(openai_recipe.main())
    assert seen["agent"].model == "fixture-model"
    assert len(client.messages) == 2
    assert len(client.traces) == 1
    assert all(trace.success is True for trace in client.traces.values())


@pytest.mark.parametrize("fail", [False, True])
def test_langchain_actual_middleware_success_and_model_failure(fail):
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, *args, **kwargs):
            if fail:
                raise RuntimeError("provider failed")
            return super()._generate(*args, **kwargs)

    client = Client()
    model = Model(messages=iter([AIMessage(content="Checklist ready")]))
    if fail:
        with pytest.raises(RuntimeError, match="provider failed"):
            asyncio.run(langchain_recipe.exercise(client, model))
    else:
        assert asyncio.run(langchain_recipe.exercise(client, model)) == "Checklist ready"
    assert len(client.messages) == (1 if fail else 2)


@pytest.mark.parametrize("fail", [False, True])
def test_adk_real_session_and_memory_service_with_stubbed_runner(monkeypatch, fail):
    from google.adk.events import Event
    from google.adk.runners import Runner
    from google.genai import types

    monkeypatch.setenv("ADK_MODEL", "gemini-2.5-flash")

    async def run_async(self, *, user_id, session_id, new_message, **kwargs):
        session = await self.session_service.get_session(
            app_name=self.app_name, user_id=user_id, session_id=session_id
        )
        event = Event(author="user", content=new_message)
        await self.session_service.append_event(session, event)
        yield event
        if fail:
            yield Event(author="checklist_agent", error_code="fixture-failure")
        else:
            event = Event(
                author="checklist_agent",
                content=types.Content(
                    role="model", parts=[types.Part(text="Project code recorded.")]
                ),
            )
            await self.session_service.append_event(session, event)
            yield event

    monkeypatch.setattr(Runner, "run_async", run_async)
    client = Client()
    if fail:
        with pytest.raises(RuntimeError, match="fixture-failure"):
            asyncio.run(adk.exercise(client))
        assert not client.messages
    else:
        session = asyncio.run(adk.exercise(client))
        assert len(client.messages) == 2
        assert all(sid == session for sid, _ in client.messages)
        assert client.messages[1][1].metadata["adk_author"] == "checklist_agent"


def test_hybrid_actual_provider_routes_message_kind_not_nonexistent_memory_type():
    client = Client()
    result = asyncio.run(hybrid.exercise(client))
    assert result.filters_applied["memory_types_searched"] == ["message"]
    client.long_term.search_entities.assert_not_awaited()
    client.long_term.search_preferences.assert_not_awaited()
    assert len(client.messages) == 1


def test_hybrid_empty_readback_is_not_verified_as_persistence():
    client = Client()
    client.short_term.get_conversation.side_effect = RuntimeError("database failed")
    with pytest.raises(RuntimeError, match="readback failed"):
        asyncio.run(hybrid.exercise(client))


def test_bedrock_actual_adapter_emits_validated_batch_dimensions(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    calls = []

    def invoke_model(**kwargs):
        calls.append(json.loads(kwargs["body"]))
        return {"body": io.BytesIO(json.dumps({"embedding": [0.1] * 1024}).encode())}

    embedder = cloud.build_embedder("bedrock")
    assert embedder.dimensions == 1024
    embedder._ensure_underlying()._client = SimpleNamespace(invoke_model=invoke_model)
    asyncio.run(cloud.verify_vectors(embedder))
    assert len(calls) == 2
    assert all("inputText" in call for call in calls)


def test_vertex_recipe_selects_exact_dimension_without_network(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fixture-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    assert cloud.build_embedder("vertex").dimensions == 768


@pytest.mark.parametrize(("provider", "dimensions"), [("bedrock", 1024), ("vertex", 768)])
def test_common_settings_accepts_each_cloud_embedder(monkeypatch, tmp_path, provider, dimensions):
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "NEO4J_URI": "neo4j+s://tutorial.databases.neo4j.io",
        "NEO4J_USERNAME": "tutorial-user",
        "NEO4J_PASSWORD": "synthetic-tutorial-password",
        "AWS_REGION": "us-east-1",
        "GOOGLE_CLOUD_PROJECT": "fixture-project",
        "GOOGLE_CLOUD_LOCATION": "us-central1",
    }.items():
        monkeypatch.setenv(key, value)
    embedder = cloud.build_embedder(provider)
    config = common.settings(embedding=embedder)
    assert config.embedding is embedder
    assert config.embedding.dimensions == dimensions


def test_cloud_dimension_mismatch_stops_before_database_setup():
    embedder = SimpleNamespace(dimensions=768, embed=AsyncMock(return_value=[[0.1], [0.1]]))
    with pytest.raises(RuntimeError, match="dimensions"):
        asyncio.run(cloud.verify_vectors(embedder))


@pytest.mark.parametrize("path", sorted(RECIPES.glob("*.py")), ids=lambda path: path.name)
def test_maintained_integration_program_compiles(path):
    compile(path.read_text(), str(path), "exec")


def test_common_settings_constructs_an_embedding_provider_from_the_real_factory(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "NEO4J_URI": "neo4j+s://tutorial.databases.neo4j.io",
            "NEO4J_USERNAME": "tutorial-user",
            "NEO4J_PASSWORD": "synthetic-tutorial-password",
            "NEO4J_DATABASE": "tutorial-database",
            "OPENAI_API_KEY": "fixture-key",
        },
    )
    config = common.settings()
    assert config.backend == "bolt"
    assert config.neo4j.uri == "neo4j+s://tutorial.databases.neo4j.io"
    assert config.neo4j.username == "tutorial-user"
    assert config.neo4j.password.get_secret_value() == "synthetic-tutorial-password"
    assert config.neo4j.database == "tutorial-database"
    assert config.embedding.dimensions == 1536


@pytest.mark.parametrize("missing", ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"])
def test_common_settings_requires_explicit_aura_credentials_before_provider_setup(
    monkeypatch, tmp_path, missing
):
    monkeypatch.chdir(tmp_path)
    exports = {
        "NEO4J_URI": "neo4j+s://tutorial.databases.neo4j.io",
        "NEO4J_USERNAME": "tutorial-user",
        "NEO4J_PASSWORD": "synthetic-tutorial-password",
    }
    exports.pop(missing)
    monkeypatch.setattr(os, "environ", exports)

    def unexpected_provider(*_args, **_kwargs):
        raise AssertionError("Missing database credentials must stop before provider setup")

    monkeypatch.setattr("neo4j_agent_memory.llm.from_provider", unexpected_provider)
    with pytest.raises(KeyError, match=missing):
        common.settings()


def test_microsoft_selected_client_and_agent_constructor_match_current_package(monkeypatch):
    from agent_framework.openai import OpenAIChatClient

    from neo4j_agent_memory.integrations.microsoft_agent import Neo4jMicrosoftMemory

    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    memory = Neo4jMicrosoftMemory(Client(), "fixture-session", extract_entities=False)
    client = OpenAIChatClient(model="fixture-model", api_key="fixture-key")
    agent = client.as_agent(name="Fixture", context_providers=[memory.context_provider])
    assert agent.name == "Fixture"


def test_crewai_selected_constructor_recipe_uses_current_framework(monkeypatch, tmp_path):
    monkeypatch.setenv("CREWAI_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("CREWAI_DISABLE_TELEMETRY", "true")
    from crewai import Crew

    monkeypatch.setenv("CREWAI_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")

    def kickoff(self, *args, **kwargs):
        assert len(self.tasks) == 1
        assert "stored fixture context" in self.tasks[0].description
        assert self.memory is False
        return "Checklist ready"

    monkeypatch.setattr(Crew, "kickoff", kickoff)
    assert crew.run_crew("stored fixture context") == "Checklist ready"
