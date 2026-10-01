"""Local handoff and restart checks exercise actual tutorial state and mock services."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

EXAMPLES = Path(__file__).resolve().parents[2] / "docs/modules/ROOT/examples"
sys.path.insert(0, str(EXAMPLES))
import hosted_tutorial_helpers as helpers  # noqa: E402
import skills_quickstart as skills  # noqa: E402
from hosted_tutorial_state import (  # noqa: E402
    TutorialState,
    check_new_run,
    http_client,
    inspect_file,
)

from neo4j_agent_memory import NamsSettings  # noqa: E402
from tests.docs.test_ontology_tutorial import Service, run_lesson, settings  # noqa: E402


def test_no_write_capability_failure_can_start_separate_run_without_replacing_ledger(
    tmp_path, monkeypatch
):
    config = settings()
    monkeypatch.setattr("neo4j_agent_memory.NamsSettings", lambda: config)
    allowed = False
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"distillation": allowed})

    monkeypatch.setattr(
        skills,
        "http_client",
        lambda selected: http_client(selected, transport=httpx.MockTransport(handle)),
    )
    seeded = []

    async def connect(_settings):
        async def close():
            pass

        return SimpleNamespace(close=close)

    async def seed(_client, state):
        seeded.append(state.path)
        assert state.data["capabilities"]["distillation"] is True

    monkeypatch.setattr("neo4j_agent_memory.connect", connect)
    monkeypatch.setattr(skills, "seed", seed)
    original = tmp_path / "first" / "skills.json"
    flags = [
        "--empty-workspace-confirmed",
        "--disposal-owner",
        "fixture-owner",
        "--disposal-plan",
        "agreed retention",
        "--workspace-label",
        "fixture-workspace",
    ]
    with pytest.raises(RuntimeError, match="unavailable"):
        asyncio.run(skills.main(["seed", "--state", str(original), *flags]))
    before = original.read_bytes()
    assert calls == [("GET", "/v1/skills/capabilities")]
    assert not seeded
    next_path = tmp_path / "second" / "skills.json"
    assert check_new_run(original, next_path) == "no_remote_write_started"
    assert not next_path.exists()
    allowed = True
    asyncio.run(skills.main(["seed", "--state", str(next_path), *flags]))
    assert seeded == [next_path]
    assert original.read_bytes() == before
    assert json.loads(next_path.read_text())["operator_context"] == {
        "workspace_label": "fixture-workspace",
        "workspace_owner": "fixture-owner",
    }


def test_missing_ontology_template_can_retry_without_recovery_writes(tmp_path):
    service = Service()
    service.missing_template = True
    old = TutorialState.create(tmp_path / "first" / "ontology.json", settings(), "ontology")
    with pytest.raises(RuntimeError, match="template is absent"):
        asyncio.run(run_lesson(service, old))
    assert all(method == "GET" for method, _, _ in service.calls)
    before = old.path.read_bytes()
    next_path = tmp_path / "second" / "ontology.json"
    assert check_new_run(old.path, next_path) == "no_remote_write_started"
    service.missing_template = False
    new = TutorialState.create(next_path, settings(), "ontology")
    asyncio.run(run_lesson(service, new))
    assert old.path.read_bytes() == before
    assert new.data["ontology"]["status"] == "complete"
    assert check_new_run(new.path, tmp_path / "third" / "ontology.json") == (
        "recorded_disposition_complete"
    )


@pytest.mark.parametrize("case", ["pending", "intent_without_id", "legacy", "resource", "retained"])
def test_empty_or_finished_ledger_never_hides_uncertain_or_retained_write(tmp_path, case):
    state = TutorialState.create(tmp_path / "old" / "state.json", settings(), "nams")
    if case in {"pending", "intent_without_id"}:
        state.begin("create conversation")
        # Simulate a lost response/ID. Even a cleared pending flag cannot erase intent.
        if case == "intent_without_id":
            state.finish()
    elif case == "legacy":
        state.data.pop("remote_write_started")
    else:
        state.record("entity", "owned-id", status="retained")
        if case == "retained":
            state.data["cleanup_complete"] = True  # Inconsistent completion must not authorize.
    state.save()
    before = state.path.read_bytes()
    with pytest.raises(RuntimeError, match="No automatic new run"):
        check_new_run(state.path, tmp_path / "new" / "state.json")
    assert state.path.read_bytes() == before


def test_write_intent_is_on_disk_before_remote_call_and_never_resets(tmp_path):
    state = TutorialState.create(tmp_path / "state.json", settings(), "nams")
    state.begin("create conversation")
    assert json.loads(state.path.read_text())["remote_write_started"] is True
    state.finish()
    assert json.loads(state.path.read_text())["remote_write_started"] is True


def test_new_run_path_cannot_overwrite_state_or_share_report_directory(tmp_path):
    state = TutorialState.create(tmp_path / "state.json", settings(), "skills")
    with pytest.raises(RuntimeError, match="unused state path"):
        check_new_run(state.path, state.path)
    with pytest.raises(RuntimeError, match="separate run directory"):
        check_new_run(state.path, tmp_path / "other.json")
    occupied = tmp_path / "new" / "state.json"
    occupied.parent.mkdir()
    occupied.symlink_to(tmp_path / "missing.json")
    with pytest.raises(RuntimeError, match="unused state path"):
        check_new_run(state.path, occupied)
    report_dir = tmp_path / "old-reports"
    report_dir.mkdir()
    report = report_dir / "skills-review.json"
    report.write_text('{"prior": "review"}')
    with pytest.raises(RuntimeError, match="preserve its existing reports"):
        check_new_run(state.path, report_dir / "new-state.json")
    assert report.read_text() == '{"prior": "review"}'


def test_rotated_key_allows_only_redacted_local_inspection_without_sdk_or_environment(tmp_path):
    state = TutorialState.create(
        tmp_path / "state.json",
        settings(),
        "nams",
        workspace_label="operator-confirmed workspace",
        workspace_owner="responsible operator",
    )
    state.record(
        "message", "message-id", content_sha256="private-digest", metadata={"raw": "hidden"}
    )
    state.begin("append message")
    before = state.path.read_bytes()
    with pytest.raises(RuntimeError, match="credential changed"):
        TutorialState.load(state.path, settings(api_key="rotated-synthetic-key"), "nams")
    # -I -S removes project/site packages and user Python startup configuration.
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(EXAMPLES / "hosted_tutorial_state.py"),
            "inspect-file",
            str(state.path),
        ],
        cwd=tmp_path,
        env={"MEMORY_API_KEY": "rotated-synthetic-key"},
        capture_output=True,
        text=True,
        check=True,
    )
    shown = json.loads(result.stdout)
    assert shown["local_only"] and not shown["service_checked"]
    assert shown["pending"] == "append message"
    assert shown["resources"]["message"] == {"message-id": {"status": "retained"}}
    assert shown["operator_context"]["workspace_owner"] == "responsible operator"
    for hidden in (
        "credential_salt",
        "credential_digest",
        state.data["identity"]["credential_salt"],
        state.data["identity"]["credential_digest"],
        "synthetic-key",
        "private-digest",
        "hidden",
    ):
        assert hidden not in result.stdout
    assert state.path.read_bytes() == before
    with pytest.raises(RuntimeError, match="credential changed"):
        TutorialState.load(state.path, settings(api_key="rotated-synthetic-key"), "nams")


def test_local_inspection_shows_ontology_restoration_target_without_schema(tmp_path):
    state = TutorialState.create(tmp_path / "state.json", settings(), "ontology")
    state.data["ontology"] = {
        "previous": {
            "version_id": "ORIGINAL-VERSION",
            "ontology_id": "o",
            "revision": 1,
            "validation_mode": "permissive",
            "document": {"marker": "SCHEMA-DOC"},
        },
        "clone_id": "clone",
    }
    state.save()
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(EXAMPLES / "hosted_tutorial_state.py"),
            "inspect-file",
            str(state.path),
        ],
        cwd=tmp_path,
        env={},
        capture_output=True,
        text=True,
        check=True,
    )
    shown = json.loads(result.stdout)
    assert shown["ontology"]["previous"] == {
        "version_id": "ORIGINAL-VERSION",
        "ontology_id": "o",
        "revision": 1,
        "validation_mode": "permissive",
    }
    assert shown["ontology"]["clone_id"] == "clone"
    assert "SCHEMA-DOC" not in result.stdout


def test_local_inspection_strips_credential_bearing_url_but_keeps_port(tmp_path):
    state = TutorialState.create(tmp_path / "state.json", settings(), "nams")
    state.data["identity"]["endpoint"] = (
        "https://user:secret@service.invalid:8443/v1?key=secret#secret"
    )
    state.save()
    report = inspect_file(state.path)
    assert report["identity"]["endpoint"] == "https://service.invalid:8443/v1"
    assert "secret" not in json.dumps(report)


@pytest.mark.parametrize("outcome", ["Withheld", "Failed"])
def test_terminal_skill_without_artifact_records_outcome_without_publication(tmp_path, outcome):
    state = TutorialState.create(tmp_path / "state.json", settings(), "skills")
    state.data["run_id"] = "original-run"
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"outcome": outcome, "reason": "fixture"})

    async def run():
        async with http_client(settings(), transport=httpx.MockTransport(handle)) as http:
            await skills.run_command(http, "inspect", state)

    with pytest.raises(RuntimeError, match="No skill to publish"):
        asyncio.run(run())
    assert state.data["run"]["outcome"] == outcome
    assert "skill_id" not in state.data
    assert calls == [("GET", "/v1/skills/runs/original-run")]


def test_skill_timeout_resumes_same_job_without_second_generation(tmp_path, monkeypatch):
    state = TutorialState.create(tmp_path / "state.json", settings(), "skills")
    state.data["run_id"] = "original-run"
    ready = False
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"outcome": "Withheld"} if ready else {"status": "running"})

    async def bounded_poll(fetch):
        return await helpers.poll_skill_run(fetch, timeout=0.005, interval=0.001)

    monkeypatch.setattr(skills, "poll_skill_run", bounded_poll)

    async def inspect():
        async with http_client(settings(), transport=httpx.MockTransport(handle)) as http:
            await skills.run_command(http, "inspect", state)

    with pytest.raises(TimeoutError):
        asyncio.run(inspect())
    assert state.data["run_id"] == "original-run"
    ready = True
    with pytest.raises(RuntimeError, match="No skill to publish"):
        asyncio.run(inspect())
    assert state.data["run"]["outcome"] == "Withheld"
    assert set(calls) == {("GET", "/v1/skills/runs/original-run")}


@pytest.mark.parametrize("interrupted_record", [False, True])
def test_cleanup_requires_terminal_run_and_every_returned_skill_id(
    tmp_path, monkeypatch, interrupted_record
):
    config = settings()
    state = TutorialState.create(tmp_path / "state.json", config, "skills")
    state.data["run_id"] = "still-running"
    if interrupted_record:
        state.data["run"] = {"outcome": "Created", "skillId": "returned-but-unrecorded"}
    state.save()
    calls = []
    monkeypatch.setattr("neo4j_agent_memory.NamsSettings", lambda: config)
    monkeypatch.setattr(
        skills,
        "http_client",
        lambda selected: http_client(
            selected, transport=httpx.MockTransport(lambda request: calls.append(request))
        ),
    )
    message = "not fully recorded" if interrupted_record else "timeout does not cancel"
    with pytest.raises(RuntimeError, match=message):
        asyncio.run(skills.main(["cleanup", "--state", str(state.path)]))
    assert calls == []


@pytest.mark.parametrize("retained", [False, True])
def test_new_run_requires_the_actual_cleanup_disposition(tmp_path, retained):
    from tests.docs.test_hosted_tutorial_cleanup import Service as CleanupService
    from tests.docs.test_hosted_tutorial_cleanup import ledger

    state = ledger(tmp_path)
    service = CleanupService(state)
    if retained:
        service.provenance[0]["neighbors"].append("foreign-id")
        service.provenance[0]["neighbor_count"] = 2
    asyncio.run(service.run(state))
    assert state.data["cleanup_complete"] is (not retained)
    target = tmp_path / "next-run" / "state.json"
    if retained:
        with pytest.raises(RuntimeError, match="No automatic new run"):
            check_new_run(state.path, target)
    else:
        assert check_new_run(state.path, target) == "recorded_disposition_complete"


ASSEMBLIES = {
    "nams_quickstart.py": ["hosted_tutorial_state.py", "hosted_tutorial_cleanup.py"],
    "ontology_quickstart.py": ["hosted_tutorial_state.py", "hosted_tutorial_helpers.py"],
    "skills_quickstart.py": [
        "hosted_tutorial_state.py",
        "hosted_tutorial_helpers.py",
        "hosted_tutorial_cleanup.py",
    ],
}


@pytest.mark.parametrize("program", ASSEMBLIES)
def test_copied_files_compile_and_load_cli_without_credentials_or_state(tmp_path, program):
    files = [*ASSEMBLIES[program], program]
    for filename in files:
        (tmp_path / filename).write_text((EXAMPLES / filename).read_text())
    for arguments in (["-m", "py_compile", *files], [program, "--help"]):
        result = subprocess.run(
            [sys.executable, *arguments], cwd=tmp_path, env={}, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert "--state" in result.stdout
    assert not (tmp_path / ".tutorial-state").exists()


@pytest.mark.parametrize("failure", ["missing", "truncated"])
def test_assembly_checkpoint_rejects_missing_or_truncated_helper_before_seed(tmp_path, failure):
    program = "nams_quickstart.py"
    files = [*ASSEMBLIES[program], program]
    for filename in files:
        (tmp_path / filename).write_text((EXAMPLES / filename).read_text())
    helper = tmp_path / "hosted_tutorial_cleanup.py"
    if failure == "missing":
        helper.unlink()
    else:
        helper.write_text("async def cleanup(")
    for arguments in (["-m", "py_compile", *files], [program, "--help"]):
        result = subprocess.run(
            [sys.executable, *arguments], cwd=tmp_path, env={}, capture_output=True, text=True
        )
        assert result.returncode != 0
    assert not (tmp_path / ".tutorial-state").exists()
