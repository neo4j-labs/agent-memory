"""Persist, restart/read back, and clean up a run-owned hosted conversation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from hosted_tutorial_cleanup import cleanup
from hosted_tutorial_state import TutorialState, http_client, remember_message, verify_messages

STATE = Path(".tutorial-state/nams.json")


async def exercise(client, state):
    name = "docs-nams-" + state.data["run_token"]
    metadata = {"tutorialRun": state.data["run_token"], "tutorialLesson": "nams"}
    state.begin("create conversation " + name)
    conversation = await client.short_term.create_conversation(name, metadata=metadata)
    conversation_id = str(conversation.id)
    state.data["conversation_id"] = conversation_id
    state.record("conversation", conversation_id, metadata=metadata)
    state.finish()
    print(f"Conversation ID: {conversation_id}")
    token = state.data["run_token"][:8]
    transcript = [
        {"role": "user", "content": f"Maya-{token} is preparing the Robotics-{token} workshop."},
        {
            "role": "assistant",
            "content": f"The Robotics-{token} checklist includes Sensor-{token}.",
        },
    ]
    state.begin("store fixture messages")
    stored = await client.short_term.bulk_add_messages(conversation_id, transcript)
    for message in stored:
        remember_message(state, message)
    state.finish()
    if len(stored) != len(transcript):
        raise RuntimeError("Message write did not return both records; inspect retained state")
    await verify_messages(client, state)
    settled = await client.long_term.wait_for_extraction(
        session_id=conversation_id, timeout=60.0, interval=1.0
    )
    if not settled:
        raise TimeoutError(f"Extraction still pending for conversation {conversation_id}")
    entities = await client.long_term.search_entities(f"Robotics-{token} workshop", limit=5)
    print(f"Extraction settled; workspace search returned {len(entities)} candidate(s)")
    # Search candidates do not establish this run's entity provenance or quality.
    state.data["seed_verified"] = True
    state.save()
    print(f"Retained state: {state.path}; run verify in a new process, then cleanup")
    return conversation_id


async def main():
    from neo4j_agent_memory import NamsSettings, connect

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["seed", "inspect", "verify", "cleanup"], nargs="?", default="seed"
    )
    parser.add_argument("--state", type=Path, default=STATE)
    args = parser.parse_args()
    settings = NamsSettings()
    state = (
        TutorialState.create(args.state, settings, "nams")
        if args.command == "seed"
        else TutorialState.load(args.state, settings, "nams")
    )
    if args.command == "inspect":
        print(json.dumps(state.inspect(), indent=2))
        return
    if args.command == "cleanup":
        async with http_client(settings) as http:
            complete = await cleanup(http, state)
        if not complete:
            raise SystemExit(2)
        return
    client = await connect(settings)
    try:
        if args.command == "seed":
            await exercise(client, state)
        else:
            await verify_messages(client, state)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
