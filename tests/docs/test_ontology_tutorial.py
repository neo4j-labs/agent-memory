"""Run the actual ontology lesson/recovery against a stateful mocked HTTP service."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/modules/ROOT/examples"
sys.path.insert(0, str(EXAMPLES))
import hosted_tutorial_helpers as helpers  # noqa: E402
import ontology_quickstart as lesson  # noqa: E402
from hosted_tutorial_state import TutorialState, http_client  # noqa: E402

from neo4j_agent_memory import NamsSettings  # noqa: E402
from neo4j_agent_memory.nams import HttpTransport, NamsOntology, StaticApiKeyAuth  # noqa: E402

DOC = {
    "domain": {"id": "healthcare", "name": "Healthcare"},
    "entity_types": [{"label": "Patient", "pole_type": "PERSON"}],
    "relationships": [],
}


def settings(**overrides):
    return NamsSettings(
        nams={
            "endpoint": "https://ontology.invalid/v1",
            "api_key": "synthetic-key",
            "workspace_id": "isolated-test",
            "max_retries": 0,
            **overrides,
        },
        _env_file=None,
    )


def version(version_id, ontology_id, revision, mode):
    return {
        "id": version_id,
        "ontology_id": ontology_id,
        "revision": revision,
        "validation_mode": mode,
        "schema_json": json.dumps(DOC),
    }


class Service:
    def __init__(self):
        self.versions = {
            "prior-1": version("prior-1", "prior-ontology", 1, "permissive"),
            "prior-2": version("prior-2", "prior-ontology", 2, "strict"),
            "clone-1": version("clone-1", "owned-clone", 1, "permissive"),
            "clone-2": version("clone-2", "owned-clone", 2, "strict"),
        }
        self.active = "prior-1"  # Latest revision 2 is deliberately not active.
        self.clone_exists = False
        self.calls = []
        self.legacy = False
        self.missing_template = False
        self.failure = None
        self.activation_ignored = False
        self.restore_mismatch = False
        self.delete_ignored = False
        self.preexisting_clone = False
        self.closed = False
        self.http_clients = []
        self.active_payload = None

    def handle(self, request):
        method, path = request.method, request.url.path.removeprefix("/v1/")
        body = json.loads(request.content) if request.content else None
        self.calls.append((method, path, body))
        assert not any(word in path for word in ("entities", "query", "conversations"))
        if self.failure == (method, path, body):
            return httpx.Response(503, json={"error": "deliberate offline failure"})
        if method == "GET" and path == "ontologies/active":
            if self.active_payload is not None:
                return httpx.Response(200, json=self.active_payload)
            return httpx.Response(
                200,
                json={
                    "ontology": DOC,
                    "version": None if self.legacy else self.versions[self.active],
                },
            )
        if method == "GET" and path == "ontologies":
            rows = [
                {
                    "id": "prior-ontology",
                    "name": "healthcare",
                    "current_revision": 2,
                    "is_active": True,
                }
            ]
            if not self.missing_template:
                rows.append({"id": "system-template", "name": "healthcare", "is_system": True})
            if self.preexisting_clone:
                rows.append({"id": "owned-clone", "name": "healthcare-clone"})
            return httpx.Response(200, json={"ontologies": rows})
        if method == "POST" and path == "ontologies/healthcare/clone":
            self.clone_exists = True
            return httpx.Response(201, json=self.versions["clone-1"])
        if method == "PUT" and path == "ontologies/owned-clone":
            assert body["validation_mode"] == "strict"
            return httpx.Response(200, json=self.versions["clone-2"])
        if method == "POST" and path == "ontologies/active":
            selected = body["version_id"]
            if not (self.activation_ignored and selected == "clone-2"):
                self.active = (
                    "prior-2" if self.restore_mismatch and selected == "prior-1" else selected
                )
            return httpx.Response(200, json=self.versions[self.active])
        if path == "ontologies/owned-clone":
            if method == "DELETE":
                assert self.active == "prior-1", "Delete occurred before exact restoration"
                if not self.delete_ignored:
                    self.clone_exists = False
                return httpx.Response(204)
            if method == "GET":
                return httpx.Response(
                    200 if self.clone_exists else 404,
                    json={
                        "record": {"id": "owned-clone", "name": "healthcare-clone"},
                        "versions": [self.versions["clone-1"], self.versions["clone-2"]],
                    },
                )
        raise AssertionError(f"Unexpected request: {method} {path}")

    def http_client(self, config=None):
        client = http_client(config or settings(), transport=httpx.MockTransport(self.handle))
        self.http_clients.append(client)
        return client

    async def read_active(self):
        async with self.http_client() as http:
            return await helpers.read_active_binding(http)

    async def client(self):
        config = settings().nams
        transport = HttpTransport.from_config(config, auth=StaticApiKeyAuth.from_config(config))
        transport._client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

        async def close():
            await transport.close()
            self.closed = True

        # The ontology accessor, HTTP route table, parsing, and errors are real SDK code.
        return SimpleNamespace(ontology=NamsOntology(transport), close=close)


def new_state(tmp_path):
    return TutorialState.create(tmp_path / "ontology.json", settings(), "ontology")


async def run_lesson(service, state):
    client = await service.client()
    try:
        await lesson.exercise(client, state, read_active=service.read_active)
    finally:
        await client.close()


def test_success_restores_older_bound_revision_and_verifies_exact_clone_absence(tmp_path):
    service, state = Service(), new_state(tmp_path)
    asyncio.run(run_lesson(service, state))
    assert service.active == "prior-1" and not service.clone_exists
    assert state.data["ontology"]["previous"]["revision"] == 1
    assert state.data["ontology"]["previous"]["validation_mode"] == "permissive"
    assert state.data["ontology"]["status"] == "complete"
    assert state.data["resources"]["ontology"]["owned-clone"]["status"] == "deleted"
    assert set(state.data["resources"]["ontology_version"]) == {"clone-1", "clone-2"}
    assert not state.data["pending"]
    assert service.calls[-1][:2] == ("GET", "ontologies/owned-clone")
    # The released get_active() would request this prior detail and infer v2.
    # The lesson uses only its explicit catalog read and authoritative REST GETs.
    assert sum(path == "ontologies" for _, path, _ in service.calls) == 1
    assert not any(path == "ontologies/prior-ontology" for _, path, _ in service.calls)
    assert all(client.is_closed for client in service.http_clients)
    assert service.closed


def test_raw_active_reader_uses_resolved_credentials_and_only_the_active_route():
    calls = []

    def handle(request):
        calls.append((request.method, str(request.url)))
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        assert request.headers["X-Workspace-Id"] == "isolated-test"
        return httpx.Response(
            200,
            json={
                "ontology": DOC,
                "version": version("prior-1", "prior-ontology", 1, "permissive"),
            },
        )

    async def run():
        async with http_client(settings(), transport=httpx.MockTransport(handle)) as http:
            binding = await helpers.read_active_binding(http)
        assert http.is_closed
        return binding

    binding = asyncio.run(run())
    assert binding["version_id"] == "prior-1" and binding["revision"] == 1
    assert binding["validation_mode"] == "permissive"
    assert binding["document"]["domain"]["id"] == "healthcare"
    assert calls == [("GET", "https://ontology.invalid/v1/ontologies/active")]


@pytest.mark.parametrize(
    "invalid",
    [
        "missing-version",
        "null-version",
        "version-list",
        "missing-id",
        "blank-id",
        "missing-ontology",
        "boolean-revision",
        "string-revision",
        "zero-revision",
        "unknown-mode",
        "missing-document",
        "malformed-schema",
        "schema-array",
        "conflicting-schema",
    ],
)
def test_raw_active_metadata_failure_prevents_tutorial_mutations(tmp_path, invalid):
    service, state = Service(), new_state(tmp_path)
    payload = {"ontology": DOC, "version": version("prior-1", "prior-ontology", 1, "permissive")}
    if invalid == "missing-version":
        payload.pop("version")
    elif invalid == "null-version":
        payload["version"] = None
    elif invalid == "version-list":
        payload["version"] = []
    elif invalid == "missing-id":
        payload["version"].pop("id")
    elif invalid == "blank-id":
        payload["version"]["id"] = " "
    elif invalid == "missing-ontology":
        payload["version"].pop("ontology_id")
    elif invalid.endswith("revision"):
        payload["version"]["revision"] = {
            "boolean-revision": True,
            "string-revision": "1",
            "zero-revision": 0,
        }[invalid]
    elif invalid == "unknown-mode":
        payload["version"]["validation_mode"] = "invented"
    elif invalid == "missing-document":
        payload.pop("ontology")
    elif invalid == "malformed-schema":
        payload["version"]["schema_json"] = "{"
    elif invalid == "schema-array":
        payload["version"]["schema_json"] = "[]"
    else:
        payload["version"]["schema_json"] = json.dumps(
            {**DOC, "domain": {"id": "foreign", "name": "Foreign"}}
        )
    service.active_payload = payload
    with pytest.raises(RuntimeError, match="binding|schema"):
        asyncio.run(run_lesson(service, state))
    assert all(method == "GET" for method, _, _ in service.calls)
    assert not state.data.get("ontology") and state.data["pending"] is None
    assert service.closed and all(client.is_closed for client in service.http_clients)


@pytest.mark.parametrize("schema", ["missing", None, "normalized"])
def test_raw_active_reader_accepts_optional_or_normalized_matching_schema(schema):
    service = Service()
    payload = {"ontology": DOC, "version": version("prior-1", "prior-ontology", 1, "permissive")}
    if schema == "missing":
        payload["version"].pop("schema_json")
    elif schema is None:
        payload["version"]["schema_json"] = None
    else:
        from neo4j_agent_memory.nams import OntologyDocument

        payload["version"]["schema_json"] = OntologyDocument.model_validate(DOC).model_dump_json()
    service.active_payload = payload
    assert asyncio.run(service.read_active())["version_id"] == "prior-1"


@pytest.mark.parametrize("missing", ["legacy", "missing_template"])
def test_missing_prerequisite_stops_before_writes(tmp_path, missing):
    service, state = Service(), new_state(tmp_path)
    setattr(service, missing, True)
    with pytest.raises(RuntimeError):
        asyncio.run(run_lesson(service, state))
    assert all(method == "GET" for method, _, _ in service.calls)
    assert service.closed


def test_body_failure_is_preserved_after_successful_cleanup(tmp_path):
    service, state = Service(), new_state(tmp_path)

    async def run():
        client = await service.client()
        try:
            async with helpers.temporary_strict_ontology(
                client.ontology, "healthcare", state, read_active=service.read_active
            ):
                raise ValueError("exercise failed")
        finally:
            await client.close()

    with pytest.raises(ValueError, match="exercise failed"):
        asyncio.run(run())
    assert service.active == "prior-1" and not service.clone_exists


def test_activation_mismatch_fails_lesson_but_cleans_when_prior_remains(tmp_path):
    service, state = Service(), new_state(tmp_path)
    service.activation_ignored = True
    with pytest.raises(RuntimeError, match="readback differs"):
        asyncio.run(run_lesson(service, state))
    assert service.active == "prior-1" and not service.clone_exists


@pytest.mark.parametrize("failure", ["request", "readback"])
def test_restoration_failure_retains_clone_and_state(tmp_path, failure):
    service, state = Service(), new_state(tmp_path)
    if failure == "request":
        service.failure = ("POST", "ontologies/active", {"version_id": "prior-1"})
    else:
        service.restore_mismatch = True
    with pytest.raises(Exception):
        asyncio.run(run_lesson(service, state))
    assert service.clone_exists and state.data["pending"]["operation"] == "restore"
    assert not any(method == "DELETE" for method, _, _ in service.calls)
    assert service.closed


def test_both_exercise_and_restoration_failure_remain_in_exception_chain(tmp_path):
    service, state = Service(), new_state(tmp_path)
    service.failure = ("POST", "ontologies/active", {"version_id": "prior-1"})

    async def run():
        client = await service.client()
        try:
            async with helpers.temporary_strict_ontology(
                client.ontology, "healthcare", state, read_active=service.read_active
            ):
                raise ValueError("original exercise failure")
        finally:
            await client.close()

    with pytest.raises(Exception) as caught:
        asyncio.run(run())
    assert isinstance(caught.value.__cause__, ValueError)
    assert str(caught.value.__cause__) == "original exercise failure"
    assert service.clone_exists


async def interrupt_after_activation(service, state):
    """Persist a process-stop boundary after the write but before its acknowledgement."""
    client = await service.client()
    try:
        previous = await service.read_active()
        clone = await client.ontology.clone("healthcare")
        strict = await client.ontology.update(
            clone.ontology_id, clone.document, validation_mode="strict"
        )
        state.data["ontology"] = {
            "previous": previous,
            "clone_id": clone.ontology_id,
            "strict": helpers._version_binding(strict),
        }
        state.record("ontology", clone.ontology_id, created_by_run=True)
        state.record("ontology_version", clone.id, ontology_id=clone.ontology_id)
        state.record("ontology_version", strict.id, ontology_id=clone.ontology_id)
        state.begin({"operation": "activate", "version_id": strict.id})
        await client.ontology.activate(strict.id)
    finally:
        await client.close()


async def recover_new_client(service, path):
    state = TutorialState.load(path, settings(), "ontology")
    client = await service.client()
    try:
        await helpers.recover_ontology(client.ontology, state, read_active=service.read_active)
    finally:
        await client.close()
    return state


def test_recovery_loads_durable_state_and_restores_without_replaying_seed(tmp_path):
    service, state = Service(), new_state(tmp_path)
    asyncio.run(interrupt_after_activation(service, state))
    service.calls.clear()
    recovered = asyncio.run(recover_new_client(service, state.path))
    assert service.active == "prior-1" and not service.clone_exists
    assert recovered.data["ontology"]["interrupted_operations"] == [
        {"operation": "activate", "version_id": "clone-2"}
    ]
    assert not any(method == "PUT" or path.endswith("/clone") for method, path, _ in service.calls)
    assert recovered.data["pending"] is None


def test_concurrent_binding_is_not_overwritten_by_recovery(tmp_path):
    service, state = Service(), new_state(tmp_path)
    asyncio.run(interrupt_after_activation(service, state))
    service.active = "prior-2"
    service.calls.clear()
    with pytest.raises(RuntimeError, match="changed unexpectedly"):
        asyncio.run(recover_new_client(service, state.path))
    assert service.clone_exists
    assert all(method == "GET" for method, _, _ in service.calls)


def test_unknown_clone_outcome_is_retained_without_repeating_mutation(tmp_path):
    service, state = Service(), new_state(tmp_path)

    async def prepare():
        client = await service.client()
        state.data["ontology"] = {"previous": await service.read_active()}
        state.begin({"operation": "clone", "template": "healthcare"})
        await client.close()

    asyncio.run(prepare())
    service.calls.clear()
    with pytest.raises(RuntimeError, match="no returned ID"):
        asyncio.run(recover_new_client(service, state.path))
    assert all(method == "GET" for method, _, _ in service.calls)
    assert TutorialState.load(state.path, settings()).data["pending"] is not None


def test_preexisting_clone_response_never_authorizes_deletion(tmp_path):
    service, state = Service(), new_state(tmp_path)
    service.preexisting_clone = True
    with pytest.raises(RuntimeError, match="ownership"):
        asyncio.run(run_lesson(service, state))
    assert service.clone_exists
    assert not any(method == "DELETE" for method, _, _ in service.calls)


@pytest.mark.parametrize("failure", ["request", "readback"])
def test_delete_failure_can_be_recovered_after_original_binding_restored(tmp_path, failure):
    service, state = Service(), new_state(tmp_path)
    if failure == "request":
        service.failure = ("DELETE", "ontologies/owned-clone", None)
    else:
        service.delete_ignored = True
    with pytest.raises(Exception):
        asyncio.run(run_lesson(service, state))
    assert service.active == "prior-1" and service.clone_exists
    assert state.data["pending"]["operation"] == "delete"
    service.failure, service.delete_ignored = None, False
    asyncio.run(recover_new_client(service, state.path))
    assert not service.clone_exists


def test_standalone_verification_is_read_only_and_checks_disposition(tmp_path):
    service, state = Service(), new_state(tmp_path)
    asyncio.run(run_lesson(service, state))
    service.calls.clear()

    async def verify():
        client = await service.client()
        try:
            await helpers.verify_ontology_restoration(
                client.ontology,
                TutorialState.load(state.path, settings()),
                read_active=service.read_active,
            )
        finally:
            await client.close()

    asyncio.run(verify())
    assert all(method == "GET" for method, _, _ in service.calls)
    service.clone_exists = True
    with pytest.raises(RuntimeError, match="absence"):
        asyncio.run(verify())


def test_cli_closes_client_when_restore_fails(tmp_path, monkeypatch):
    import neo4j_agent_memory

    service = Service()
    service.failure = ("POST", "ontologies/active", {"version_id": "prior-1"})
    monkeypatch.setattr(neo4j_agent_memory, "NamsSettings", settings)

    async def connect(_settings):
        return await service.client()

    monkeypatch.setattr(neo4j_agent_memory, "connect", connect)
    monkeypatch.setattr(lesson, "http_client", service.http_client)
    with pytest.raises(Exception):
        asyncio.run(lesson.main(["seed", "--state", str(tmp_path / "cli.json")]))
    assert service.closed and service.clone_exists
    assert service.http_clients and all(client.is_closed for client in service.http_clients)


def test_cli_rejects_workspace_switch_before_connect(tmp_path, monkeypatch):
    import neo4j_agent_memory

    state = new_state(tmp_path)
    changed = settings(workspace_id="another-workspace")
    monkeypatch.setattr(neo4j_agent_memory, "NamsSettings", lambda: changed)

    async def forbidden_connect(_settings):
        pytest.fail("Identity mismatch must be checked before opening a client")

    monkeypatch.setattr(neo4j_agent_memory, "connect", forbidden_connect)
    with pytest.raises(RuntimeError, match="changed"):
        asyncio.run(lesson.main(["recover", "--state", str(state.path)]))


@pytest.mark.parametrize("operation", ["clone", "update", "activate"])
def test_failed_write_retains_unknown_clone_or_cleans_only_known_clone(tmp_path, operation):
    service, state = Service(), new_state(tmp_path)
    service.failure = {
        "clone": ("POST", "ontologies/healthcare/clone", None),
        "update": ("PUT", "ontologies/owned-clone", None),
        "activate": ("POST", "ontologies/active", {"version_id": "clone-2"}),
    }[operation]
    if operation == "update":
        # Use the exact body generated by the public SDK, including normalized defaults.
        from neo4j_agent_memory.nams import OntologyDocument

        service.failure = (
            "PUT",
            "ontologies/owned-clone",
            {
                "ontology": OntologyDocument.model_validate(DOC).model_dump(exclude_none=True),
                "validation_mode": "strict",
            },
        )
    with pytest.raises(Exception):
        asyncio.run(run_lesson(service, state))
    assert service.active == "prior-1"
    if operation == "clone":
        assert state.data["pending"]["operation"] == "clone"
        assert not any(method == "DELETE" for method, _, _ in service.calls)
    else:
        assert not service.clone_exists
        assert state.data["pending"] is None


def test_recovery_verifies_already_deleted_clone_without_repeating_delete(tmp_path):
    service, state = Service(), new_state(tmp_path)
    service.failure = ("DELETE", "ontologies/owned-clone", None)
    with pytest.raises(Exception):
        asyncio.run(run_lesson(service, state))
    # Model a remote commit followed by a lost response to the first deletion.
    service.clone_exists, service.failure = False, None
    service.calls.clear()
    recovered = asyncio.run(recover_new_client(service, state.path))
    assert not any(method == "DELETE" for method, _, _ in service.calls)
    assert recovered.data["ontology"]["deleted"] is True
    assert recovered.data["pending"] is None


@pytest.mark.parametrize("foreign_binding", [False, True])
def test_live_python_guard_restores_before_deletion_or_retains_foreign_binding(
    tmp_path, foreign_binding
):
    from tests.integration.nams.test_ontology import fresh_clone, restore_active_ontology

    service = Service()

    async def run():
        client = await service.client()
        guard = restore_active_ontology.__wrapped__(client)
        try:
            owned = await guard.__anext__()
            # The live guard's template is conservation; use its test-owned returned IDs.
            clone = await client.ontology.clone("healthcare")
            owned["ontologies"].add(clone.ontology_id)
            owned["versions"]["clone-2"] = "strict"
            service.active = "prior-2" if foreign_binding else "clone-2"
            service.calls.clear()
            if foreign_binding:
                with pytest.raises(AssertionError, match="Unexpected active binding"):
                    await guard.__anext__()
            else:
                with pytest.raises(StopAsyncIteration):
                    await guard.__anext__()
        finally:
            await client.close()

    asyncio.run(run())
    if foreign_binding:
        assert service.clone_exists and all(method == "GET" for method, _, _ in service.calls)
    else:
        assert service.active == "prior-1" and not service.clone_exists


def test_live_python_guard_rejects_incomplete_prior_before_yielding():
    from tests.integration.nams.test_ontology import restore_active_ontology

    service = Service()
    service.legacy = True

    async def run():
        client = await service.client()
        try:
            guard = restore_active_ontology.__wrapped__(client)
            with pytest.raises(AssertionError):
                await guard.__anext__()
        finally:
            await client.close()

    asyncio.run(run())
    assert all(method == "GET" for method, _, _ in service.calls)
