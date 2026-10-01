"""Failure-path contracts for the maintained Skills tutorial, without live writes."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "docs/modules/ROOT/examples"))
import skills_quickstart as skills  # noqa: E402
from hosted_tutorial_state import TutorialState  # noqa: E402

from neo4j_agent_memory import NamsSettings  # noqa: E402


def ledger(tmp_path):
    settings = NamsSettings(
        nams={"endpoint": "https://service.invalid/v1", "api_key": "nams_test"}, _env_file=None
    )
    state = TutorialState.create(tmp_path / "skills.json", settings, "skills")
    state.data["seed_verified"] = True
    state.record("step", "owned-step")
    return state


def invoke(state, command, handle):
    async def run():
        async with httpx.AsyncClient(
            base_url="https://service.invalid/v1/", transport=httpx.MockTransport(handle)
        ) as http:
            await skills.run_command(http, command, state)

    return asyncio.run(run())


def test_generate_payload_matches_captured_service_definition(tmp_path):
    schema = json.loads(
        (ROOT / "tests/fixtures/nams/skill-generate-request-schema.json").read_text()
    )["schema"]
    state = ledger(tmp_path)
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(200, json={"distillation": True})
        body = json.loads(request.content)
        assert set(body) <= set(schema["properties"])
        assert body["nameHint"] == "simulated-refund-decision"
        assert "name" not in body
        return httpx.Response(202, json={"runId": "run-id"})

    invoke(state, "generate", handle)
    assert state.data["run_id"] == "run-id"
    assert state.data["resources"]["skill_run"]["run-id"]["status"] == "retained"
    with pytest.raises(RuntimeError, match="already exists"):
        invoke(state, "generate", handle)
    assert len(calls) == 2


def test_disabled_distillation_stops_before_generation(tmp_path):
    state = ledger(tmp_path)
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={"distillation": False})

    with pytest.raises(RuntimeError, match="unavailable"):
        invoke(state, "generate", handle)
    assert calls == ["GET"]
    assert state.data["pending"] is None


def test_seed_cli_requires_owner_disposition_before_creating_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr(sys, "argv", ["skills_quickstart.py", "seed", "--state", str(path)])
    with pytest.raises(SystemExit) as exc:
        asyncio.run(skills.main())
    assert exc.value.code == 2
    assert not path.exists()


def test_missing_generation_id_preserves_uncertain_outcome(tmp_path):
    state = ledger(tmp_path)

    def handle(request):
        return httpx.Response(200, json={"distillation": True} if request.method == "GET" else {})

    with pytest.raises(RuntimeError, match="missing runId"):
        invoke(state, "generate", handle)
    assert state.data["pending"]
    with pytest.raises(RuntimeError, match="Unresolved write"):
        invoke(state, "generate", handle)


@pytest.mark.parametrize(
    "provenance",
    [
        {"claims": [{"sourceNodeIds": ["foreign-step"]}]},
        {"claims": [{"sourceNodeIds": ["owned-step", "foreign-step"]}]},
        {"groundingScore": 1.0},
    ],
)
def test_unexplained_or_unrecognized_provenance_cannot_publish(tmp_path, provenance):
    state = ledger(tmp_path)
    state.data["run_id"] = "run-id"
    writes = []

    def handle(request):
        if request.method != "GET" and not request.url.path.endswith("/query"):
            writes.append(request.url.path)
        if "/runs/" in request.url.path:
            return httpx.Response(200, json={"outcome": "Created", "skillId": "skill-id"})
        if request.url.path.endswith("/explain-provenance"):
            return httpx.Response(200, json=provenance)
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"rows": []})
        return httpx.Response(200, json={"name": "a-generated-name"})

    with pytest.raises(RuntimeError, match="provenance|Provenance"):
        invoke(state, "inspect", handle)
    with pytest.raises(RuntimeError, match="Inspect provenance"):
        invoke(state, "publish", handle)
    assert writes == []
    assert (tmp_path / "skills-review.json").exists()


def test_changed_review_blocks_publication_until_another_inspection(tmp_path):
    state = ledger(tmp_path)
    state.data.update(
        run_id="run-id",
        skill_id="skill-id",
        provenance_verified=True,
        review_sha256="previous-review-hash",
    )
    writes = []

    def handle(request):
        if request.url.path.endswith("/explain-provenance"):
            return httpx.Response(200, json={"sourceNodeIds": ["owned-step"]})
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"rows": []})
        if request.method != "GET":
            writes.append(request.url.path)
        return httpx.Response(200, json={"name": "changed-name"})

    with pytest.raises(RuntimeError, match="changed"):
        invoke(state, "publish", handle)
    assert not state.data["provenance_verified"]
    assert writes == []


def test_partial_seed_keeps_real_step_id_and_pending_tool_call(tmp_path):
    state = ledger(tmp_path)
    state.data["resources"] = {}

    async def create(_, **kwargs):
        return SimpleNamespace(id="conversation-id")

    async def messages(_conversation, transcript):
        return [
            SimpleNamespace(id=f"message-{i}", **message) for i, message in enumerate(transcript)
        ]

    async def trace(**_):
        return SimpleNamespace(id="local-group")

    async def step(*_, **__):
        return SimpleNamespace(id="returned-step")

    async def call(*_, **__):
        raise ConnectionError("uncertain tool-call result")

    client = SimpleNamespace(
        short_term=SimpleNamespace(create_conversation=create, bulk_add_messages=messages),
        reasoning=SimpleNamespace(start_trace=trace, add_step=step, record_tool_call=call),
    )
    with pytest.raises(ConnectionError):
        asyncio.run(skills.seed(client, state))
    saved = json.loads(state.path.read_text())
    assert saved["conversation_id"] == "conversation-id"
    assert "returned-step" in saved["resources"]["step"]
    assert saved["pending"] == "record tool call for returned-step"
    assert "local-group" not in json.dumps(saved["resources"])


def test_unsigned_verification_is_not_a_signature_pass(tmp_path, capsys):
    import io
    from zipfile import ZipFile

    state = ledger(tmp_path)
    state.data.update(skill_id="skill-id", published=True)
    data = io.BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("SKILL.md", "# Synthetic procedure")
        archive.writestr("../not-extracted.txt", "fixture")

    def handle(request):
        if request.url.path.endswith("/download"):
            return httpx.Response(200, content=data.getvalue())
        return httpx.Response(200, json={"signed": False, "attestationConfigured": False})

    invoke(state, "download", handle)
    assert "does not verify its signature" in capsys.readouterr().out
    assert not (tmp_path.parent / "not-extracted.txt").exists()
    assert json.loads((tmp_path / "skills-attestation.json").read_text())["signed"] is False


@pytest.mark.parametrize(
    "provenance",
    [
        {"sources": [{"sourceId": "owned-step"}, {"id": "foreign-step"}]},
        {"sourceNodeIds": ["owned-step"], "sourceNodes": [{"id": "foreign-step"}]},
        {"claims": [{"sourceNodeIds": ["owned-step"], "sourceRefs": ["foreign-step"]}]},
        {"sourceNodeIds": ["owned-step"], "sourceMetadata": {"id": "foreign-step"}},
    ],
)
def test_publish_rejects_mixed_unknown_source_shapes_and_clears_prior_verification(
    tmp_path, provenance
):
    """An earlier approval flag/hash cannot turn partial parsing into a publish pass."""
    from hosted_tutorial_state import digest

    state = ledger(tmp_path)
    detail = {"name": "fixture-skill"}
    state.data.update(
        skill_id="skill-id",
        provenance_verified=True,
        review_sha256=digest(
            json.dumps({"skill": detail, "provenance": provenance}, sort_keys=True)
        ),
    )
    state.save()
    writes = []

    def handle(request):
        if request.url.path.endswith("/explain-provenance"):
            return httpx.Response(200, json=provenance)
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"rows": []})
        if request.method != "GET":
            writes.append(request.url.path)
        return httpx.Response(200, json=detail)

    with pytest.raises(RuntimeError, match="(?i)(source|provenance)"):
        invoke(state, "publish", handle)
    assert writes == []
    assert state.data["provenance_verified"] is False
    assert json.loads(state.path.read_text())["provenance_verified"] is False
    assert not state.data.get("published")


@pytest.mark.parametrize("failure", ["unknown-provenance", "detail-http-failure"])
def test_failed_reinspection_clears_verification_before_reading_new_evidence(tmp_path, failure):
    state = ledger(tmp_path)
    state.data.update(run_id="run-id", provenance_verified=True, review_sha256="old-review")
    state.save()
    writes = []

    def handle(request):
        if request.method != "GET":
            writes.append(request.url.path)
        if "/runs/" in request.url.path:
            return httpx.Response(200, json={"outcome": "Created", "skillId": "skill-id"})
        if request.url.path.endswith("/explain-provenance"):
            return httpx.Response(200, json={"groundingScore": 1.0})
        if failure == "detail-http-failure":
            return httpx.Response(503, json={"error": "deliberate offline failure"})
        return httpx.Response(200, json={"name": "fixture-skill"})

    with pytest.raises((RuntimeError, httpx.HTTPStatusError)):
        invoke(state, "inspect", handle)
    assert writes == []
    assert state.data["provenance_verified"] is False
    assert json.loads(state.path.read_text())["provenance_verified"] is False
    assert not state.data.get("published")


@pytest.mark.parametrize("unidentified", [None, "source", "neighbor"])
def test_derived_skill_sources_require_complete_provenance_ids(tmp_path, unidentified):
    from datetime import datetime, timezone

    state = ledger(tmp_path)
    state.data["run_id"] = "run-id"
    row = {
        "id": "derived-entity",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sources": ["owned-step"],
        "source_count": 1,
        "neighbors": ["owned-step"],
        "neighbor_count": 1,
    }
    if unidentified:
        row[f"{unidentified}_count"] = 2
    writes = []

    def handle(request):
        if "/runs/" in request.url.path:
            return httpx.Response(200, json={"outcome": "Created", "skillId": "skill-id"})
        if request.url.path.endswith("/explain-provenance"):
            return httpx.Response(200, json={"sourceNodeIds": ["derived-entity"]})
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"rows": [row]})
        if request.method != "GET":
            writes.append(request.url.path)
        return httpx.Response(200, json={"name": "fixture-skill"})

    if unidentified:
        with pytest.raises(RuntimeError, match="unrecorded sources"):
            invoke(state, "inspect", handle)
        with pytest.raises(RuntimeError, match="Inspect provenance"):
            invoke(state, "publish", handle)
        assert state.data["provenance_verified"] is False
        assert writes == []
    else:
        invoke(state, "inspect", handle)
        invoke(state, "publish", handle)
        assert state.data["published"] is True
        assert writes == ["/v1/skills/skill-id/review", "/v1/skills/skill-id/publish"]
