"""Offline copied-file, interrupted Strands, and final Desktop config regressions."""

import importlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "docs/modules/ROOT/examples"
PAGES = ROOT / "docs/modules/ROOT/pages/tutorials"
PARTIALS = ROOT / "docs/modules/ROOT/partials"
LESSONS = {
    "first-agent-memory": "first_agent_memory",
    "conversation-memory": "conversation_memory",
    "knowledge-graph": "knowledge_graph",
    "anthropic-and-local-embeddings": "anthropic_local_memory",
    "microsoft-agent-memory": "microsoft_shopping_tutorial",
    "strands-agent-quickstart": "strands_memory_tutorial",
    "mcp-server": "mcp_local_tutorial",
}
EXPORTS = {
    "NEO4J_URI": "neo4j+s://synthetic.databases.neo4j.io",
    "NEO4J_USERNAME": "synthetic-user",
    "NEO4J_PASSWORD": "synthetic-secret-do-not-print",
    "NEO4J_DATABASE": "synthetic-db",
    "BEDROCK_MODEL_ID": "synthetic-model",
}


def assembled_page(page):
    def expand(text):
        return re.sub(
            r"include::partial\$(.+?)\[\]",
            lambda match: expand((PARTIALS / match[1]).read_text()),
            text,
        )

    return expand((PAGES / f"{page}.adoc").read_text())


def copy_lesson(page, folder):
    text = assembled_page(page)
    files = re.findall(
        r"\.Save as `([^`]+)`\n\[source,python\]\n----\ninclude::example\$(.+?)\[\]",
        text,
    )
    assert len(files) == (4 if page in list(LESSONS)[:3] else 3)
    for name, source in files:
        assert name == source
        (folder / name).write_text((EXAMPLES / source).read_text())
    block = next(
        block
        for block in re.findall(r"\[source,bash\]\n----\n(.*?)\n----", text, re.S)
        if block.startswith("python -m py_compile ")
    )
    return text, [shlex.split(line) for line in block.splitlines()]


def offline_run(command, folder):
    # Imported automatically by Python: any unexpected network use fails the check.
    (folder / "sitecustomize.py").write_text(
        "import socket\n"
        "def forbidden(*args, **kwargs):\n"
        "    raise AssertionError('Network forbidden in the assembly checkpoint')\n"
        "socket.socket.connect = forbidden\n"
        "socket.socket.connect_ex = forbidden\n"
    )
    return subprocess.run(
        [sys.executable, *command[1:]],
        cwd=folder,
        env={
            "PATH": os.defpath,
            "HOME": str(folder),
            "PYTHONPATH": str(folder),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("page", LESSONS)
def test_authored_assembly_uses_only_displayed_files_and_never_connects(page, tmp_path):
    text, commands = copy_lesson(page, tmp_path)
    manifest = re.findall(r"^\|`([^`]+\.py)`", text, re.M)
    assert set(manifest) == {path.name for path in tmp_path.glob("*.py")}
    assert set(commands[0][3:]) == set(manifest)
    assert text.index("python -m py_compile") < text.index("python wait_for_tutorial_neo4j.py\n")
    assert ":page-role: code-nocollapse" in text
    for command in commands:
        result = offline_run(command, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "strands-tutorial-session.txt").exists()
    assert not (tmp_path / ".tutorial-state").exists()


@pytest.mark.parametrize("damage", ["missing-helper", "truncated-helper", "truncated-main"])
def test_assembly_detects_missing_or_syntactically_valid_truncated_files(damage, tmp_path):
    _text, commands = copy_lesson("first-agent-memory", tmp_path)
    if damage == "missing-helper":
        (tmp_path / "aura_connection.py").unlink()
        result = offline_run(commands[0], tmp_path)
        assert result.returncode != 0
    else:
        name = "aura_connection.py" if damage == "truncated-helper" else "first_agent_memory.py"
        (tmp_path / name).write_text("# A syntactically valid but incomplete copy\n")
        assert offline_run(commands[0], tmp_path).returncode == 0
    result = offline_run(commands[1], tmp_path)
    assert result.returncode != 0
    assert "Lesson imports verified" not in result.stdout


@pytest.fixture
def modules(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(EXAMPLES))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", dict(EXPORTS))
    return SimpleNamespace(
        strands=importlib.import_module("strands_memory_tutorial"),
        mcp=importlib.import_module("mcp_local_tutorial"),
    )


@pytest.fixture
def strands_runtime(modules, monkeypatch):
    import neo4j_agent_memory

    framework = ModuleType("strands")
    framework.Agent = Mock(side_effect=AssertionError("No agent turn expected"))
    integration = ModuleType("neo4j_agent_memory.integrations.strands")
    integration.Neo4jSessionManager = Mock(side_effect=ConnectionError("Synthetic startup failure"))
    monkeypatch.setitem(sys.modules, "strands", framework)
    monkeypatch.setitem(sys.modules, "neo4j_agent_memory.integrations.strands", integration)
    monkeypatch.setattr(neo4j_agent_memory, "BoltSettings", Mock(return_value=object()))
    return framework.Agent, integration.Neo4jSessionManager


def inspect_driver(monkeypatch, messages, conversations=1):
    import neo4j

    driver = Mock()
    driver.__enter__ = Mock(return_value=driver)
    driver.__exit__ = Mock(return_value=False)
    driver.execute_query.return_value = (
        [{"conversations": conversations, "messages": messages}],
        None,
        None,
    )
    factory = Mock(return_value=driver)
    monkeypatch.setattr(neo4j.GraphDatabase, "driver", factory)
    return driver, factory


def test_strands_startup_failure_keeps_id_and_inspection_does_not_repeat_writes(
    modules, strands_runtime, monkeypatch, capsys
):
    lesson = modules.strands
    monkeypatch.setattr(sys, "argv", ["strands_memory_tutorial.py", "record"])
    with pytest.raises(ConnectionError, match="Synthetic"):
        lesson.main()
    saved = lesson.SESSION_FILE.read_bytes()
    with pytest.raises(RuntimeError, match="run inspect"):
        lesson.main()
    assert lesson.SESSION_FILE.read_bytes() == saved
    agent, manager = strands_runtime
    assert manager.call_count == 1
    agent.assert_not_called()
    driver, factory = inspect_driver(monkeypatch, [], conversations=0)
    os.environ.pop("BEDROCK_MODEL_ID")  # Inspection needs only Aura, not AWS/model configuration.
    monkeypatch.setattr(sys, "argv", ["strands_memory_tutorial.py", "inspect"])
    lesson.main()
    output = capsys.readouterr().out
    assert '"sentinel_stored": false' in output and '"message_count": 0' in output
    assert "Not ready for recall" in output
    assert lesson.SESSION_FILE.read_bytes() == saved
    assert manager.call_count == 1
    agent.assert_not_called()
    factory.assert_called_once_with(
        EXPORTS["NEO4J_URI"], auth=(EXPORTS["NEO4J_USERNAME"], EXPORTS["NEO4J_PASSWORD"])
    )
    (query,) = driver.execute_query.call_args.args
    assert "MATCH" in query and not re.search(r"\b(CREATE|MERGE|DELETE|SET)\b", query)
    assert driver.execute_query.call_args.kwargs == {
        "session_id": saved.decode(),
        "database_": EXPORTS["NEO4J_DATABASE"],
        "routing_": "r",
    }
    driver.__exit__.assert_called_once()


@pytest.mark.parametrize("model", [None, "", "replace-with-your-model"])
def test_strands_missing_model_does_not_create_session_file(
    modules, strands_runtime, monkeypatch, model
):
    if model is None:
        os.environ.pop("BEDROCK_MODEL_ID")
    else:
        os.environ["BEDROCK_MODEL_ID"] = model
    monkeypatch.setattr(sys, "argv", ["strands_memory_tutorial.py", "record"])
    with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
        modules.strands.main()
    assert not modules.strands.SESSION_FILE.exists()
    strands_runtime[1].assert_not_called()


@pytest.mark.parametrize("role,sentinel", [("user", True), ("assistant", False)])
def test_inspection_requires_exact_stored_user_message(modules, monkeypatch, role, sentinel):
    lesson = modules.strands
    lesson.SESSION_FILE.write_text("strands-docs-existing")
    driver, _ = inspect_driver(
        monkeypatch, [{"id": "returned-id", "role": role, "content": lesson.SENTINEL}]
    )
    result = lesson.inspect_session()
    assert result["sentinel_stored"] is sentinel
    assert result["message_ids"] == ["returned-id"]
    driver.__exit__.assert_called_once()


def test_failed_inspection_does_not_report_empty_or_change_id(modules, monkeypatch, capsys):
    lesson = modules.strands
    lesson.SESSION_FILE.write_text("strands-docs-existing")
    driver, _ = inspect_driver(monkeypatch, [])
    driver.execute_query.side_effect = ConnectionError("Synthetic failed read")
    with pytest.raises(ConnectionError):
        lesson.inspect_session()
    assert lesson.SESSION_FILE.read_text() == "strands-docs-existing"
    assert "message_count" not in capsys.readouterr().out
    driver.__exit__.assert_called_once()


def desktop_config(module):
    return {
        "mcpServers": {
            "unrelated": {"command": "unchanged", "args": []},
            "neo4j-docs": {
                "command": sys.executable,
                "args": [str(Path(module.__file__).resolve())],
                "env": {key: value for key, value in EXPORTS.items() if key.startswith("NEO4J_")},
            },
        }
    }


def test_final_merged_desktop_config_is_checked_without_secrets_or_mutation(
    modules, tmp_path, capsys
):
    path = tmp_path / "desktop.json"
    original = json.dumps(desktop_config(modules.mcp))
    path.write_text(original)
    modules.mcp.check_desktop_config(path)
    assert path.read_text() == original
    output = capsys.readouterr().out
    assert "Verified: merged Desktop JSON" in output
    assert EXPORTS["NEO4J_PASSWORD"] not in output
    assert EXPORTS["NEO4J_URI"] not in output


@pytest.mark.parametrize(
    "damage", ["comma", "nesting", "missing-env", "wrong-path", "duplicate-key"]
)
def test_desktop_merge_errors_fail_without_printing_config(
    modules, tmp_path, monkeypatch, capsys, damage
):
    path = tmp_path / "desktop.json"
    config = desktop_config(modules.mcp)
    if damage == "nesting":
        config = config["mcpServers"]
    elif damage == "missing-env":
        config["mcpServers"]["neo4j-docs"]["env"].pop("NEO4J_PASSWORD")
    elif damage == "wrong-path":
        config["mcpServers"]["neo4j-docs"]["args"] = ["relative-script.py"]
    text = json.dumps(config)
    if damage == "comma":
        text = text[:-1] + ",}"
    elif damage == "duplicate-key":
        text = '{"mcpServers": {}, ' + text[1:]
    path.write_text(text)
    monkeypatch.setattr(sys, "argv", ["mcp_local_tutorial.py", "--check-config", str(path)])
    monkeypatch.setattr(
        modules.mcp, "build_server", Mock(side_effect=AssertionError("No server startup"))
    )
    with pytest.raises(SystemExit) as caught:
        modules.mcp.main()
    assert caught.value.code == 1
    output = capsys.readouterr().err
    assert "Desktop configuration check failed" in output
    assert EXPORTS["NEO4J_PASSWORD"] not in output
    assert EXPORTS["NEO4J_URI"] not in output
    assert path.read_text() == text
    modules.mcp.build_server.assert_not_called()
