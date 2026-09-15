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
    identity = {
        "endpoint": endpoint,
        "workspace_id": config.workspace_id,
        "credential_sha256": digest(key),
    }
    return identity, headers, config.timeout


def identity(settings):
    return connection(settings)[0]


def http_client(settings, **kwargs):
    import httpx

    resolved, headers, timeout = connection(settings)
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
    def create(cls, path, settings, lesson):
        data = {
            "schema_version": 1,
            "lesson": lesson,
            "identity": identity(settings),
            "run_token": uuid4().hex,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "resources": {},
            "pending": None,
        }
        private_json(Path(path), data, exclusive=True)
        return cls(path, data)

    @classmethod
    def load(cls, path, settings, lesson=None):
        path = Path(path)
        if path.is_symlink():
            raise RuntimeError("Refusing a symlink for tutorial state")
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise RuntimeError("Unsupported or incomplete tutorial state")
        if data.get("identity") != identity(settings):
            raise RuntimeError(
                "Endpoint, workspace, or credential changed; verify ownership before recovery"
            )
        if lesson is not None and data.get("lesson") != lesson:
            raise RuntimeError("State belongs to another tutorial")
        if not isinstance(data.get("resources"), dict) or not data.get("run_token"):
            raise RuntimeError("Incomplete tutorial state")
        datetime.fromisoformat(data["started_at"])
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
        self.data["pending"] = operation
        self.save()

    def finish(self):
        self.data["pending"] = None
        self.save()

    def inspect(self):
        data = json.loads(json.dumps(self.data))
        data["identity"].pop("credential_sha256", None)
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
