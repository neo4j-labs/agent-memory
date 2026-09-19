"""Distil a synthetic procedure; keep review, publication, and disposition explicit."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path
from urllib.parse import quote
from zipfile import ZipFile

from hosted_tutorial_cleanup import (
    PROVENANCE_QUERY,
    cleanup,
    exclusively_owned,
    query,
    request_json,
)
from hosted_tutorial_helpers import poll_skill_run
from hosted_tutorial_state import (
    TutorialState,
    digest,
    http_client,
    private_json,
    remember_message,
    verify_messages,
)

STATE = Path(".tutorial-state/skills.json")


def output_path(state, name):
    return state.path.parent / name


async def preflight(http):
    capabilities = await request_json(http, "GET", "skills/capabilities")
    if capabilities.get("distillation") is not True:
        raise RuntimeError("Skill distillation is unavailable; no fixture was seeded")
    return capabilities


async def seed(client, state):
    """The CLI establishes the empty-workspace/owner prerequisite before this call."""
    name = "docs-skill-" + state.data["run_token"]
    metadata = {"tutorialRun": state.data["run_token"], "tutorialLesson": "skills"}
    state.begin("create Skills fixture conversation")
    conversation = await client.short_term.create_conversation(name, metadata=metadata)
    conversation_id = str(conversation.id)
    state.data["conversation_id"] = conversation_id
    state.record("conversation", conversation_id, metadata=metadata)
    state.finish()
    state.begin("store Skills fixture messages")
    messages = await client.short_term.bulk_add_messages(
        conversation_id,
        [
            {
                "role": "user",
                "content": "Fixture procedure: look up the order, check the return policy, record a refund decision. All orders and actions below are simulated.",
            },
            {
                "role": "assistant",
                "content": "Each fixture uses a delivered order, a confirmed damaged item, and a return window of 30 days. Record the decision without issuing a payment.",
            },
        ],
    )
    for message in messages:
        remember_message(state, message)
    state.finish()
    if len(messages) != 2:
        raise RuntimeError("Expected two fixture messages; inspect the partial seed")
    for order in ("FIXTURE-104", "FIXTURE-105", "FIXTURE-106"):
        # NAMS start/complete_trace group steps locally; they create no durable trace ID.
        trace = await client.reasoning.start_trace(
            session_id=conversation_id, task=f"Simulated refund decision for {order}"
        )
        for tool, arguments, result in [
            ("lookup_order", {"order_id": order}, {"delivered": True, "days_since_delivery": 4}),
            (
                "check_return_policy",
                {"damage_confirmed": True, "return_window_days": 30},
                {"eligible": True},
            ),
            (
                "record_refund_decision",
                {"order_id": order},
                {"decision": "approve", "payment_issued": False},
            ),
        ]:
            state.begin(f"record step {order}/{tool}")
            step = await client.reasoning.add_step(
                trace.id, action=tool, observation=json.dumps(result)
            )
            state.record("step", str(step.id), conversation_id=conversation_id)
            state.finish()
            state.begin(f"record tool call for {step.id}")
            call = await client.reasoning.record_tool_call(
                step.id, tool_name=tool, arguments=arguments, result={**result, "simulated": True}
            )
            state.record("tool_call", str(call.id), step_id=str(step.id))
            state.finish()
        await client.reasoning.complete_trace(
            trace.id, outcome="Simulated decision recorded", success=True
        )
    await verify_messages(client, state)
    traces = await client.reasoning.get_session_traces(conversation_id)
    steps = [step for trace in traces for step in trace.steps]
    actual_steps = {str(step.id) for step in steps}
    actual_calls = {str(call.id) for step in steps for call in step.tool_calls}
    if actual_steps != set(state.data["resources"].get("step", {})) or len(actual_steps) != 9:
        raise RuntimeError("Step readback differs from the nine recorded fixture IDs")
    if actual_calls != set(state.data["resources"].get("tool_call", {})) or len(actual_calls) != 9:
        raise RuntimeError("Tool-call readback differs from the nine recorded fixture IDs")
    state.data["seed_verified"] = True
    state.save()
    print(f"Verified fixture: conversation {conversation_id}; 9 recorded steps and 9 tool calls")


def provenance_source_ids(value):
    """Accept explicit source-ID fields; fail closed on undocumented/empty shapes.

    The published provenance response is free-form JSON. This conservative
    inspector does not infer IDs from arbitrary strings or a grounding score.
    An unrecognized live shape needs a reviewed parser update before publication.
    """
    found = set()

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            for key, item in node.items():
                normalized = key.replace("_", "").lower()
                if normalized in {"sourceid", "sourcenodeid"}:
                    if not isinstance(item, str) or not item:
                        raise RuntimeError("Malformed source ID in provenance")
                    found.add(item)
                elif normalized in {"sourceids", "sourcenodeids"}:
                    if not isinstance(item, list) or not all(
                        isinstance(x, str) and x for x in item
                    ):
                        raise RuntimeError("Malformed source IDs in provenance")
                    found.update(item)
                elif normalized.startswith("source"):
                    raise RuntimeError(
                        "Unrecognized source field in provenance; review its schema before publishing"
                    )
                else:
                    visit(item)

    visit(value)
    if not found:
        raise RuntimeError(
            "No recognized source IDs in provenance; inspect response before publishing"
        )
    return found


async def inspect_provenance(http, state, skill_id):
    state.data["provenance_verified"] = False
    state.save()
    detail = await request_json(http, "GET", f"skills/{quote(skill_id, safe='')}")
    provenance = await request_json(
        http, "GET", f"skills/{quote(skill_id, safe='')}/explain-provenance"
    )
    report = {"skill": detail, "provenance": provenance}
    path = output_path(state, "skills-review.json")
    private_json(path, report)
    sources = provenance_source_ids(provenance)
    known = {
        key
        for kind in ("message", "step", "tool_call")
        for key in state.data["resources"].get(kind, {})
    }
    rows = await query(http, PROVENANCE_QUERY, {"message_ids": sorted(known)})
    for row in rows:
        if exclusively_owned(row, known, state.data["started_at"]):
            state.record("entity", row["id"], provenance=row, owned=False)
            known.add(row["id"])
    if not sources <= known:
        state.data["provenance_verified"] = False
        state.data["unexplained_sources"] = sorted(sources - known)
        state.save()
        raise RuntimeError("Provenance references unrecorded sources; publication is blocked")
    state.data["provenance_verified"] = True
    state.data["review_sha256"] = digest(json.dumps(report, sort_keys=True))
    state.data["provenance_source_ids"] = sorted(sources)
    state.save()
    print(f"Inspect {path} before running publish; generated name is only a naming hint")
    return report


async def run_command(http, command, state):
    data = state.data
    if command == "generate":
        if not data.get("seed_verified"):
            raise RuntimeError("Run seed successfully before generating")
        if data.get("run_id"):
            raise RuntimeError("A run already exists; inspect it instead of generating again")
        await preflight(http)
        state.begin("generate a workspace-scoped skill")
        run = await request_json(
            http,
            "POST",
            "skills/generate",
            json={"nameHint": "simulated-refund-decision", "scope": {"type": "workspace"}},
        )
        if not run.get("runId"):
            raise RuntimeError("Generation response is missing runId; retain uncertain outcome")
        data["run_id"] = run["runId"]
        state.record("skill_run", run["runId"])
        state.finish()
        print(f"Saved run ID: {run['runId']}; run inspect next")
        return
    if command == "inspect":
        run_id = data.get("run_id")
        if not run_id:
            raise RuntimeError(
                "No recorded run ID; inspect local state before any generation retry"
            )

        async def fetch():
            return await request_json(http, "GET", f"skills/runs/{quote(run_id, safe='')}")

        run = await poll_skill_run(fetch)
        data["run"] = run
        state.save()
        if run["outcome"] != "Created":
            raise RuntimeError(f"No skill to publish: {json.dumps(run)}")
        data["skill_id"] = run["skillId"]
        state.record("skill", run["skillId"])
        await inspect_provenance(http, state, run["skillId"])
        return
    skill_id = data["skill_id"]
    route = "skills/" + quote(skill_id, safe="")
    if command == "publish":
        if not data.get("provenance_verified") or data.get("published"):
            raise RuntimeError("Inspect provenance first; do not repeat publication")
        reviewed_hash = data["review_sha256"]
        await inspect_provenance(http, state, skill_id)
        if data["review_sha256"] != reviewed_hash:
            data["provenance_verified"] = False
            state.save()
            raise RuntimeError(
                "Skill or provenance changed; inspect the new report before publication"
            )
        state.begin("approve reviewed skill")
        response = await http.post(route + "/review", json={"decision": "approve"})
        response.raise_for_status()
        data["approved"] = True
        state.finish()
        state.begin("publish reviewed skill")
        response = await http.post(route + "/publish")
        response.raise_for_status()
        data["published"] = True
        state.finish()
        print(f"Published skill ID: {skill_id}")
    elif command == "download":
        if not data.get("published"):
            raise RuntimeError("Publish the reviewed skill first")
        verification = await request_json(http, "GET", route + "/verify")
        private_json(output_path(state, "skills-attestation.json"), verification)
        response = await http.get(route + "/download")
        response.raise_for_status()
        with ZipFile(io.BytesIO(response.content)) as archive:
            if not any(Path(name).name == "SKILL.md" for name in archive.namelist()):
                raise RuntimeError("Downloaded archive has no SKILL.md")
        path = output_path(state, "simulated-refund-skill.zip")
        # Private directory; create the artifact privately too, without extraction.
        import os

        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(response.content)
        print(f"Verified ZIP contains SKILL.md; saved {path}")
        print("Inspect skills-attestation.json; ZIP validation does not verify its signature")


async def main(argv=None):
    from neo4j_agent_memory import NamsSettings, connect

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["seed", "generate", "inspect", "publish", "download", "state", "cleanup"],
    )
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument(
        "--workspace-label", help="Operator-recorded workspace name/ID; not routing"
    )
    parser.add_argument(
        "--empty-workspace-confirmed",
        action="store_true",
        help="Owner verified this newly provisioned workspace is empty",
    )
    parser.add_argument(
        "--disposal-owner", help="Workspace owner/operator responsible for retained resources"
    )
    parser.add_argument(
        "--disposal-plan", help="Verified disposal procedure or explicitly agreed retention"
    )
    args = parser.parse_args(argv)
    settings = NamsSettings()
    if args.command == "seed":
        if not args.empty_workspace_confirmed or not args.disposal_owner or not args.disposal_plan:
            parser.error(
                "seed requires --empty-workspace-confirmed, --disposal-owner and --disposal-plan"
            )
        state = TutorialState.create(
            args.state,
            settings,
            "skills",
            workspace_label=args.workspace_label,
            workspace_owner=args.disposal_owner,
        )
        state.data["disposition_agreement"] = {
            "owner": args.disposal_owner,
            "procedure": args.disposal_plan,
            "owner_verified_empty_workspace": True,
        }
        state.save()
        async with http_client(settings) as http:
            state.data["capabilities"] = await preflight(http)
            state.save()
        client = await connect(settings)
        try:
            await seed(client, state)
        finally:
            await client.close()
        return
    state = TutorialState.load(args.state, settings, "skills")
    if args.command == "state":
        print(json.dumps(state.inspect(), indent=2))
        return
    async with http_client(settings) as http:
        if args.command == "cleanup":
            if state.data.get("run_id") and state.data.get("run", {}).get("outcome") not in {
                "Created",
                "Withheld",
                "Failed",
            }:
                raise RuntimeError(
                    "Skill run is not recorded terminal; inspect the same run or hand it to the "
                    "owner before cleanup. A client timeout does not cancel the service job."
                )
            if state.data.get("run", {}).get("outcome") == "Created":
                skill_id = state.data["run"].get("skillId")
                if (
                    not skill_id
                    or skill_id != state.data.get("skill_id")
                    or skill_id not in state.data["resources"].get("skill", {})
                ):
                    raise RuntimeError(
                        "Created skill ID is not fully recorded; inspect the same run before cleanup"
                    )
            complete = await cleanup(http, state)
            print("Operator disposition is still required for retained step/tool/run/skill records")
            if not complete:
                raise SystemExit(2)
        else:
            await run_command(http, args.command, state)


if __name__ == "__main__":
    asyncio.run(main())
