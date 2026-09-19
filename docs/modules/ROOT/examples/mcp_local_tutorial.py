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


def check_desktop_config(path):
    """Validate the final merged file locally, without printing its contents."""

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    "Duplicate JSON object key; merge entries without duplicating keys"
                )
            result[key] = value
        return result

    try:
        config = json.loads(Path(path).read_text(), object_pairs_hook=unique_keys)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON at line {error.lineno}, column {error.colno}") from None
    except (OSError, UnicodeError):
        raise ValueError("Cannot read the Desktop configuration as UTF-8 text") from None
    try:
        entry = config["mcpServers"]["neo4j-docs"]
        env = entry["env"]
        required = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")
        if any(not isinstance(env.get(key), str) or not env[key].strip() for key in required):
            raise ValueError("The neo4j-docs env must contain all four nonempty NEO4J_* settings")
        if not env["NEO4J_URI"].startswith("neo4j+s://"):
            raise ValueError("The neo4j-docs NEO4J_URI must use neo4j+s://")
        if entry["command"] != sys.executable or entry["args"] != [str(Path(__file__).resolve())]:
            raise ValueError(
                "Regenerate the entry using this lesson's interpreter and script paths"
            )
    except (KeyError, TypeError, AttributeError):
        raise ValueError("Expected mcpServers -> neo4j-docs -> command, args and env") from None
    print("Verified: merged Desktop JSON, lesson paths and explicit Aura variables")


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--config", action="store_true")
    action.add_argument("--check-config", type=Path, metavar="PATH")
    args = parser.parse_args()
    if args.check_config:
        try:
            check_desktop_config(args.check_config)
        except ValueError as error:
            print(f"Desktop configuration check failed: {error}", file=sys.stderr)
            raise SystemExit(1) from None
    elif args.prepare:
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
