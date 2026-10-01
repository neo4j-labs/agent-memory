"""Offline regressions for the documentation's actual maintained programs.

No credentials, service writes, provider calls, or model downloads are used.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import httpx
import pytest

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/modules/ROOT/examples"
sys.path.insert(0, str(EXAMPLES))


def load_fixture(name):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


helpers = load_fixture("hosted_tutorial_helpers")
skills = load_fixture("skills_quickstart")
nams = load_fixture("nams_quickstart")


def make_state(tmp_path, lesson="skills"):
    from neo4j_agent_memory import NamsSettings

    return skills.TutorialState.create(
        tmp_path / f"{lesson}.json",
        NamsSettings(
            nams={"endpoint": "https://service.invalid/v1", "api_key": "nams_fixture"},
            _env_file=None,
        ),
        lesson,
    )


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, duration):
        self.now += duration


def test_skill_poll_waits_through_queued_and_running():
    states = iter(
        [
            {"status": "queued"},
            {"status": "running"},
            {"status": "completed", "outcome": "Created", "skillId": "returned-skill"},
        ]
    )
    seen = []

    async def fetch():
        state = next(states)
        seen.append(state)
        return state

    clock = Clock()
    result = asyncio.run(helpers.poll_skill_run(fetch, clock=clock, sleep=clock.sleep))
    assert result["skillId"] == "returned-skill"
    assert len(seen) == 3


@pytest.mark.parametrize("outcome", ["Withheld", "Failed"])
def test_skill_terminal_outcomes_do_not_require_a_skill(outcome):
    async def fetch():
        return {"outcome": outcome}

    assert asyncio.run(helpers.poll_skill_run(fetch))["outcome"] == outcome


@pytest.mark.parametrize(
    "state",
    [
        {"status": "completed"},
        {"status": "cancelled"},
        {"outcome": "Created"},
        {"outcome": "Unexpected", "status": "running"},
    ],
)
def test_skill_unknown_or_incomplete_terminal_response_is_not_success(state):
    async def fetch():
        return state

    with pytest.raises(RuntimeError):
        asyncio.run(helpers.poll_skill_run(fetch))


def test_skill_poll_timeout_is_finite_while_queued():
    clock = Clock()
    count = 0

    async def fetch():
        nonlocal count
        count += 1
        return {"status": "queued"}

    with pytest.raises(TimeoutError):
        asyncio.run(
            helpers.poll_skill_run(fetch, timeout=5, interval=2, clock=clock, sleep=clock.sleep)
        )
    assert count == 3
    assert clock.now == 5


# Ontology restoration/recovery contracts live in test_ontology_tutorial.py.

HOSTED_PROGRAMS = [
    "nams_quickstart",
    "nams_first_write",
    "skills_quickstart",
    "hosted_tutorial_state",
    "hosted_tutorial_cleanup",
    "hosted_tutorial_helpers",
]


def hosted_calls():
    """Every ``client.<layer>.<method>(...)`` call in the hosted lesson programs."""
    import ast

    from neo4j_agent_memory.nams.long_term import NamsLongTermMemory
    from neo4j_agent_memory.nams.reasoning import NamsReasoningMemory
    from neo4j_agent_memory.nams.short_term import NamsShortTermMemory

    owners = {
        "short_term": NamsShortTermMemory,
        "long_term": NamsLongTermMemory,
        "reasoning": NamsReasoningMemory,
    }
    for name in HOSTED_PROGRAMS:
        for node in ast.walk(ast.parse((EXAMPLES / f"{name}.py").read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            if (
                isinstance(receiver, ast.Attribute)
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == "client"
                and receiver.attr in owners
            ):
                yield pytest.param(
                    owners[receiver.attr], node, id=f"{name}:{node.lineno}:{node.func.attr}"
                )


def test_hosted_programs_make_sdk_calls():
    """Guard the parametrization below against silently matching nothing."""
    assert {param.id.split(":")[0] for param in hosted_calls()} >= {
        "nams_quickstart",
        "skills_quickstart",
    }


@pytest.mark.parametrize("owner,node", list(hosted_calls()))
def test_hosted_calls_bind_to_current_nams_api(owner, node):
    """The fakes in this module accept any arguments, so bind each call to the real class."""
    import inspect

    method = getattr(owner, node.func.attr)  # A renamed or removed method fails here.
    assert not any(keyword.arg is None for keyword in node.keywords), "unpack not checked"
    inspect.signature(method).bind(
        None, *[None for _ in node.args], **{keyword.arg: None for keyword in node.keywords}
    )


class ShortTerm:
    def __init__(self):
        self.messages = []
        self.ids = []

    async def create_conversation(self, name, **kwargs):
        assert kwargs["metadata"]["tutorialRun"]
        return SimpleNamespace(id="server-conversation-id")

    async def bulk_add_messages(self, conversation_id, transcript):
        self.ids.append(conversation_id)
        self.messages = [
            SimpleNamespace(id=f"message-{index}", content=row["content"], role=row["role"])
            for index, row in enumerate(transcript)
        ]
        return self.messages

    async def get_conversation(self, conversation_id):
        self.ids.append(conversation_id)
        return SimpleNamespace(messages=self.messages)


class Reasoning:
    def __init__(self):
        self.steps = []
        self.sessions = []

    async def start_trace(self, session_id, task):
        self.sessions.append(session_id)
        return SimpleNamespace(id="returned-trace")

    async def add_step(self, trace_id, **kwargs):
        assert trace_id == "returned-trace"
        step = SimpleNamespace(id=f"returned-step-{len(self.steps)}", tool_calls=[])
        self.steps.append(step)
        return step

    async def record_tool_call(self, step_id, tool_name, arguments, result):
        assert step_id == self.steps[-1].id
        call = SimpleNamespace(id=f"tool-{step_id}", tool_name=tool_name)
        self.steps[-1].tool_calls.append(call)
        return call

    async def complete_trace(self, trace_id, **kwargs):
        assert trace_id == "returned-trace"

    async def get_session_traces(self, conversation_id):
        self.sessions.append(conversation_id)
        return [SimpleNamespace(steps=self.steps)]


def test_hosted_exercise_reuses_server_ids_and_checks_reads(tmp_path):
    short_term = ShortTerm()
    seen = []

    async def wait(**kwargs):
        seen.append(kwargs)
        return True

    async def search(*args, **kwargs):
        return []  # An empty candidate set is not invented as entity provenance.

    client = SimpleNamespace(
        short_term=short_term,
        long_term=SimpleNamespace(wait_for_extraction=wait, search_entities=search),
    )
    state = make_state(tmp_path, "nams")
    assert asyncio.run(nams.exercise(client, state)) == "server-conversation-id"
    assert set(short_term.ids) == {"server-conversation-id"}
    assert len(state.data["resources"]["message"]) == 2
    assert state.data["seed_verified"] is True
    assert seen[0]["session_id"] == "server-conversation-id"
    assert seen[0]["timeout"] == 60.0


def test_hosted_extraction_timeout_is_not_verified_and_keeps_cleanup_records(tmp_path):
    async def wait(**kwargs):
        return False

    client = SimpleNamespace(
        short_term=ShortTerm(),
        long_term=SimpleNamespace(wait_for_extraction=wait),
    )
    state = make_state(tmp_path, "nams")
    with pytest.raises(TimeoutError):
        asyncio.run(nams.exercise(client, state))
    assert not state.data.get("seed_verified")
    resources = state.data["resources"]
    assert list(resources["conversation"]) == ["server-conversation-id"]
    assert len(resources["message"]) == 2
    saved = json.loads(state.path.read_text())
    assert not saved.get("seed_verified")
    assert saved["resources"] == resources


def test_skills_fixture_is_one_procedure_and_records_real_conversation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = SimpleNamespace(short_term=ShortTerm(), reasoning=Reasoning())
    ledger = make_state(tmp_path)
    asyncio.run(skills.seed(client, ledger))
    state = json.loads(ledger.path.read_text())
    assert state["seed_verified"] is True
    assert state["conversation_id"] == "server-conversation-id"
    assert len(client.reasoning.steps) == 9
    assert len(state["resources"]["step"]) == 9
    assert len(state["resources"]["tool_call"]) == 9
    assert {call.tool_name for step in client.reasoning.steps for call in step.tool_calls} == {
        "lookup_order",
        "check_return_policy",
        "record_refund_decision",
    }


def test_skills_routes_preserve_v1_and_returned_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    payload = io.BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("SKILL.md", "# Simulated procedure")

    def handle(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/capabilities"):
            return httpx.Response(200, json={"distillation": True, "attestationConfigured": False})
        if path.endswith("/generate"):
            assert json.loads(request.content) == {
                "nameHint": "simulated-refund-decision",
                "scope": {"type": "workspace"},
            }
            return httpx.Response(202, json={"runId": "actual-run", "status": "queued"})
        if path.endswith("/runs/actual-run"):
            return httpx.Response(200, json={"outcome": "Created", "skillId": "actual-skill"})
        if path.endswith("/download"):
            return httpx.Response(200, content=payload.getvalue())
        if path.endswith("/explain-provenance"):
            return httpx.Response(200, json={"claims": [{"sourceNodeIds": ["returned-step"]}]})
        if path.endswith("/query"):
            return httpx.Response(200, json={"rows": []})
        return httpx.Response(200, json={"fixture": True})

    async def run():
        state = make_state(tmp_path)
        state.data["seed_verified"] = True
        state.record("step", "returned-step")
        async with httpx.AsyncClient(
            base_url=state.data["identity"]["endpoint"] + "/", transport=httpx.MockTransport(handle)
        ) as http:
            for command in ["generate", "inspect"]:
                await skills.run_command(http, command, state)
            assert not any(path.endswith("/publish") for _, path in calls)
            for command in ["publish", "download"]:
                await skills.run_command(http, command, state)
        return state

    state = asyncio.run(run())
    assert state.data["run_id"] == "actual-run"
    assert state.data["skill_id"] == "actual-skill"
    assert all(path.startswith("/v1/skills/") or path == "/v1/query" for _, path in calls)
    assert Path("simulated-refund-skill.zip").exists()


def test_skills_withheld_and_http_failure_do_not_publish(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"outcome": "Withheld"})

    async def run():
        state = make_state(tmp_path)
        state.data["run_id"] = "returned-run"
        async with httpx.AsyncClient(
            base_url="https://service.invalid/v1/", transport=httpx.MockTransport(handle)
        ) as http:
            with pytest.raises(RuntimeError, match="No skill to publish"):
                await skills.run_command(http, "inspect", state)
        async with httpx.AsyncClient(
            base_url="https://service.invalid/v1/",
            transport=httpx.MockTransport(lambda _request: httpx.Response(403)),
        ) as http:
            with pytest.raises(httpx.HTTPStatusError):
                state.data.pop("run_id")
                state.data["seed_verified"] = True
                await skills.run_command(http, "generate", state)

    asyncio.run(run())
    assert calls == ["/v1/skills/runs/returned-run"]


class FirstWriteService:
    """In-memory NAMS routes that ``nams_first_write.py`` calls, served through httpx."""

    def __init__(self, extraction_summary):
        self.extraction_summary = extraction_summary
        self.log = []
        self.conversations = {}

    def __call__(self, request):
        import uuid

        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else None
        parts = path.strip("/").split("/")
        if request.method == "GET" and path == "/conversations":
            response = httpx.Response(200, json={"conversations": []})
        elif request.method == "POST" and path == "/conversations":
            conversation_id = str(uuid.uuid4())
            self.conversations[conversation_id] = []
            response = httpx.Response(201, json={"id": conversation_id, "metadata": {}})
        elif request.method == "POST" and path == "/query":
            response = httpx.Response(200, json={"columns": ["id"], "rows": []})
        elif parts[0] == "conversations" and parts[1] not in self.conversations:
            response = httpx.Response(404, json={"error": "conversation not found"})
        elif parts[0] == "conversations" and request.method == "DELETE" and len(parts) == 2:
            del self.conversations[parts[1]]
            response = httpx.Response(204)
        elif parts[0] == "conversations" and request.method == "GET" and len(parts) == 2:
            response = httpx.Response(200, json={"id": parts[1], "metadata": {}})
        elif parts[2:] == ["messages"] and request.method == "POST":
            message = {"id": str(uuid.uuid4()), "conversationId": parts[1], **body}
            self.conversations[parts[1]].append(message)
            response = httpx.Response(201, json=message)
        elif parts[2:] == ["messages"] and request.method == "GET":
            response = httpx.Response(200, json={"messages": self.conversations[parts[1]]})
        elif parts[2:] == ["extraction-status"]:
            response = httpx.Response(200, json={"summary": self.extraction_summary})
        else:
            response = httpx.Response(500, json={"error": f"unexpected {request.method} {path}"})
        self.log.append((request.method, path, response.status_code))
        return response


def run_first_write(monkeypatch, tmp_path, service):
    import neo4j_agent_memory.nams.long_term as nams_long_term
    import neo4j_agent_memory.nams.transport as transport

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        transport.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(service), **kwargs),
    )
    clock = Clock()
    monkeypatch.setattr(nams_long_term, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(nams_long_term, "asyncio", SimpleNamespace(sleep=clock.sleep))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORY_API_KEY", "nams_fixture")
    monkeypatch.setenv("MEMORY_ENDPOINT", "https://service.invalid/v1")
    monkeypatch.delenv("MEMORY_WORKSPACE_ID", raising=False)
    asyncio.run(load_fixture("nams_first_write").main())


def test_first_write_uses_returned_id_and_confirms_delete(monkeypatch, tmp_path, capsys):
    service = FirstWriteService({"completed": 1})
    run_first_write(monkeypatch, tmp_path, service)
    methods = [(method, path) for method, path, _status in service.log]
    created = methods.index(("POST", "/conversations"))
    first_write = next(i for i, (m, p) in enumerate(methods) if m == "POST" and "/messages" in p)
    assert created < first_write
    conversation_paths = [p for _m, p in methods if p.startswith("/conversations/")]
    assert conversation_paths
    assert all("docs-nams-first-write" not in path for path in conversation_paths)
    delete = next(i for i, (m, p) in enumerate(methods) if m == "DELETE")
    method, path, status = service.log[delete + 1]
    assert method == "GET" and path.startswith(methods[delete][1]) and status == 404
    assert not service.conversations
    assert "Stored and read back: user said 'Hello from my first hosted write.'" in (
        capsys.readouterr().out
    )


def test_first_write_keeps_conversation_while_extraction_is_pending(monkeypatch, tmp_path):
    service = FirstWriteService({"pending": 1})
    with pytest.raises(TimeoutError, match="Extraction still pending"):
        run_first_write(monkeypatch, tmp_path, service)
    assert all(method != "DELETE" for method, _path, _status in service.log)
    assert len(service.conversations) == 1


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.py")), ids=lambda path: path.name)
def test_complete_tutorial_programs_compile(path):
    compile(path.read_text(), str(path), "exec")


def load_lifecycle_example():
    path = EXAMPLES.parents[3] / "examples/ontology-lifecycle/main.py"
    spec = importlib.util.spec_from_file_location("docs_ontology_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("status,errored", [("failed", 0), ("completed", 1)])
def test_failed_migration_stops_before_real_migration(status, errored):
    lifecycle = load_lifecycle_example()
    calls = []

    async def migrate(*args, **kwargs):
        calls.append(kwargs["dry_run"])
        return SimpleNamespace(
            id="returned-job",
            status=status,
            errored=errored,
            error_message="fixture failure",
            processed=0,
        )

    async def run():
        client = SimpleNamespace(ontology=SimpleNamespace(migrate=migrate))
        for dry_run in [True, False]:
            await lifecycle.run_migration(
                client,
                ontology_id="returned-ontology",
                from_version=SimpleNamespace(id="returned-v1"),
                to_version=SimpleNamespace(id="returned-v2"),
                dry_run=dry_run,
            )

    with pytest.raises(RuntimeError, match="returned-job"):
        asyncio.run(run())
    assert calls == [True]


def test_migration_deadline_stops_before_following_operation(monkeypatch):
    lifecycle = load_lifecycle_example()
    monkeypatch.setattr(lifecycle, "MIGRATION_TIMEOUT", 0)
    calls = []

    async def migrate(*args, **kwargs):
        calls.append(kwargs["dry_run"])
        return SimpleNamespace(id="queued-job", status="pending")

    async def run():
        client = SimpleNamespace(ontology=SimpleNamespace(migrate=migrate))
        for dry_run in [True, False]:
            await lifecycle.run_migration(
                client,
                ontology_id="returned-ontology",
                from_version=SimpleNamespace(id="returned-v1"),
                to_version=SimpleNamespace(id="returned-v2"),
                dry_run=dry_run,
            )

    with pytest.raises(TimeoutError, match="queued-job"):
        asyncio.run(run())
    assert calls == [True]


def test_tutorial_readback_method_exists_on_both_backends():
    from neo4j_agent_memory.memory.short_term import ShortTermMemory
    from neo4j_agent_memory.nams.short_term import NamsShortTermMemory

    assert callable(ShortTermMemory.get_conversation)
    assert callable(NamsShortTermMemory.get_conversation)
    for name in ["anthropic_local_memory", "nams_quickstart", "microsoft_shopping_tutorial"]:
        source = (EXAMPLES / f"{name}.py").read_text()
        assert ".get_messages(" not in source
