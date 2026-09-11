"""One NAMS API key per developer — created, listed, rotated, revoked.

Why this script exists
----------------------
Every teammate's editor needs its own credential for the shared workspace. A
single key pasted into four ``.mcp.json`` files cannot be revoked for one laptop
without breaking the other three, and the audit trail says "the team" rather
than "Bob's laptop". ``client.auth`` is the lifecycle API for that::

    await client.auth.list_api_keys(workspace_id)   # metadata only, no plaintext
    await client.auth.create_api_key(label)         # plaintext returned ONCE
    await client.auth.rotate_api_key(key_id)        # mint replacement, revoke old
    await client.auth.revoke_api_key(key_id)        # effective on the next request

Key management requires an **admin** key or a user token: a leaked workspace
data key cannot mint more keys. Keys are also owner-private — only the user who
created one can list, reveal, rotate or revoke it.

Safety
------
``create``, ``rotate`` and ``revoke`` are **dry-run by default**. Pass
``--confirm`` to actually call the service. Plaintext key values are printed to
stdout exactly once and never written to a file.

Usage
-----
::

    uv run python provision_keys.py list
    uv run python provision_keys.py create --label alice-laptop          # dry run
    uv run python provision_keys.py create --label alice-laptop --confirm
    uv run python provision_keys.py rotate --key-id ak_123 --confirm
    uv run python provision_keys.py revoke --key-id ak_123 --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

from _shared import connect_nams, load_env, redact

from neo4j_agent_memory.core.exceptions import (
    AuthenticationError,
    NotSupportedError,
    RateLimitError,
    TransportError,
)

#: Data-plane scopes a developer's editor needs: read memory, write memory.
#: NAMS groups scopes as ``memory`` / ``entities`` / ``reasoning`` / ``ontology``
#: / ``skills``; a workspace key carries the data-plane set and nothing else.
EDITOR_SCOPES = ["memory:read", "memory:write"]

#: ``client.auth.create_api_key()`` does not send a ``category``, and the
#: service defaults an un-categorised create to an **admin** key (account-wide,
#: all scopes, not bound to a workspace). For the per-developer editor key you
#: almost always want a *workspace* key instead: mint it from the dashboard, or
#: POST with ``category: "workspace"``. This is the one place that caveat lives.
WORKSPACE_KEY_NOTE = """\
Note: create_api_key() omits `category`, and NAMS defaults an un-categorised
create to an ADMIN key — account-wide, all scopes, not bound to a workspace.
For a per-developer editor credential you want a WORKSPACE key (data-plane
scopes, permanently bound to one workspace, cannot mint more keys). Create it
from the dashboard at https://memory.neo4jlabs.com, or directly:

    curl -X POST "$MEMORY_ENDPOINT/auth/api-keys" \\
      -H "Authorization: Bearer $MEMORY_ADMIN_KEY" \\
      -H "Content-Type: application/json" \\
      -d '{"label": "<label>", "category": "workspace", "workspaceId": "<ws_id>"}'

See https://neo4j.com/labs/agent-memory/reference/authentication for the two
key categories."""


def _workspace_id(args: argparse.Namespace) -> str | None:
    return args.workspace or os.environ.get("MEMORY_WORKSPACE_ID") or None


def _print_key_row(key: Any) -> None:
    """One metadata line per key. ``key.key`` is deliberately not printed here."""
    scopes = ", ".join(key.scopes) if key.scopes else "(default)"
    print(
        f"  {key.id}  label={key.label or '(none)'}  "
        f"workspace={key.workspace_id or '(unbound)'}  "
        f"scopes={scopes}  created={key.created_at or '?'}"
    )


async def cmd_list(client: Any, args: argparse.Namespace) -> int:
    workspace = _workspace_id(args)
    if not workspace:
        print(
            "list needs a workspace id: pass --workspace ws_… or set "
            "MEMORY_WORKSPACE_ID (see .env.example)."
        )
        return 1
    keys = await client.auth.list_api_keys(workspace)
    print(f"{len(keys)} key(s) in workspace {workspace}:")
    for key in keys:
        _print_key_row(key)
    if not keys:
        print("  (none — create one with: provision_keys.py create --label <name>)")
    print("\nPlaintext values are not listed; NAMS returns them on create/reveal only.")
    return 0


async def cmd_create(client: Any | None, args: argparse.Namespace) -> int:
    workspace = _workspace_id(args)
    label = args.label
    scopes = args.scopes.split(",") if args.scopes else EDITOR_SCOPES
    print(f"create: label={label!r} scopes={scopes} workspace={workspace or '(unbound)'}")
    if not args.confirm:
        print("\nDry run — nothing was created. Re-run with --confirm to mint the key.")
        print(f"\n{WORKSPACE_KEY_NOTE}")
        return 0

    key = await client.auth.create_api_key(label, scopes=scopes, workspace_id=workspace)
    print(f"\nCreated key {key.id} ({key.label or label}).")
    print("Plaintext value, shown exactly once — copy it into the developer's")
    print("environment (a password manager, or `export MEMORY_API_KEY=…`):\n")
    print(f"    {key.key or '<service returned no plaintext>'}\n")
    print("Do not paste it into .mcp.json; the config files in this example")
    print("reference ${MEMORY_API_KEY} instead, so the key stays out of git.")
    print(f"\n{WORKSPACE_KEY_NOTE}")
    return 0


async def cmd_rotate(client: Any | None, args: argparse.Namespace) -> int:
    print(f"rotate: key_id={args.key_id} (mints a replacement, revokes the old key)")
    if not args.confirm:
        print("\nDry run — nothing was rotated. Re-run with --confirm.")
        return 0
    key = await client.auth.rotate_api_key(args.key_id)
    print(f"\nRotated. New key id {key.id}; plaintext shown once:\n")
    print(f"    {key.key or '<service returned no plaintext>'}\n")
    print("Update that developer's environment now — the old key stops working.")
    return 0


async def cmd_revoke(client: Any | None, args: argparse.Namespace) -> int:
    print(f"revoke: key_id={args.key_id} (effective on the next request)")
    if not args.confirm:
        print("\nDry run — nothing was revoked. Re-run with --confirm.")
        return 0
    await client.auth.revoke_api_key(args.key_id)
    print("\nRevoked. The key is blocklisted; any editor still using it gets 401.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provision per-developer NAMS API keys for a shared workspace.",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace id (defaults to MEMORY_WORKSPACE_ID).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List the workspace's keys (metadata only).")

    create = sub.add_parser("create", help="Create one key for one developer.")
    create.add_argument("--label", required=True, help="e.g. alice-laptop")
    create.add_argument(
        "--scopes",
        default=None,
        help=f"Comma-separated scopes (default: {','.join(EDITOR_SCOPES)}).",
    )
    create.add_argument("--confirm", action="store_true", help="Actually create it.")

    rotate = sub.add_parser("rotate", help="Rotate a key (new value, old revoked).")
    rotate.add_argument("--key-id", required=True)
    rotate.add_argument("--confirm", action="store_true", help="Actually rotate it.")

    revoke = sub.add_parser("revoke", help="Revoke a key.")
    revoke.add_argument("--key-id", required=True)
    revoke.add_argument("--confirm", action="store_true", help="Actually revoke it.")
    return parser


async def main(argv: list[str] | None = None) -> int:
    load_env()
    args = build_parser().parse_args(argv)

    handlers = {
        "list": cmd_list,
        "create": cmd_create,
        "rotate": cmd_rotate,
        "revoke": cmd_revoke,
    }
    # A dry run describes the call it would make, so it neither needs a key nor
    # opens a connection. Only `list` and a --confirm-ed mutation do.
    if args.command != "list" and not args.confirm:
        return await handlers[args.command](None, args)

    print(f"Admin credential: MEMORY_API_KEY = {redact(os.environ.get('MEMORY_API_KEY'))}")
    client = await connect_nams()
    try:
        return await handlers[args.command](client, args)
    except NotSupportedError as exc:
        # `client.auth` on a bolt connection is a sentinel: bolt authenticates
        # to your own Neo4j with NEO4J_PASSWORD, there are no NAMS keys to mint.
        print(f"\nKey management is NAMS-only: {exc}")
        return 1
    except AuthenticationError as exc:
        print(
            f"\nRejected: {exc}\nKey management needs an ADMIN key (or a user "
            "token). A workspace data key cannot mint or manage keys."
        )
        return 1
    except (RateLimitError, TransportError) as exc:
        print(f"\nRequest failed: {exc}")
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
