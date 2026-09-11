"""Smoke tests for the `claude-code-team-memory` example.

The example is configuration plus three scripts, so the tests split the same way:

* **Structure** — every config file parses as JSON, names a real command or URL,
  and contains no pasted credential; `bundle/build.sh` points at the repository's
  single `.mcpb` manifest rather than a second copy of it.
* **Claims** — the "core = 6, extended = 16" contrast is asserted against
  ``register_tools`` itself (importing ``neo4j_agent_memory.mcp._tools``, not
  grepping prose), and the hosted NAMS surface is asserted to be described as a
  *different* count so ts-mcp-F01 ("the hosted server has 16 tools") cannot recur.
* **Behaviour** — all three scripts are executed end to end against a
  ``respx``-mocked NAMS: no API key, no network, no Neo4j. These scripts are the
  only code a reader runs before wiring an editor, and nothing else type-checks
  them (``mypy``/``ty`` cover ``examples/*.py``, which excludes subdirectories).

Route payloads mirror ``tests/unit/nams/*`` fixtures, which were verified against
the live NAMS OpenAPI spec.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("respx", reason="respx not installed")

import httpx  # noqa: E402
import respx  # noqa: E402

from tests.examples._manifests import assert_library_pin  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "claude-code-team-memory"
SCRIPTS = ("doctor.py", "provision_keys.py", "seed_workspace.py", "_shared.py")
CONFIG_FILES = (
    ".mcp.json.example",
    "claude_desktop_config.json.example",
    "cursor_mcp.json.example",
)

ENDPOINT = "https://memory.test/v1"
WORKSPACE_ID = "ws_test"
CONVERSATION_ID = "00000000-0000-0000-0000-0000000000aa"
ENTITY_ID = "00000000-0000-0000-0000-0000000000e1"
KEY_ID = "ak_00000000"

#: Deliberately not key-shaped beyond the prefix: the secrets guard below scans
#: the example directory, and a realistic-looking value in a fixture invites
#: someone to copy it there.
FAKE_PLAINTEXT = "nams_test_plaintext_value"

CONVERSATION = {
    "id": CONVERSATION_ID,
    "userId": "demo",
    "createdAt": "2026-09-10T12:00:00Z",
    "updatedAt": "2026-09-10T12:00:00Z",
}
ENTITY = {
    "id": ENTITY_ID,
    "name": "Atlas",
    "type": "organization",
    "description": "Internal data platform service.",
    "createdAt": "2026-09-10T12:00:04Z",
    "updatedAt": "2026-09-10T12:00:04Z",
}
#: ``wait_for_extraction(expected_names=[...])`` only settles once *every* name is
#: searchable, so the search route has to return both of the names
#: ``seed_workspace.EXPECTED_ENTITIES`` asks for. Returning one of them is how a
#: mocked run ends up silently polling until its timeout.
PERSON_ENTITY = {
    "id": "00000000-0000-0000-0000-0000000000e2",
    "name": "Alice Nakamura",
    "type": "person",
    "createdAt": "2026-09-10T12:00:04Z",
    "updatedAt": "2026-09-10T12:00:04Z",
}
API_KEY_RECORD = {
    "id": KEY_ID,
    "label": "alice-laptop",
    "scopes": ["memory:read", "memory:write"],
    "workspace_id": WORKSPACE_ID,
    "created_at": "2026-09-10T12:00:00Z",
}


# ── module loading ───────────────────────────────────────────────────


def _noop_load_env(*_args: object, **_kwargs: object) -> None:
    """Stand-in for the scripts' ``load_env`` so no local ``.env`` is read."""


def _load(script: str):
    """Import one of the example's scripts by path.

    The scripts ``import _shared``, which resolves because Python puts a script's
    own directory on ``sys.path`` — replicate that here rather than packaging the
    example just so tests can import it.
    """
    if str(EXAMPLE_DIR) not in sys.path:
        sys.path.insert(0, str(EXAMPLE_DIR))
    name = f"team_memory_{script.removesuffix('.py')}"
    spec = importlib.util.spec_from_file_location(name, EXAMPLE_DIR / script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, name


@pytest.fixture
def script(monkeypatch):
    """Load a script with ``load_env`` neutralised.

    A developer's own ``examples/claude-code-team-memory/.env`` must never leak
    into a test run — that is how a real key ends up in CI output.
    """
    loaded: list[str] = []

    def _loader(name: str):
        module, module_name = _load(name)
        loaded.append(module_name)
        monkeypatch.setattr(module, "load_env", _noop_load_env)
        return module

    yield _loader
    for module_name in loaded:
        sys.modules.pop(module_name, None)


def _mock_nams(router: respx.Router) -> None:
    """Every route the three scripts drive."""
    # connect() probe + doctor's list_conversations
    router.get(f"{ENDPOINT}/conversations").respond(200, json={"conversations": [CONVERSATION]})
    router.post(f"{ENDPOINT}/conversations").respond(201, json=CONVERSATION)
    router.post(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/messages/bulk").respond(
        201,
        json={
            "messages": [
                {
                    "id": f"00000000-0000-0000-0000-0000000000{index:02d}",
                    "conversationId": CONVERSATION_ID,
                    "role": "user",
                    "content": "decision",
                    "createdAt": "2026-09-10T12:00:01Z",
                }
                for index in range(15)
            ]
        },
    )

    # Echo the requested name so two different writes are two different entities.
    def _create_entity(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        return httpx.Response(201, json={**ENTITY, "name": body.get("name", "Atlas")})

    router.post(f"{ENDPOINT}/entities").mock(side_effect=_create_entity)
    router.get(f"{ENDPOINT}/conversations/{CONVERSATION_ID}/extraction-status").respond(
        200, json={"summary": {"completed": 15}, "messages": []}
    )
    router.post(f"{ENDPOINT}/entities/search").respond(
        200, json={"entities": [ENTITY, PERSON_ENTITY], "searchType": "vector"}
    )
    router.post(f"{ENDPOINT}/query").respond(
        200, json={"columns": ["entities"], "rows": [{"entities": 7}], "stats": {}}
    )
    # client.auth
    router.get(f"{ENDPOINT}/auth/api-keys").respond(200, json={"keys": [API_KEY_RECORD]})
    router.post(f"{ENDPOINT}/auth/api-keys").respond(
        201, json={**API_KEY_RECORD, "key": FAKE_PLAINTEXT}
    )
    router.post(f"{ENDPOINT}/auth/api-keys/{KEY_ID}/rotate").respond(
        200, json={**API_KEY_RECORD, "id": "ak_rotated", "key": FAKE_PLAINTEXT}
    )
    router.delete(f"{ENDPOINT}/auth/api-keys/{KEY_ID}").respond(204)


@pytest.fixture
def nams_env(monkeypatch):
    monkeypatch.setenv("MEMORY_API_KEY", "nams_unit_test_key")
    monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("MEMORY_WORKSPACE_ID", WORKSPACE_ID)
    monkeypatch.delenv("TEAM_MEMORY_CONVERSATION_ID", raising=False)


# ── structure ────────────────────────────────────────────────────────


@pytest.mark.syntax
class TestStructure:
    def test_required_files_exist(self):
        expected = (
            "README.md",
            "WALKTHROUGH.md",
            "requirements.txt",
            ".env.example",
            "bundle/build.sh",
            *SCRIPTS,
            *CONFIG_FILES,
        )
        for name in expected:
            assert (EXAMPLE_DIR / name).exists(), f"Missing: {name}"

    def test_scripts_compile(self):
        for name in SCRIPTS:
            ast.parse((EXAMPLE_DIR / name).read_text(encoding="utf-8"))

    def test_requirements_pin_the_library_with_nams_and_mcp_extras(self):
        pin = assert_library_pin(EXAMPLE_DIR / "requirements.txt")
        assert "nams" in pin.extras, "the hosted transport comes from the [nams] extra"
        assert "mcp" in pin.extras, "doctor.py imports FastMCP via the [mcp] extra"

    @pytest.mark.parametrize("filename", CONFIG_FILES)
    def test_config_file_is_valid_mcp_json(self, filename):
        data = json.loads((EXAMPLE_DIR / filename).read_text(encoding="utf-8"))
        servers = data["mcpServers"]
        assert isinstance(servers, dict) and servers, f"{filename}: no servers"
        for name, entry in servers.items():
            has_command = bool(entry.get("command"))
            has_url = bool(entry.get("url"))
            assert has_command or has_url, f"{filename}:{name} has neither command nor url"
            if has_command:
                assert isinstance(entry.get("args", []), list)

    @pytest.mark.parametrize("filename", CONFIG_FILES)
    def test_config_file_names_the_documented_servers(self, filename):
        """Both halves are wired in every file: hosted NAMS and self-hosted."""
        raw = (EXAMPLE_DIR / filename).read_text(encoding="utf-8")
        assert "https://mcp.memory.neo4jlabs.com/mcp" in raw, "hosted NAMS entry missing"
        assert "neo4j-agent-memory[mcp]" in raw, "self-hosted uvx entry missing"
        assert '"mcp",' in raw and '"serve",' in raw, "not the documented `mcp serve` command"
        assert "--session-strategy" in raw and "per_day" in raw

    def test_bundle_build_script_wraps_the_repo_manifest(self):
        """One manifest, in deploy/mcpb — not a second copy in the example."""
        script = EXAMPLE_DIR / "bundle" / "build.sh"
        raw = script.read_text(encoding="utf-8")
        assert "deploy/mcpb/manifest.json" in raw
        assert (REPO_ROOT / "deploy" / "mcpb" / "manifest.json").exists()
        assert not (EXAMPLE_DIR / "bundle" / "manifest.json").exists(), (
            "the example must point at deploy/mcpb/manifest.json, not duplicate it"
        )

    def test_env_example_documents_the_hosted_variables(self):
        content = (EXAMPLE_DIR / ".env.example").read_text(encoding="utf-8")
        for variable in ("MEMORY_API_KEY", "MEMORY_ENDPOINT", "MEMORY_WORKSPACE_ID"):
            assert variable in content, f"{variable} undocumented"

    def test_no_api_key_shape_anywhere_in_the_example(self):
        """Every `nams_…` in the directory must be an all-`x` placeholder.

        The guard exists because this example's whole job is to hand credentials
        to editors; a demo recording or a hurried `cp` is exactly how one gets
        committed.
        """
        key_shape = re.compile(r"nams_[A-Za-z0-9_-]{12,}")
        offenders: list[str] = []
        for path in sorted(EXAMPLE_DIR.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.name == ".env":
                continue
            for match in key_shape.findall(path.read_text(encoding="utf-8", errors="ignore")):
                tail = match[len("nams_") :]
                if set(tail.lower()) != {"x"}:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {match[:10]}…")
        assert not offenders, "non-placeholder key shapes found:\n  " + "\n  ".join(offenders)

    def test_readme_follows_labs_conventions(self):
        readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert "Neo4j-Labs" in readme, "missing the Labs badge"
        assert "Neo4j Labs Project" in readme, "missing the Labs disclaimer"
        assert "## Prerequisites" in readme
        assert "## Expected output" in readme
        assert "## Support" in readme
        assert "Verified against" in readme
        assert "MEMORY_API_KEY" in readme, "the live command must name the key variable"
        assert "docs/.../" not in readme, "no placeholder doc paths"

    def test_readme_states_the_two_tool_surfaces_once(self):
        """ts-mcp-F01: the hosted surface is 47 tools, not the self-hosted 6/16."""
        readme = (EXAMPLE_DIR / "README.md").read_text(encoding="utf-8")
        assert "**47**" in readme or "47" in readme
        assert "`--profile core`" in readme and "`--profile extended`" in readme
        # One table, one place: the count must not be restated in WALKTHROUGH as
        # a competing claim.
        walkthrough = (EXAMPLE_DIR / "WALKTHROUGH.md").read_text(encoding="utf-8")
        assert "README.md" in walkthrough, "WALKTHROUGH must defer to the README table"


# ── claims checked against the library ───────────────────────────────


@pytest.mark.imports
class TestClaims:
    def test_the_surface_the_scripts_import_exists(self):
        from neo4j_agent_memory import NamsSettings, connect  # noqa: F401
        from neo4j_agent_memory.core.exceptions import (  # noqa: F401
            AuthenticationError,
            NotSupportedError,
            RateLimitError,
            TransportError,
        )
        from neo4j_agent_memory.nams.auth_keys import NamsAuth

        for method in ("create_api_key", "list_api_keys", "rotate_api_key", "revoke_api_key"):
            assert hasattr(NamsAuth, method), f"client.auth.{method} is gone"

    async def test_documented_profile_counts_match_register_tools(self, script):
        """The README/doctor counts come from the registrar, not from prose."""
        pytest.importorskip("fastmcp", reason="fastmcp not installed")
        from fastmcp import FastMCP

        from neo4j_agent_memory.mcp._tools import register_tools

        doctor = script("doctor.py")
        for profile, documented in doctor.SELF_HOSTED_PROFILES.items():
            server = FastMCP(name=f"test-{profile}")
            register_tools(server, profile=profile)
            assert len({tool.name for tool in await server.list_tools()}) == documented

    def test_hosted_count_is_distinct_from_the_self_hosted_profiles(self, script):
        doctor = script("doctor.py")
        assert doctor.HOSTED_NAMS_TOOL_COUNT not in doctor.SELF_HOSTED_PROFILES.values()
        assert doctor.HOSTED_NAMS_MCP_URL == "https://mcp.memory.neo4jlabs.com/mcp"

    def test_seed_data_is_synthetic_and_fits_one_bulk_call(self, script):
        seed = script("seed_workspace.py")
        assert 10 <= len(seed.DECISIONS) <= 100, "NAMS caps bulk_add_messages at 100"
        assert all({"role", "content"} <= set(m) for m in seed.DECISIONS)
        assert seed.EXPECTED_ENTITIES, "wait_for_extraction needs a specific assertion"


# ── behaviour, against a mocked NAMS ─────────────────────────────────


class TestDoctorOffline:
    async def test_configs_only_passes_without_a_key(self, script, monkeypatch, capsys):
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)
        doctor = script("doctor.py")

        exit_code = await doctor.main(["--configs-only"])

        out = capsys.readouterr().out
        assert exit_code == 0, out
        assert "1. Environment" in out
        assert "2. MCP config files" in out
        for filename in CONFIG_FILES:
            assert f"[PASS] {filename}" in out
        assert "self-hosted --profile core: 6 tool(s)" in out
        assert "self-hosted --profile extended: 16 tool(s)" in out
        assert "47 scope-gated tools" in out
        assert "skipped the live NAMS checks" in out

    async def test_missing_key_fails_a_full_run(self, script, monkeypatch, capsys):
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)
        doctor = script("doctor.py")

        exit_code = await doctor.main([])

        out = capsys.readouterr().out
        assert exit_code == 1
        assert "[FAIL] MEMORY_API_KEY" in out
        assert "no MEMORY_API_KEY; skipping the live checks" in out

    async def test_placeholder_key_is_rejected(self, script, monkeypatch, capsys):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_xxxxxxxxxxxxxxxx")
        doctor = script("doctor.py")

        exit_code = await doctor.main(["--configs-only"])

        assert exit_code == 1
        assert "still the nams_xxxx placeholder" in capsys.readouterr().out

    async def test_broken_config_is_reported_with_its_line(self, script, tmp_path, capsys):
        doctor = script("doctor.py")
        broken = tmp_path / "broken.json"
        broken.write_text('{\n  "mcpServers": {,}\n}\n', encoding="utf-8")

        doctor.check_config_file(broken, show_servers=False)

        assert "[FAIL] broken.json: invalid JSON at line" in capsys.readouterr().out

    async def test_pasted_key_in_a_config_is_reported(self, script, tmp_path, capsys):
        doctor = script("doctor.py")
        leaky = tmp_path / "leaky.json"
        leaky.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "team-memory": {
                            "url": "https://mcp.memory.neo4jlabs.com/mcp",
                            "headers": {"Authorization": "Bearer nams_realLookingKey123456"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        doctor.check_config_file(leaky, show_servers=False)

        assert "a literal nams_ key is pasted into this file" in capsys.readouterr().out

    async def test_pasted_key_in_an_installed_config_only_warns(self, script, tmp_path, capsys):
        """Claude Desktop cannot expand ${VAR}, so a literal value there is not a bug."""
        doctor = script("doctor.py")
        leaky = tmp_path / "installed.json"
        leaky.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "team-memory": {
                            "url": "https://mcp.memory.neo4jlabs.com/mcp",
                            "headers": {"Authorization": "Bearer nams_realLookingKey123456"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        doctor.check_config_file(leaky, show_servers=False, committed=False)

        out = capsys.readouterr().out
        assert "[WARN] installed.json" in out
        assert "keep it untracked" in out
        assert "nams_realLookingKey123456" not in out, "the value must never be echoed"


class TestDoctorLive:
    async def test_full_run_against_mocked_nams(self, script, nams_env, capsys):
        doctor = script("doctor.py")

        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router)
            exit_code = await doctor.main(["--conversation-id", CONVERSATION_ID])

        out = capsys.readouterr().out
        assert exit_code == 0, out
        assert "[PASS] MEMORY_API_KEY: nams_… (" in out
        assert "value redacted" in out, "the key must never be echoed"
        assert "nams_unit_test_key" not in out
        assert f"[PASS] MEMORY_WORKSPACE_ID: {WORKSPACE_ID}" in out
        assert f"endpoint reachable: {ENDPOINT} (backend=nams)" in out
        assert "workspace readable: 1 recent conversation(s) visible" in out
        assert "[PASS] extraction status: pending=0, complete=True" in out
        assert "entities searchable: 2 entity/entities — Atlas, Alice Nakamura" in out
        assert "read-only Cypher: [{'entities': 7}]" in out
        assert "All checks passed" in out

    async def test_unreachable_endpoint_fails_rather_than_hanging(self, script, nams_env, capsys):
        doctor = script("doctor.py")
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{ENDPOINT}/conversations").mock(side_effect=httpx.ConnectError("nope"))
            exit_code = await doctor.main([])

        out = capsys.readouterr().out
        assert exit_code == 1
        assert "[FAIL] endpoint reachable" in out


class TestProvisionKeys:
    async def test_create_dry_run_neither_connects_nor_mints(self, script, monkeypatch, capsys):
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)
        provision = script("provision_keys.py")

        # No respx router at all: a dry run that touched the network would raise.
        exit_code = await provision.main(["create", "--label", "alice-laptop"])

        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Dry run — nothing was created" in out
        assert "ADMIN key" in out, "the category caveat must be impossible to miss"
        assert 'category": "workspace"' in out

    async def test_list_prints_metadata_only(self, script, nams_env, capsys):
        provision = script("provision_keys.py")

        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router)
            exit_code = await provision.main(["list"])

        out = capsys.readouterr().out
        assert exit_code == 0, out
        assert f"1 key(s) in workspace {WORKSPACE_ID}" in out
        assert KEY_ID in out and "alice-laptop" in out
        assert FAKE_PLAINTEXT not in out, "list must never print a plaintext value"

    async def test_create_confirm_prints_the_plaintext_once(self, script, nams_env, capsys):
        provision = script("provision_keys.py")

        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router)
            exit_code = await provision.main(["create", "--label", "alice-laptop", "--confirm"])

        out = capsys.readouterr().out
        assert exit_code == 0, out
        assert out.count(FAKE_PLAINTEXT) == 1
        assert "shown exactly once" in out

    async def test_rotate_and_revoke(self, script, nams_env, capsys):
        provision = script("provision_keys.py")

        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router)
            assert await provision.main(["rotate", "--key-id", KEY_ID, "--confirm"]) == 0
            assert await provision.main(["revoke", "--key-id", KEY_ID, "--confirm"]) == 0

        out = capsys.readouterr().out
        assert "Rotated. New key id ak_rotated" in out
        assert "Revoked." in out

    async def test_list_without_a_workspace_says_so(self, script, monkeypatch, capsys):
        monkeypatch.setenv("MEMORY_API_KEY", "nams_unit_test_key")
        monkeypatch.setenv("MEMORY_ENDPOINT", ENDPOINT)
        monkeypatch.delenv("MEMORY_WORKSPACE_ID", raising=False)
        provision = script("provision_keys.py")

        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router)
            exit_code = await provision.main(["list"])

        assert exit_code == 1
        assert "list needs a workspace id" in capsys.readouterr().out


class TestSeedWorkspace:
    async def test_dry_run_writes_nothing(self, script, monkeypatch, capsys):
        monkeypatch.delenv("MEMORY_API_KEY", raising=False)
        seed = script("seed_workspace.py")

        exit_code = await seed.main(["--dry-run"])

        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Nothing was written" in out
        assert "15 message(s) would go to 'team-decisions'" in out

    async def test_seeds_and_reads_back_against_mocked_nams(self, script, nams_env, capsys):
        seed = script("seed_workspace.py")

        with respx.mock(assert_all_called=False) as router:
            _mock_nams(router)
            exit_code = await seed.main(["--timeout", "5"])

        out = capsys.readouterr().out
        assert exit_code == 0, out
        assert f"Conversation 'team-decisions' → {CONVERSATION_ID}" in out
        assert "Stored 15 decision messages in one request." in out
        assert "Entity: Atlas (ORGANIZATION)" in out
        assert "Entity: Helios (ORGANIZATION)" in out
        # The bolt-only surface is demonstrated, not described.
        assert "Facts are bolt-only, as expected" in out
        assert "Extraction settled: True" in out
        assert "Searchable entities: 2" in out
        assert "Cypher round-trip: [{'entities': 7}]" in out
        assert f"export TEAM_MEMORY_CONVERSATION_ID={CONVERSATION_ID}" in out
