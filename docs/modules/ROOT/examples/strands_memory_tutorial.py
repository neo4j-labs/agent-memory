"""Two-process AuraDB conversation persistence using a Strands session manager."""

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

from aura_connection import aura_config

SESSION_FILE = Path("strands-tutorial-session.txt")
SENTINEL = "The workshop passphrase is cedar-lantern-47."


def inspect_session():
    """Read the saved session directly; never construct an agent or write memory."""
    from neo4j import GraphDatabase

    session_id = SESSION_FILE.read_text().strip()
    if not session_id:
        raise RuntimeError("The session file is empty; preserve it and follow recovery")
    config = aura_config()
    with GraphDatabase.driver(
        config["uri"], auth=(config["username"], config["password"])
    ) as driver:
        records, _, _ = driver.execute_query(
            "MATCH (c:Conversation {session_id: $session_id}) "
            "OPTIONAL MATCH (c)-[:HAS_MESSAGE]->(m:Message) "
            "RETURN count(DISTINCT c) AS conversations, "
            "collect(DISTINCT m { .id, .role, .content }) AS messages",
            session_id=session_id,
            database_=config["database"],
            routing_="r",
        )
    if len(records) != 1 or records[0]["conversations"] > 1:
        raise RuntimeError(
            "Ambiguous session; preserve the file and inspect the dedicated instance"
        )
    messages = records[0]["messages"]
    sentinel_stored = any(
        message["role"] == "user" and message["content"] == SENTINEL for message in messages
    )
    result = {
        "session_id": session_id,
        "message_ids": [message["id"] for message in messages],
        "message_count": len(messages),
        "sentinel_stored": sentinel_stored,
    }
    print(json.dumps(result, indent=2))
    print(
        "Next: recall may make another model call and store another turn."
        if sentinel_stored
        else "Not ready for recall. Preserve the session file and follow the recovery instructions."
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["record", "inspect", "recall"])
    phase = parser.parse_args().phase
    if phase == "inspect":
        inspect_session()
        return

    from strands import Agent

    from neo4j_agent_memory import BoltSettings
    from neo4j_agent_memory.integrations.strands import Neo4jSessionManager

    model_id = os.environ.get("BEDROCK_MODEL_ID", "").strip()
    if not model_id or model_id.startswith("replace-with-"):
        raise ValueError("Export an accessible BEDROCK_MODEL_ID before recording a session")
    settings = BoltSettings(
        neo4j=aura_config(),
        embedding="bedrock/amazon.titan-embed-text-v2:0",
        extraction={"extractor_type": "none"},
    )
    if phase == "record":
        if SESSION_FILE.exists():
            raise RuntimeError(
                "A session file exists; run inspect before choosing recall or recovery"
            )
        session_id = f"strands-docs-{uuid4().hex[:8]}"
        with SESSION_FILE.open("x") as saved:
            saved.write(session_id)
    else:
        session_id = SESSION_FILE.read_text().strip()
    with Neo4jSessionManager(session_id, settings=settings, extract_entities=False) as manager:
        agent = Agent(
            model=model_id,
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
