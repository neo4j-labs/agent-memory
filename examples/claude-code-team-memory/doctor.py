"""Diagnose the team-memory setup before you open an editor.

Run this first. It answers, in order, the five questions that account for almost
every "the MCP server shows no tools" report:

==  ======================================================================
1.  Is a ``nams_…`` key present, and does it look like a key?
2.  Do the MCP config files in this directory parse, and do they name the
    documented command / URL — with no key pasted into them?
3.  What tool surface will each server actually expose? (self-hosted
    ``core`` = 6, ``extended`` = 16, hosted NAMS = 47 scope-gated)
4.  Is the endpoint reachable with that key, and is the workspace header
    being sent where the deployment requires it?
5.  Has server-side extraction finished for the seeded conversation, and
    are its entities searchable?
==  ======================================================================

Checks 1-3 need no network. Run ``--configs-only`` to stop after them.

Usage
-----
::

    uv run python doctor.py
    uv run python doctor.py --configs-only
    uv run python doctor.py --conversation-id <id>
    uv run python doctor.py --check-installed      # also parse your editors' configs

Exit code is ``1`` if any check FAILs, ``0`` otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
from pathlib import Path
from typing import Any

from _shared import EXAMPLE_DIR, load_env, recall_conversation_id, redact

from neo4j_agent_memory.core.exceptions import (
    AuthenticationError,
    NotSupportedError,
    RateLimitError,
    TransportError,
)

#: The tool counts this example documents, asserted against the real registrar
#: rather than trusted. Hosted NAMS is a separate, larger surface — see README.
SELF_HOSTED_PROFILES = {"core": 6, "extended": 16}
HOSTED_NAMS_TOOL_COUNT = 47
HOSTED_NAMS_MCP_URL = "https://mcp.memory.neo4jlabs.com/mcp"

#: Config files shipped with the example. Each is a ``*.json.example`` so it can
#: be copied into place without a chance of a stray key being committed.
CONFIG_FILES = (
    ".mcp.json.example",
    "claude_desktop_config.json.example",
    "cursor_mcp.json.example",
)

#: A real NAMS key is ``nams_`` plus a long opaque tail. Placeholders in this
#: repo are all-``x``. Anything else matching this shape in a config file is a
#: pasted credential and fails the check.
KEY_SHAPE = re.compile(r"nams_[A-Za-z0-9_-]{12,}")

_FAILURES: list[str] = []


def check(name: str, ok: bool | None, detail: str) -> None:
    """Print one result row. ``ok=None`` means skipped/not applicable."""
    if ok is None:
        mark = "SKIP"
    elif ok:
        mark = "PASS"
    else:
        mark = "FAIL"
        _FAILURES.append(name)
    print(f"  [{mark}] {name}: {detail}")


def warn(name: str, detail: str) -> None:
    print(f"  [WARN] {name}: {detail}")


def is_placeholder_key(value: str) -> bool:
    """True for the ``nams_xxxx…`` placeholders this repo commits."""
    tail = value[len("nams_") :]
    return bool(tail) and set(tail.lower()) <= {"x"}


# ── 1. Environment ───────────────────────────────────────────────────


def check_environment() -> bool:
    """Run the environment checks; return True when no key is set at all.

    The distinction matters for ``--configs-only``: *no* key is that mode's
    normal state and is forgiven, whereas a key left at the ``nams_xxxx``
    placeholder means ``.env`` was copied and never edited — a real
    misconfiguration, so it still fails.
    """
    print("\n1. Environment")
    key = os.environ.get("MEMORY_API_KEY", "")
    if not key:
        check(
            "MEMORY_API_KEY",
            False,
            "not set — copy .env.example to .env and set it, or export it",
        )
    elif not key.startswith("nams_"):
        check("MEMORY_API_KEY", False, f"set but does not start with 'nams_' ({len(key)} chars)")
    elif is_placeholder_key(key):
        check("MEMORY_API_KEY", False, "still the nams_xxxx placeholder from .env.example")
    else:
        check("MEMORY_API_KEY", True, redact(key))

    endpoint = os.environ.get("MEMORY_ENDPOINT") or "https://memory.neo4jlabs.com/v1 (default)"
    check("MEMORY_ENDPOINT", True, endpoint)

    workspace = os.environ.get("MEMORY_WORKSPACE_ID")
    if workspace:
        check("MEMORY_WORKSPACE_ID", True, f"{workspace} → sent as the X-Workspace-Id header")
    else:
        warn(
            "MEMORY_WORKSPACE_ID",
            "unset. Production keys are workspace-bound so this is fine; "
            "header-scoped deployments (dev/staging) answer 403 without it",
        )
    return not key


# ── 2. Config files ──────────────────────────────────────────────────


def _describe_server(name: str, entry: Any) -> tuple[bool, str]:
    if not isinstance(entry, dict):
        return False, f"{name!r} is not an object"
    if "url" in entry:
        return True, f"{name!r} → remote {entry['url']}"
    command = entry.get("command")
    if not command:
        return False, f"{name!r} has neither 'command' nor 'url'"
    args = entry.get("args") or []
    return True, f"{name!r} → {command} {' '.join(str(a) for a in args[:6])}…"


def check_config_file(path: Path, *, show_servers: bool, committed: bool = True) -> None:
    """Validate one MCP config file.

    ``committed=True`` (the example's own ``*.json.example`` files) treats a
    pasted key as a failure — those files live in git. ``committed=False`` (your
    installed editor configs) only warns: Claude Desktop neither inherits your
    shell environment nor expands ``${VAR}``, so a literal value there is
    sometimes the only option. Either way the value itself is never printed.
    """
    if not path.exists():
        check(path.name, False, "missing")
        return
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        check(path.name, False, f"invalid JSON at line {exc.lineno}: {exc.msg}")
        return

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        check(path.name, False, "no non-empty 'mcpServers' object")
        return

    problems: list[str] = []
    descriptions: list[str] = []
    for name, entry in servers.items():
        ok, description = _describe_server(name, entry)
        (descriptions if ok else problems).append(description)

    pasted = [m for m in KEY_SHAPE.findall(raw) if not is_placeholder_key(m)]
    if pasted and committed:
        problems.append("a literal nams_ key is pasted into this file")

    if problems:
        check(path.name, False, "; ".join(problems))
        return
    suffix = "a literal nams_ key is present" if pasted else "no pasted key"
    if pasted:
        warn(path.name, f"valid JSON, {len(servers)} server(s), but {suffix} — keep it untracked")
    else:
        check(path.name, True, f"valid JSON, {len(servers)} server(s), {suffix}")
    if show_servers:
        for description in descriptions:
            print(f"         {description}")


def check_configs(*, check_installed: bool) -> None:
    print("\n2. MCP config files")
    for filename in CONFIG_FILES:
        check_config_file(EXAMPLE_DIR / filename, show_servers=True)

    if not check_installed:
        print("         (pass --check-installed to also parse your editors' live configs)")
        return

    print("\n   Installed editor configs (parsed, never printed):")
    for label, path in installed_config_paths().items():
        if path.exists():
            check_config_file(path, show_servers=False, committed=False)
        else:
            check(label, None, f"not present at {path}")


def installed_config_paths() -> dict[str, Path]:
    """Where each host keeps its MCP config on this platform."""
    home = Path.home()
    if platform.system() == "Darwin":
        desktop = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", str(home))
        desktop = Path(appdata) / "Claude" / "claude_desktop_config.json"
    else:
        desktop = home / ".config" / "Claude" / "claude_desktop_config.json"
    return {
        "claude_desktop_config.json": desktop,
        ".mcp.json (project)": Path.cwd() / ".mcp.json",
        ".cursor/mcp.json (project)": Path.cwd() / ".cursor" / "mcp.json",
    }


# ── 3. Tool surface ──────────────────────────────────────────────────


async def check_tool_surface() -> None:
    """Enumerate the self-hosted profiles from the real registrar.

    Imports ``register_tools`` and counts what it registers, so this example's
    "core = 6, extended = 16" claim cannot drift away from the library.
    """
    print("\n3. Tool surface")
    try:
        from fastmcp import FastMCP

        from neo4j_agent_memory.mcp._tools import register_tools
    except ImportError as exc:
        check(
            "self-hosted profiles",
            None,
            f"{exc} — install the MCP extra: uv pip install 'neo4j-agent-memory[mcp]'",
        )
        return

    for profile, documented in SELF_HOSTED_PROFILES.items():
        server: Any = FastMCP(name=f"doctor-{profile}")
        register_tools(server, profile=profile)
        names = sorted(tool.name for tool in await server.list_tools())
        check(
            f"self-hosted --profile {profile}",
            len(names) == documented,
            f"{len(names)} tool(s) (documented: {documented})",
        )
        print(f"         {', '.join(names)}")

    check(
        "hosted NAMS MCP server",
        True,
        f"{HOSTED_NAMS_TOOL_COUNT} scope-gated tools at {HOSTED_NAMS_MCP_URL} — "
        "a different, larger surface than the self-hosted profiles above; "
        "tools/list returns only what your key's scopes permit",
    )


# ── 4 & 5. Live checks ───────────────────────────────────────────────


async def check_connectivity(conversation_id: str | None) -> None:
    print("\n4. NAMS connectivity")
    if not os.environ.get("MEMORY_API_KEY"):
        check("endpoint reachable", None, "no MEMORY_API_KEY; skipping the live checks")
        return

    from neo4j_agent_memory import NamsSettings, connect

    settings = NamsSettings()
    try:
        # connect() makes one authenticated probe request (list_conversations),
        # so 401/403 and network failures surface here rather than later.
        client = await connect(settings)
    except AuthenticationError as exc:
        check(
            "endpoint reachable",
            False,
            f"authentication rejected: {exc}. If the deployment scopes by "
            "header, set MEMORY_WORKSPACE_ID",
        )
        return
    except TransportError as exc:
        check("endpoint reachable", False, f"network failure: {exc}")
        return

    try:
        check("endpoint reachable", True, f"{settings.nams.endpoint} (backend={client.backend})")
        conversations = await client.short_term.list_conversations(limit=5)
        check("workspace readable", True, f"{len(conversations)} recent conversation(s) visible")

        print("\n5. Seeded memory")
        if conversation_id is None:
            check(
                "extraction status",
                None,
                "no conversation id — run seed_workspace.py, then export "
                "TEAM_MEMORY_CONVERSATION_ID or pass --conversation-id",
            )
        else:
            status = await client.short_term.get_extraction_status(conversation_id)
            check(
                "extraction status",
                status.is_complete,
                f"pending={status.pending_count}, complete={status.is_complete}",
            )

        entities = await client.long_term.search_entities("architecture decision", limit=10)
        check(
            "entities searchable",
            bool(entities),
            f"{len(entities)} entity/entities — "
            + (", ".join(e.display_name for e in entities[:5]) or "none yet"),
        )

        try:
            rows = await client.query.cypher("MATCH (e:Entity) RETURN count(e) AS entities")
        except NotSupportedError as exc:
            check("read-only Cypher", None, f"not available on this deployment: {exc}")
        else:
            check("read-only Cypher", True, f"{rows}")
    except (AuthenticationError, RateLimitError, TransportError) as exc:
        check("live checks", False, f"{type(exc).__name__}: {exc}")
    finally:
        await client.close()


# ── Entry point ──────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose a team-memory MCP setup (key, configs, tools, connectivity).",
    )
    parser.add_argument(
        "--configs-only",
        action="store_true",
        help="Run only the offline checks (no network, no key required).",
    )
    parser.add_argument(
        "--conversation-id",
        default=None,
        help="Conversation to check extraction for (default: $TEAM_MEMORY_CONVERSATION_ID).",
    )
    parser.add_argument(
        "--check-installed",
        action="store_true",
        help="Also parse the MCP configs your editors have installed.",
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    load_env()
    args = build_parser().parse_args(argv)
    _FAILURES.clear()

    print("neo4j-agent-memory — team memory doctor")
    key_absent = check_environment()
    check_configs(check_installed=args.check_installed)
    await check_tool_surface()

    if args.configs_only:
        # Offline mode: having no key at all is the mode, not a failure. A key
        # left at the placeholder still fails — see check_environment().
        if key_absent:
            while "MEMORY_API_KEY" in _FAILURES:
                _FAILURES.remove("MEMORY_API_KEY")
        print("\n(--configs-only: skipped the live NAMS checks)")
    else:
        await check_connectivity(args.conversation_id or recall_conversation_id())

    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} check(s) failed: {', '.join(_FAILURES)}")
        return 1
    print("All checks passed. Open your editor and ask it what the team decided.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
