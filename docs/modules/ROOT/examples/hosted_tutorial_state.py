"""Private, restartable state for the hosted REST tutorials; no I/O at import."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


def read_state(path):
    """Read a local ledger without credentials, SDK imports, or service access."""
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Refusing a symlink for tutorial state")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RuntimeError("Unsupported or incomplete tutorial state")
    if not isinstance(data.get("identity"), dict):
        raise RuntimeError("Missing tutorial identity")
    if (
        not isinstance(data.get("resources"), dict)
        or not data.get("run_token")
        or "pending" not in data
    ):
        raise RuntimeError("Incomplete tutorial state")
    if any(not isinstance(entries, dict) for entries in data["resources"].values()):
        raise RuntimeError("Invalid resource ledger")
    if any(
        not isinstance(entry, dict)
        for entries in data["resources"].values()
        for entry in entries.values()
    ):
        raise RuntimeError("Invalid resource disposition")
    datetime.fromisoformat(data["started_at"])
    return data


def new_run_status(data):
    """Classify recorded evidence only; never infer absence from an empty ledger."""
    if data.get("pending"):
        return "needs_reconciliation"
    resources = data["resources"]
    entries = [entry for group in resources.values() for entry in group.values()]
    if (
        data.get("remote_write_started") is False
        and not entries
        and not any(data.get(key) for key in ("conversation_id", "run_id", "skill_id"))
        and not data.get("ontology", {}).get("clone_id")
    ):
        return "no_remote_write_started"
    disposed = data.get("cleanup_complete") is True or (
        data.get("ontology", {}).get("status") == "complete"
        and data["ontology"].get("restored") is True
        and data["ontology"].get("deleted") is True
    )
    if (
        disposed
        and entries
        and all(entry.get("status") in {"deleted", "deleted_with_ontology"} for entry in entries)
    ):
        return "recorded_disposition_complete"
    return "owner_disposition_required"


def inspect_file(path):
    """Return a redacted local summary; this never authenticates or enables writes."""
    data = read_state(path)
    endpoint = urlsplit(str(data["identity"].get("endpoint", "")))
    # Do not expose credentials/query parameters even from a manually altered file.
    endpoint = urlunsplit(
        (endpoint.scheme, endpoint.netloc.rsplit("@", 1)[-1], endpoint.path, "", "")
    )
    return {
        "local_only": True,
        "service_checked": False,
        "lesson": data.get("lesson"),
        "run_token": data["run_token"],
        "started_at": data["started_at"],
        "identity": {
            "endpoint": endpoint,
            "workspace_id": data["identity"].get("workspace_id"),
        },
        "operator_context": data.get("operator_context", {}),
        "pending": data.get("pending"),
        "new_run_status": new_run_status(data),
        "resources": {
            kind: {
                resource_id: {
                    key: entry[key] for key in ("status", "owned", "disposition") if key in entry
                }
                for resource_id, entry in entries.items()
            }
            for kind, entries in data["resources"].items()
        },
    }


def check_new_run(path, next_state):
    """Permit a separate path only after no-write proof or recorded full disposal."""
    status = new_run_status(read_state(path))
    if status not in {"no_remote_write_started", "recorded_disposition_complete"}:
        raise RuntimeError(
            "No automatic new run: uncertain, retained, or legacy state needs owner disposition"
        )
    next_state = Path(next_state)
    if next_state.exists() or next_state.is_symlink():
        raise RuntimeError("Choose an unused state path; the previous run must remain intact")
    if next_state.parent.resolve() == Path(path).parent.resolve():
        raise RuntimeError("Use a separate run directory so reports and archives cannot collide")
    if next_state.parent.exists() and any(next_state.parent.iterdir()):
        raise RuntimeError("Choose a new or empty run directory; preserve its existing reports")
    return status


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def connection(settings):
    """Use the same resolved NamsSettings for SDK and tutorial HTTP requests."""
    config = settings.nams
    parts = urlsplit(str(config.endpoint).rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("A hosted HTTP(S) endpoint is required")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Endpoint must not contain credentials, a query, or a fragment")
    if config.transport_mode == "bridge" or (
        config.transport_mode == "auto" and not re.search(r"/v\d+(?:/|$)", parts.path)
    ):
        raise ValueError("These tutorials require the hosted REST API")
    endpoint = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    key = config.api_key.get_secret_value() if config.api_key else ""
    if not key:
        raise ValueError("Set MEMORY_API_KEY before starting the tutorial")
    # Tutorial identity must not differ between the SDK and direct HTTP calls.
    headers = dict(config.headers)
    if any(k.lower() in {"authorization", "x-workspace-id"} for k in headers):
        raise ValueError("Use api_key/workspace_id settings, not overriding auth/workspace headers")
    headers["Authorization"] = f"Bearer {key}"
    if config.workspace_id:
        headers["X-Workspace-Id"] = config.workspace_id
    resolved = {"endpoint": endpoint, "workspace_id": config.workspace_id}
    return resolved, headers, config.timeout, key


def credential_digest(key: str, salt: str) -> str:
    """Bind state to the credential that seeded it, without keeping a directly
    comparable digest of that credential on disk. A per-run salt and a
    deliberately slow KDF mean the stored value answers one question -- "is this
    the same key as last time?" -- and is not worth attacking offline. Message
    content keeps ``digest``: it is a change detector, not a secret."""
    return hashlib.scrypt(
        key.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32
    ).hex()


def identity(settings, salt=None):
    resolved, _headers, _timeout, key = connection(settings)
    salt = salt or os.urandom(16).hex()
    return {**resolved, "credential_salt": salt, "credential_digest": credential_digest(key, salt)}


def http_client(settings, **kwargs):
    import httpx

    resolved, headers, timeout, _key = connection(settings)
    return httpx.AsyncClient(
        base_url=resolved["endpoint"] + "/", headers=headers, timeout=timeout, **kwargs
    )


def private_json(path: Path, data, *, exclusive=False):
    """Write without exposing partial JSON or replacing a previous exercise."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RuntimeError("Refusing a symlink for tutorial state")
    content = json.dumps(data, indent=2, allow_nan=False) + "\n"
    if exclusive:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise RuntimeError("State already exists; inspect or recover the prior run") from exc
        with os.fdopen(fd, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        return
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class TutorialState:
    def __init__(self, path, data):
        self.path = Path(path)
        self.data = data

    @classmethod
    def create(cls, path, settings, lesson, *, workspace_label=None, workspace_owner=None):
        data = {
            "schema_version": 1,
            "lesson": lesson,
            "identity": identity(settings),
            "run_token": uuid4().hex,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "resources": {},
            "pending": None,
            # Written durably by begin() before the first remote mutation.
            # Missing in an older ledger means unknown, never proof of no writes.
            "remote_write_started": False,
            "operator_context": {
                "workspace_label": workspace_label,
                "workspace_owner": workspace_owner,
            },
        }
        private_json(Path(path), data, exclusive=True)
        return cls(path, data)

    @classmethod
    def load(cls, path, settings, lesson=None):
        path = Path(path)
        data = read_state(path)
        stored = data["identity"]
        salt = stored.get("credential_salt")
        if not isinstance(salt, str) or not re.fullmatch(r"[0-9a-f]{32}", salt):
            raise RuntimeError("Tutorial identity is missing its credential salt; do not reseed")
        if stored != identity(settings, salt):
            raise RuntimeError(
                "Endpoint, workspace, or credential changed; verify ownership before recovery"
            )
        if lesson is not None and data.get("lesson") != lesson:
            raise RuntimeError("State belongs to another tutorial")
        return cls(path, data)

    def save(self):
        private_json(self.path, self.data)

    def record(self, kind, resource_id, **metadata):
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise RuntimeError(f"Missing returned {kind} ID; retained pending operation")
        resources = self.data["resources"].setdefault(kind, {})
        resources.setdefault(resource_id, {"status": "retained"}).update(metadata)
        self.save()

    def begin(self, operation):
        if self.data.get("pending"):
            raise RuntimeError("Unresolved write; inspect the retained state before retrying")
        self.data["remote_write_started"] = True
        self.data["pending"] = operation
        self.save()

    def finish(self):
        self.data["pending"] = None
        self.save()

    def inspect(self):
        data = json.loads(json.dumps(self.data))
        data["identity"].pop("credential_salt", None)
        data["identity"].pop("credential_digest", None)
        return data


def remember_message(state, message):
    role = getattr(message.role, "value", message.role)
    state.record("message", str(message.id), content_sha256=digest(message.content), role=role)


async def verify_messages(client, state):
    """Read-only exact-ID/content verification; safe to run in a fresh process."""
    expected = state.data["resources"].get("message", {})
    if not expected or not state.data.get("conversation_id"):
        raise RuntimeError("No complete message seed to verify")
    history = await client.short_term.get_conversation(state.data["conversation_id"])
    actual = {str(m.id): m for m in history.messages}
    if set(actual) != set(expected):
        raise RuntimeError("Conversation has missing or unrecorded messages; retained state")
    for message_id, entry in expected.items():
        message = actual[message_id]
        role = getattr(message.role, "value", message.role)
        if digest(message.content) != entry["content_sha256"] or role != entry["role"]:
            raise RuntimeError(f"Message readback differs: {message_id}")
    print(f"Verified: exact IDs, roles and content for {len(expected)} stored messages")


def main(argv=None):
    """Local diagnostics only; importing this module still performs no I/O."""
    import argparse

    parser = argparse.ArgumentParser(description="Inspect hosted lesson state without credentials")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect-file", help="Print a redacted local summary; no network")
    inspect.add_argument("state", type=Path)
    retry = commands.add_parser("check-new-run", help="Check a separate path; preserve both files")
    retry.add_argument("state", type=Path)
    retry.add_argument("--next-state", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "inspect-file":
        print(json.dumps(inspect_file(args.state), indent=2))
    else:
        status = check_new_run(args.state, args.next_state)
        print(f"Recorded new-run check: {status}; original state preserved")
        print(f"Use --state {args.next_state} only after rechecking the lesson prerequisites")
        print("No service state was checked and no new file was created")


if __name__ == "__main__":
    main()
