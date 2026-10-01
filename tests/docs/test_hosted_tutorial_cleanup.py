"""Exercise the maintained cleanup code with real HTTP request/response boundaries."""

from __future__ import annotations

import asyncio
import importlib
import json
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/modules/ROOT/examples"
sys.path.insert(0, str(EXAMPLES))
from hosted_tutorial_cleanup import cleanup  # noqa: E402
from hosted_tutorial_state import TutorialState, http_client, verify_messages  # noqa: E402

from neo4j_agent_memory import NamsSettings  # noqa: E402


def settings(**overrides):
    return NamsSettings(
        nams={
            "endpoint": "https://service.invalid/v1",
            "api_key": "nams_test",
            "workspace_id": "workspace-a",
            **overrides,
        },
        _env_file=None,
    )


def ledger(tmp_path):
    state = TutorialState.create(tmp_path / "state.json", settings(), "test")
    state.data["started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    state.data["conversation_id"] = "conv-1"
    state.record("conversation", "conv-1", metadata={"tutorialRun": state.data["run_token"]})
    state.record("message", "msg-1", role="user", content_sha256="test")
    return state


class Service:
    def __init__(self, state):
        self.metadata = state.data["resources"]["conversation"]["conv-1"]["metadata"]
        self.calls = []
        self.conversation = True
        self.messages = [{"id": "msg-1"}]
        self.entities = {"entity-1"}
        self.provenance = [
            {
                "id": "entity-1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "sources": ["msg-1"],
                "source_count": 1,
                "neighbors": ["msg-1"],
                "neighbor_count": 1,
            }
        ]
        self.summary = {"completed": 1}
        self.residuals = []
        self.failure = None

    def handle(self, request):
        path = request.url.path.removeprefix("/v1/")
        self.calls.append((request.method, path))
        if self.failure == (request.method, path):
            return httpx.Response(503, json={"error": "deliberate failure"})
        if path == "query":
            body = json.loads(request.content)
            assert set(body) == {"cypher", "params"}
            if "message_ids" in body["params"]:
                assert body["params"] == {"message_ids": ["msg-1"]}
                rows = [row for row in self.provenance if row["id"] in self.entities]
            else:
                assert body["params"]["conversation"] == "conv-1"
                rows = self.residuals
            return httpx.Response(200, json={"rows": rows})
        if path.endswith("/extraction-status"):
            return httpx.Response(200, json={"summary": self.summary})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": self.messages})
        if path == "conversations/conv-1":
            if request.method == "DELETE":
                self.conversation = False
                return httpx.Response(204)
            return httpx.Response(
                200 if self.conversation else 404, json={"id": "conv-1", "metadata": self.metadata}
            )
        if path.startswith("entities/"):
            entity_id = path.split("/")[-1]
            if request.method == "DELETE":
                self.entities.discard(entity_id)
                return httpx.Response(204)
            return httpx.Response(
                200 if entity_id in self.entities else 404, json={"id": entity_id}
            )
        raise AssertionError(f"Unexpected endpoint: {request.method} {path}")

    async def run(self, state, **kwargs):
        async with http_client(settings(), transport=httpx.MockTransport(self.handle)) as http:
            await cleanup(http, state, **kwargs)


def test_private_state_is_atomic_and_refuses_reseed(tmp_path):
    state = ledger(tmp_path)
    assert stat.S_IMODE(state.path.stat().st_mode) == 0o600
    before = state.path.read_bytes()
    with pytest.raises(RuntimeError, match="already exists"):
        TutorialState.create(state.path, settings(), "test")
    assert state.path.read_bytes() == before
    assert "nams_test" not in state.path.read_text()
    redacted = json.dumps(state.inspect())
    for secret in (
        "credential_salt",
        "credential_digest",
        state.data["identity"]["credential_salt"],
        state.data["identity"]["credential_digest"],
    ):
        assert secret not in redacted
    state.begin("append message")
    resumed = TutorialState.load(state.path, settings(), "test")
    assert resumed.data["pending"] == "append message"
    with pytest.raises(RuntimeError, match="Unresolved write"):
        resumed.begin("duplicate write")
    resumed.record("step", "returned-step")
    resumed.finish()
    assert TutorialState.load(state.path, settings()).data["resources"]["step"]["returned-step"]


@pytest.mark.parametrize(
    "override",
    [
        {"endpoint": "https://other.invalid/v1"},
        {"api_key": "new-key"},
        {"workspace_id": "workspace-b"},
    ],
)
def test_state_rejects_config_switch_before_requests(tmp_path, override):
    state = ledger(tmp_path)
    with pytest.raises(RuntimeError, match="changed"):
        TutorialState.load(state.path, settings(**override))


def test_invalid_state_and_auth_header_override_are_rejected(tmp_path):
    state = ledger(tmp_path)
    state.path.write_text("{}")
    with pytest.raises(RuntimeError, match="Unsupported"):
        TutorialState.load(state.path, settings())
    with pytest.raises(ValueError, match="overriding"):
        http_client(settings(headers={"X-Workspace-Id": "different"}))
    with pytest.raises(ValueError, match="REST"):
        http_client(settings(endpoint="https://bridge.invalid"))


def test_same_resolved_auth_and_workspace_are_sent_to_http():
    async def run():
        def handle(request):
            assert request.headers["authorization"] == "Bearer nams_test"
            assert request.headers["x-workspace-id"] == "workspace-a"
            assert request.url.path == "/v1/skills/capabilities"
            return httpx.Response(200)

        async with http_client(settings(), transport=httpx.MockTransport(handle)) as http:
            await http.get("skills/capabilities")

    asyncio.run(run())


@pytest.mark.parametrize("terminal_status", ["completed", "done"])
def test_cleanup_deletes_owned_entities_then_conversation_and_verifies(tmp_path, terminal_status):
    state = ledger(tmp_path)
    service = Service(state)
    service.summary = {terminal_status: 1}
    asyncio.run(service.run(state))
    deletes = [path for method, path in service.calls if method == "DELETE"]
    assert deletes == ["entities/entity-1", "conversations/conv-1"]
    assert state.data["remaining_rows"] == []
    assert state.data["retained_resources"] == {}
    assert all(
        entry["status"] == "deleted"
        for entries in state.data["resources"].values()
        for entry in entries.values()
    )
    # Re-running cleanup is an exact-ID absence check, not a reseed.
    resumed = TutorialState.load(state.path, settings())
    asyncio.run(service.run(resumed))


@pytest.mark.parametrize("change", ["shared", "old", "missing-created", "foreign-neighbor"])
def test_cleanup_retains_entities_without_exclusive_creation_proof(tmp_path, change):
    state = ledger(tmp_path)
    service = Service(state)
    row = service.provenance[0]
    if change == "shared":
        row["sources"].append("foreign-message")
        row["source_count"] = 2
    elif change == "old":
        row["created_at"] = "2000-01-01T00:00:00+00:00"
    elif change == "missing-created":
        row.pop("created_at")
    else:
        row["neighbors"].append("foreign-entity")
        row["neighbor_count"] = 2
    asyncio.run(service.run(state))
    assert "entity-1" in service.entities
    assert state.data["retained_resources"] == {"entity": ["entity-1"]}


@pytest.mark.parametrize(
    "problem",
    [
        "pending-write",
        "foreign-conversation",
        "extra-message",
        "extraction-failed",
        "unknown-status",
        "pending-extraction",
    ],
)
def test_cleanup_stops_before_delete_on_incomplete_preflight(tmp_path, problem):
    state = ledger(tmp_path)
    service = Service(state)
    if problem == "pending-write":
        state.begin("uncertain create")
    elif problem == "foreign-conversation":
        service.metadata = {"tutorialRun": "someone else's conversation"}
    elif problem == "extra-message":
        service.messages.append({"id": "unrecorded"})
    elif problem == "extraction-failed":
        service.summary = {"failed": 1}
    elif problem == "unknown-status":
        service.summary = {"invented": 1}
    else:
        service.summary = {"pending": 1}
    with pytest.raises((RuntimeError, TimeoutError)):
        asyncio.run(service.run(state, timeout=0.02, interval=0.005))
    assert not any(method == "DELETE" for method, _ in service.calls)
    assert state.path.exists()


def test_cleanup_retry_keeps_ids_after_partial_delete(tmp_path):
    state = ledger(tmp_path)
    service = Service(state)
    service.failure = ("DELETE", "conversations/conv-1")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(service.run(state))
    resumed = TutorialState.load(state.path, settings())
    assert resumed.data["resources"]["entity"]["entity-1"]["status"] == "deleted"
    service.failure = None
    asyncio.run(service.run(resumed))
    assert not resumed.data["retained_resources"]


def test_unexpected_residuals_fail_cleanup_milestone(tmp_path):
    state = ledger(tmp_path)
    service = Service(state)
    service.residuals = [{"id": "unrecorded-step", "labels": ["AgentStep"]}]
    with pytest.raises(RuntimeError, match="residuals"):
        asyncio.run(service.run(state))
    assert state.data["remaining_rows"] == service.residuals


def test_entity_residual_is_not_reported_as_deleted(tmp_path):
    state = ledger(tmp_path)
    service = Service(state)
    service.residuals = [{"id": "entity-1", "labels": ["Entity"]}]
    with pytest.raises(RuntimeError, match="residuals"):
        asyncio.run(service.run(state))
    assert state.data["resources"]["entity"]["entity-1"]["status"] == "retained"


def test_known_steps_are_retained_without_unsupported_delete(tmp_path):
    state = ledger(tmp_path)
    state.record("step", "step-1")
    service = Service(state)
    service.residuals = [{"id": "step-1", "labels": ["AgentStep"]}]
    asyncio.run(service.run(state))
    assert state.data["retained_resources"] == {"step": ["step-1"]}
    assert all(
        path.startswith(("entities/", "conversations/"))
        for method, path in service.calls
        if method == "DELETE"
    )


def test_message_verification_rejects_same_text_under_wrong_id(tmp_path):
    state = ledger(tmp_path)

    async def get_conversation(_):
        return SimpleNamespace(
            messages=[SimpleNamespace(id="different-id", content="test", role="user")]
        )

    client = SimpleNamespace(short_term=SimpleNamespace(get_conversation=get_conversation))
    with pytest.raises(RuntimeError, match="unrecorded"):
        asyncio.run(verify_messages(client, state))


def test_authored_nams_program_and_cleanup_share_real_sdk_wire_contract(tmp_path):
    """Regression for the two contracts exposed by the first live cleanup attempt."""
    import nams_quickstart
    import respx

    from neo4j_agent_memory import connect

    config = settings(validate_on_connect=False)
    state = TutorialState.create(tmp_path / "actual-sdk.json", config, "nams")
    conv_id = "f0000000-0000-4000-8000-000000000001"
    header = None
    messages = []
    deleted = False
    mutations = []

    def handle(request):
        nonlocal header, messages, deleted
        path = request.url.path.removeprefix("/v1/")
        if request.method == "POST" and path == "conversations":
            body = json.loads(request.content)
            assert body == {
                "metadata": {"tutorialRun": state.data["run_token"], "tutorialLesson": "nams"}
            }
            header = {"id": conv_id, "metadata": body["metadata"], "userId": ""}
            mutations.append(path)
            return httpx.Response(201, json=header)
        if path == f"conversations/{conv_id}/messages/bulk":
            transcript = json.loads(request.content)["messages"]
            messages = [
                {
                    "id": f"f0000000-0000-4000-8000-00000000000{i + 2}",
                    "conversationId": conv_id,
                    **item,
                }
                for i, item in enumerate(transcript)
            ]
            mutations.append(path)
            return httpx.Response(201, json={"messages": messages})
        if path == f"conversations/{conv_id}/messages":
            return httpx.Response(200, json={"messages": messages})
        if path == f"conversations/{conv_id}/extraction-status":
            return httpx.Response(200, json={"summary": {"done": 2}})
        if path == f"conversations/{conv_id}":
            if request.method == "DELETE":
                deleted = True
                mutations.append(path)
                return httpx.Response(204)
            return httpx.Response(404 if deleted else 200, json=header)
        if path == "entities/search":
            return httpx.Response(200, json={"entities": []})
        if path == "query":
            return httpx.Response(200, json={"rows": []})
        raise AssertionError(f"Unplanned request: {request.method} {path}")

    async def run():
        with respx.mock(assert_all_called=False) as router:
            router.route().mock(side_effect=handle)
            client = await connect(config)
            try:
                await nams_quickstart.exercise(client, state)
            finally:
                await client.close()
            async with http_client(config) as http:
                await cleanup(http, state)

    asyncio.run(run())
    assert deleted
    assert len(mutations) == 3
    assert not state.data["retained_resources"]


@pytest.mark.parametrize("conversation_exists", [True, False])
@pytest.mark.parametrize("entity_exists", [True, False])
def test_retry_does_not_delete_with_stale_entity_ownership(
    tmp_path, conversation_exists, entity_exists
):
    """A fresh empty provenance result invalidates ownership saved before DELETE503."""
    state = ledger(tmp_path)
    service = Service(state)
    service.failure = ("DELETE", "entities/entity-1")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(service.run(state))
    saved = TutorialState.load(state.path, settings())
    assert saved.data["cleanup_preflight_complete"] is True
    assert saved.data["resources"]["entity"]["entity-1"]["owned"] is True
    assert saved.data["resources"]["entity"]["entity-1"]["status"] != "deleted"

    # After interruption the entity no longer has any provenance edge to the
    # owned messages. It may still exist with unrelated sources, or be gone.
    service.failure = None
    service.provenance = []
    service.conversation = conversation_exists
    if not entity_exists:
        service.entities.clear()
    service.calls.clear()
    asyncio.run(service.run(saved))

    assert ("DELETE", "entities/entity-1") not in service.calls
    entry = saved.data["resources"]["entity"]["entity-1"]
    if entity_exists:
        assert "entity-1" in service.entities
        assert entry["owned"] is False
        assert entry["status"] != "deleted"
        assert saved.data["retained_resources"]["entity"] == ["entity-1"]
    else:
        assert ("GET", "entities/entity-1") in service.calls
        assert entry["status"] == "deleted"
        assert "entity" not in saved.data["retained_resources"]
    assert not service.conversation


@pytest.mark.parametrize(
    "change",
    ["anonymous-source", "anonymous-neighbor", "missing-source-count", "missing-neighbor-count"],
)
def test_cleanup_retains_entities_when_provenance_ids_are_incomplete(tmp_path, change):
    state = ledger(tmp_path)
    service = Service(state)
    row = service.provenance[0]
    if change == "anonymous-source":
        # Cypher collect(source.id) omitted the second source because its ID is null.
        row["source_count"] = 2
    elif change == "anonymous-neighbor":
        row["neighbor_count"] = 2
    elif change == "missing-source-count":
        row.pop("source_count")
    else:
        row.pop("neighbor_count")
    asyncio.run(service.run(state))
    assert ("DELETE", "entities/entity-1") not in service.calls
    assert "entity-1" in service.entities
    assert state.data["retained_resources"] == {"entity": ["entity-1"]}


@pytest.mark.parametrize("lesson", ["nams", "skills"])
@pytest.mark.parametrize("remaining", [None, "entity", "step"])
def test_cleanup_cli_reports_retained_resources_with_nonzero_exit(
    tmp_path, monkeypatch, capsys, lesson, remaining
):
    """Exercise each actual CLI and cleanup helper; only the HTTP boundary is fake."""
    import neo4j_agent_memory

    module = importlib.import_module(f"{lesson}_quickstart")
    config = settings()
    state = ledger(tmp_path)
    state.data["lesson"] = lesson
    state.save()
    service = Service(state)
    if remaining == "entity":
        # An ID-less neighbor makes entity deletion unsafe, even after the
        # owned conversation and messages can be removed successfully.
        service.provenance[0]["neighbor_count"] = 2
    elif remaining == "step":
        state.record("step", "step-1")
        service.residuals = [{"id": "step-1", "labels": ["AgentStep"]}]

    clients = []

    def client_factory(actual_settings):
        assert actual_settings is config
        client = http_client(config, transport=httpx.MockTransport(service.handle))
        clients.append(client)
        return client

    monkeypatch.setattr(neo4j_agent_memory, "NamsSettings", lambda: config)
    monkeypatch.setattr(module, "http_client", client_factory)
    monkeypatch.setattr(
        sys, "argv", [f"{lesson}_quickstart.py", "cleanup", "--state", str(state.path)]
    )
    if remaining:
        with pytest.raises(SystemExit) as exc:
            asyncio.run(module.main())
        assert exc.value.code == 2
    else:
        assert asyncio.run(module.main()) is None

    assert len(clients) == 1 and clients[0].is_closed
    assert service.conversation is False
    saved = TutorialState.load(state.path, config, lesson)
    expected = {remaining: [f"{remaining}-1"]} if remaining else {}
    assert saved.data["retained_resources"] == expected
    assert saved.data["resources"]["conversation"]["conv-1"]["status"] == "deleted"
    assert saved.data["resources"]["message"]["msg-1"]["status"] == "deleted"
    if remaining == "entity":
        assert ("DELETE", "entities/entity-1") not in service.calls
        assert "entity-1" in service.entities
    assert not any(
        method == "DELETE" and not path.startswith(("entities/", "conversations/"))
        for method, path in service.calls
    )
    output = capsys.readouterr().out
    assert f"Retained resources: {expected}" in output
    if lesson == "skills":
        assert "Operator disposition" in output
