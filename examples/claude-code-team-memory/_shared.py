"""Shared helpers for the three scripts in this example.

Kept here so ``doctor.py``, ``provision_keys.py`` and ``seed_workspace.py``
agree on environment loading, key redaction and connection handling. The
scripts import it as ``_shared`` — Python puts a script's own directory on
``sys.path``, so no packaging is needed.

Nothing in here touches a Neo4j URI: this example is configuration for the
hosted NAMS backend and for the self-hosted MCP server, and the only credential
it handles is a ``nams_…`` API key that stays in the environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

EXAMPLE_DIR = Path(__file__).resolve().parent

#: The conversation every script in this example shares. Override with
#: ``TEAM_MEMORY_CONVERSATION`` if your workspace already uses the name.
CONVERSATION_NAME = os.environ.get("TEAM_MEMORY_CONVERSATION", "team-decisions")


def load_env(*, quiet: bool = False) -> None:
    """Load this directory's ``.env`` so the documented setup step has an effect.

    ``python-dotenv`` is used when installed; otherwise a four-line parser
    handles the ``KEY=value`` shape ``.env.example`` documents. Existing
    environment variables always win, so ``MEMORY_API_KEY=… python doctor.py``
    overrides the file.
    """
    env_file = EXAMPLE_DIR / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    else:
        load_dotenv(env_file)
    if not quiet:
        print(f"Loaded environment from {env_file}")


def redact(secret: str | None) -> str:
    """Render a credential safely: prefix and length only, never characters.

    Every script prints keys through this. The offline smoke test greps the
    whole directory for anything matching a real ``nams_<base62>`` key shape,
    so a redaction regression fails the build rather than leaking in a demo
    recording.
    """
    if not secret:
        return "<unset>"
    prefix = "nams_" if secret.startswith("nams_") else "<unknown-prefix>"
    return f"{prefix}… ({len(secret)} chars, value redacted)"


def require_api_key() -> str:
    """Return ``MEMORY_API_KEY`` or exit with the signup instructions."""
    key = os.environ.get("MEMORY_API_KEY", "")
    if not key:
        raise SystemExit(
            "MEMORY_API_KEY is not set.\n"
            "  1. Get a key at https://memory.neo4jlabs.com\n"
            "  2. cp .env.example .env and set MEMORY_API_KEY=nams_…\n"
            "Nothing in this example ever writes a key to disk."
        )
    return key


async def connect_nams() -> Any:
    """Connect to NAMS from the environment and return the connected client.

    ``NamsSettings()`` reads ``MEMORY_API_KEY``, ``MEMORY_ENDPOINT`` and
    ``MEMORY_WORKSPACE_ID``. ``connect()`` hands back an already-connected,
    NAMS-typed client — the caller owns ``await client.close()``.
    """
    from neo4j_agent_memory import NamsSettings, connect

    require_api_key()
    return await connect(NamsSettings())


def recall_conversation_id() -> str | None:
    """The conversation id ``seed_workspace.py`` printed, if it was exported.

    Nothing is written to disk: ``seed_workspace.py`` ends by printing an
    ``export TEAM_MEMORY_CONVERSATION_ID=…`` line, and ``doctor.py`` reads that
    variable (or takes ``--conversation-id``). Keeping ids out of the working
    tree means a live run leaves no untracked files behind.
    """
    return os.environ.get("TEAM_MEMORY_CONVERSATION_ID") or None


__all__ = [
    "CONVERSATION_NAME",
    "EXAMPLE_DIR",
    "connect_nams",
    "load_env",
    "recall_conversation_id",
    "redact",
    "require_api_key",
]
