"""Local stdio server backed by AuraDB for the macOS Claude Desktop tutorial.

Claude must invoke a storage tool; merely chatting does not store every turn.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from aura_connection import aura_config


def build_server():
    from neo4j_agent_memory import MemorySettings
    from neo4j_agent_memory.mcp.server import create_mcp_server

    settings = MemorySettings(
        backend="bolt",
        neo4j=aura_config(),
        embedding="BAAI/bge-small-en-v1.5",
        extraction={"extractor_type": "none"},
    )
    return create_mcp_server(settings, profile="core", auto_preferences=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--config", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        from neo4j_agent_memory.llm import from_provider

        embedding = from_provider("BAAI/bge-small-en-v1.5", kind="embedding")
        vector = asyncio.run(embedding.embed_one("MCP tutorial readiness"))
        if len(vector) != 384:
            raise RuntimeError("Unexpected local embedding dimension")
        print("Verified: local embedding model returned 384 dimensions")
    elif args.config:
        neo4j = aura_config()
        print(
            json.dumps(
                {
                    "mcpServers": {
                        "neo4j-docs": {
                            "command": sys.executable,
                            "args": [str(Path(__file__).resolve())],
                            "env": {
                                "NEO4J_URI": neo4j["uri"],
                                "NEO4J_USERNAME": neo4j["username"],
                                "NEO4J_PASSWORD": neo4j["password"],
                                "NEO4J_DATABASE": neo4j["database"],
                            },
                        }
                    }
                },
                indent=2,
            )
        )
    else:
        build_server().run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
