"""Activate and inspect a strict revision, then restore the exact prior binding."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from hosted_tutorial_helpers import (
    read_active_binding,
    recover_ontology,
    temporary_strict_ontology,
    verify_ontology_restoration,
)
from hosted_tutorial_state import TutorialState, http_client


async def exercise(client, state, *, read_active):
    async with temporary_strict_ontology(
        client.ontology, "healthcare", state, read_active=read_active
    ) as strict:
        labels = [item.label for item in strict.document.entity_types]
        print(f"Strict schema entity labels: {', '.join(labels)}")
        print("Verified: exact active revision, strict mode, and schema readback")


async def main(argv=None):
    from neo4j_agent_memory import NamsSettings, connect

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["seed", "inspect", "verify", "recover", "cleanup"])
    parser.add_argument("--state", type=Path, default=Path(".tutorial-state/ontology.json"))
    parser.add_argument(
        "--workspace-label", help="Operator-recorded workspace name/ID; not routing"
    )
    parser.add_argument("--workspace-owner", help="Operator responsible for resource disposition")
    args = parser.parse_args(argv)
    settings = NamsSettings()
    state = (
        TutorialState.create(
            args.state,
            settings,
            "ontology",
            workspace_label=args.workspace_label,
            workspace_owner=args.workspace_owner,
        )
        if args.command == "seed"
        else TutorialState.load(args.state, settings, "ontology")
    )
    if args.command == "inspect":
        print(json.dumps(state.inspect(), indent=2))
        return
    client = await connect(settings)
    try:
        async with http_client(settings) as http:

            async def read_active():
                return await read_active_binding(http)

            if args.command == "seed":
                await exercise(client, state, read_active=read_active)
            elif args.command == "verify":
                await verify_ontology_restoration(client.ontology, state, read_active=read_active)
            else:
                await recover_ontology(client.ontology, state, read_active=read_active)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
