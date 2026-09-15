"""Two-process AuraDB conversation persistence using a Strands session manager."""

import argparse
import os
from pathlib import Path
from uuid import uuid4

from aura_connection import aura_config

SESSION_FILE = Path("strands-tutorial-session.txt")
SENTINEL = "The workshop passphrase is cedar-lantern-47."


def main():
    from strands import Agent

    from neo4j_agent_memory import BoltSettings
    from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["record", "recall"])
    phase = parser.parse_args().phase
    settings = BoltSettings(
        neo4j=aura_config(),
        embedding="bedrock/amazon.titan-embed-text-v2:0",
        extraction={"extractor_type": "none"},
    )
    if phase == "record":
        if SESSION_FILE.exists():
            raise RuntimeError("A session file exists; use recall before starting another exercise")
        session_id = f"strands-docs-{uuid4().hex[:8]}"
        SESSION_FILE.write_text(session_id)
    else:
        session_id = SESSION_FILE.read_text().strip()
    with Neo4jSessionManager(session_id, settings=settings, extract_entities=False) as manager:
        agent = Agent(
            model=os.environ["BEDROCK_MODEL_ID"],
            session_manager=manager,
            system_prompt="Use the conversation history to answer. Do not invent a missing passphrase.",
        )
        restored_text = str(agent.messages)
        if phase == "recall":
            if SENTINEL not in restored_text:
                raise RuntimeError("The stored message was not restored; no recall claim verified")
            print("Verified: prior message restored before the model call")
        prompt = SENTINEL if phase == "record" else "What workshop passphrase did I give you?"
        response = agent(prompt)
        print(response)
        print(f"Session ID: {session_id}; phase={phase}")
    print("Session manager closed after persisting the turn")


if __name__ == "__main__":
    main()
