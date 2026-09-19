"""Offline contracts for authored Python references.

These checks compare published declarations/inventories with the checked-out
source and exercise selected CLI examples using an injected local extractor.
They do not download models or connect to Neo4j, NAMS, or an LLM provider.
"""

from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "neo4j_agent_memory"
REFERENCE = ROOT / "docs" / "modules" / "ROOT" / "pages" / "reference"
CONTRACT = re.compile(
    r"// contract: ([^:]+):([^.]+)\.([^\n]+)\n"
    r"\[source,python\]\n----\n(.*?)\n----",
    re.DOTALL,
)


def source_class(path: str, name: str) -> ast.ClassDef:
    tree = ast.parse((SOURCE / path).read_text())
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)


def reference_contracts():
    for path in [
        *sorted((REFERENCE / "api").glob("*.adoc")),
        *sorted(REFERENCE.glob("extractor*.adoc")),
        *sorted(REFERENCE.glob("schemas*.adoc")),
    ]:
        for match in CONTRACT.finditer(path.read_text()):
            yield pytest.param(*match.groups(), id=f"{path.stem}:{match[2]}.{match[3]}")


@pytest.mark.parametrize("path,class_name,method_name,declaration", list(reference_contracts()))
def test_published_signature_matches_source(path, class_name, method_name, declaration):
    """Catch renamed/missing kwargs, wrong defaults, return types, and async mismatches."""
    cls = source_class(path, class_name)
    actual = next(
        node
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )
    documented = ast.parse(declaration).body[0]
    assert type(documented) is type(actual)
    arguments = copy.deepcopy(actual.args)
    if arguments.args and arguments.args[0].arg in {"self", "cls"}:
        arguments.args.pop(0)
    assert ast.dump(documented.args) == ast.dump(arguments)
    assert (ast.dump(documented.returns) if documented.returns else None) == (
        ast.dump(actual.returns) if actual.returns else None
    )


def settings_values(node: ast.AST, tree: ast.Module):
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        enum = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == node.value.id)
        item = next(
            n for n in enum.body if isinstance(n, ast.Assign) and n.targets[0].id == node.attr
        )
        return ast.literal_eval(item.value)
    if isinstance(node, ast.List):
        return [settings_values(item, tree) for item in node.elts]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return settings_values(node.left, tree) * settings_values(node.right, tree)
    return ast.literal_eval(node)


def setting_tables():
    filenames = [p.name for p in sorted(REFERENCE.glob("configuration*.adoc"))]
    filenames.append("environment-variables.adoc")
    for filename in filenames:
        text = (REFERENCE / filename).read_text()
        for match in re.finditer(
            r"// settings-fields: (\w+):(\w+):(python|env)\n.*?\n\|===\n(.*?)\n\|===",
            text,
            re.DOTALL,
        ):
            yield pytest.param(*match.groups(), id=f"{filename}:{match[1]}")


@pytest.mark.parametrize("class_name,prefix,kind,table", list(setting_tables()))
def test_settings_inventory_defaults_and_constraints(class_name, prefix, kind, table):
    """Keep both config tables complete, including accepted-but-unconsumed fields."""
    tree = ast.parse((SOURCE / "config/settings.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    rows = {}
    for line in table.splitlines():
        if line.startswith("| `"):
            cells = re.split(r"(?<!\\) \| ", line[2:])
            rows[cells[0].strip("`")] = cells
    fields = [n for n in cls.body if isinstance(n, ast.AnnAssign) and n.target.id != "model_config"]
    expected_names = {
        "NAM_" + prefix.upper() + "__" + n.target.id.upper() if kind == "env" else n.target.id
        for n in fields
    }
    assert set(rows) == expected_names
    for field in fields:
        key = (
            "NAM_" + prefix.upper() + "__" + field.target.id.upper()
            if kind == "env"
            else field.target.id
        )
        keywords = {kw.arg: kw.value for kw in field.value.keywords}
        if "default" in keywords:
            value = settings_values(keywords["default"], tree)
            expected = (
                json.dumps(value)
                if isinstance(value, (list, dict))
                else repr(value)
                if isinstance(value, str)
                else str(value)
            )
        elif "default_factory" in keywords and ast.unparse(keywords["default_factory"]) == "dict":
            expected = "{}"
        else:
            expected = "required"
        assert rows[key][2] == f"`{expected}`", key
        constraint = (
            ", ".join(
                f"{name} {settings_values(keywords[name], tree)}"
                for name in ("ge", "gt", "le", "lt")
                if name in keywords
            )
            or "—"
        )
        assert rows[key][3] == constraint, key


def test_every_settings_group_is_documented():
    fields = {
        n.target.id
        for n in source_class("config/settings.py", "MemorySettings").body
        if isinstance(n, ast.AnnAssign) and n.target.id != "model_config"
    }
    text = (REFERENCE / "configuration.adoc").read_text()
    intro = text.split("=== Provider shapes", 1)[0]
    assert all(f"| `{name}` |" in intro for name in fields)


def test_domain_schema_labels_and_descriptions_match_registry():
    tree = ast.parse((SOURCE / "extraction/gliner_extractor.py").read_text())
    registry = next(
        n for n in tree.body if isinstance(n, ast.AnnAssign) and n.target.id == "DOMAIN_SCHEMAS"
    )
    text = (REFERENCE / "schemas-domains.adoc").read_text()
    for key, call in zip(registry.value.keys, registry.value.values, strict=True):
        name = ast.literal_eval(key)
        entities = ast.literal_eval(
            next(kw.value for kw in call.keywords if kw.arg == "entity_types")
        )
        table = re.search(rf"// domain-schema: {name}\n.*?\n\|===\n(.*?)\n\|===", text, re.DOTALL)[
            1
        ]
        documented = dict(re.findall(r"^\| `([^`]+)` \| (.+)$", table, re.MULTILINE))
        assert documented == entities, name


def test_schema_object_inventory_matches_bolt_schema_manager():
    cls = source_class("graph/schema.py", "SchemaManager")
    expected = set()
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and node.target.id == "_MANAGED_VECTOR_INDEXES":
            expected.update(ast.literal_eval(node.value))
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {
            "setup_constraints",
            "setup_indexes",
            "setup_point_indexes",
        }:
            assignment = next(n for n in node.body if isinstance(n, ast.Assign))
            expected.update(ast.literal_eval(assignment.value))
    text = (REFERENCE / "schema-objects.adoc").read_text()
    documented = set(re.findall(r"^\| `([^`]+)`\s+\| `([^`]+)`\s+\| `([^`]+)`", text, re.MULTILINE))
    assert documented == expected


@pytest.mark.parametrize(
    "command_name", ["extract", "schemas list", "schemas show", "stats", "mcp serve"]
)
def test_cli_option_tables_match_registered_options(command_name):
    from neo4j_agent_memory.cli.main import cli

    command = cli
    for component in command_name.split():
        command = command.commands[component]
    text = (REFERENCE / "cli.adoc").read_text()
    table = re.search(
        rf"// cli-options: {command_name}\n.*?\n\|===\n(.*?)\n\|===", text, re.DOTALL
    )[1]
    documented = set(
        re.findall(r"`(--?[\w-]+)`", "\n".join(line.split(" | ")[0] for line in table.splitlines()))
    )
    expected = {
        option
        for param in command.params
        for option in [*param.opts, *getattr(param, "secondary_opts", [])]
        if option.startswith("-")
    }
    assert documented == expected
    for param in command.params:
        for choice in getattr(param.type, "choices", []):
            assert f"`{choice}`" in table


def test_cli_schema_and_json_example_with_injected_extractor(tmp_path, monkeypatch):
    """Exercise documented flags, YAML parsing, and JSON shape without model I/O."""
    from click.testing import CliRunner

    from neo4j_agent_memory.cli.main import cli
    from neo4j_agent_memory.extraction import ExtractedEntity, ExtractionResult
    from neo4j_agent_memory.extraction.factory import ExtractorBuilder

    document = (REFERENCE / "cli.adoc").read_text()
    schema = re.search(r"\[source,yaml\]\n----\n(.*?)\n----", document, re.DOTALL)[1]
    schema_file = tmp_path / "custom_schema.yaml"
    schema_file.write_text(schema)
    calls = []

    class LocalExtractor:
        async def extract(self, text, **kwargs):
            calls.append((text, kwargs))
            return ExtractionResult(
                entities=[ExtractedEntity(type="PERSON", name="John Smith", confidence=0.95)],
                source_text=text,
            )

    monkeypatch.setattr(ExtractorBuilder, "build", lambda _self: LocalExtractor())
    runner = CliRunner()
    validation = runner.invoke(cli, ["schemas", "validate", str(schema_file)])
    assert validation.exit_code == 0, validation.output
    result = runner.invoke(
        cli,
        ["extract", "--schema", str(schema_file), "-e", "PERSON", "--format", "json", "John Smith"],
    )
    assert result.exit_code == 0, result.output
    expected_json = json.loads(
        re.search(r"\[source,json\]\n----\n(.*?)\n----", document, re.DOTALL)[1]
    )
    assert json.loads(result.output) == expected_json
    assert calls == [("John Smith", {"extract_relations": True, "extract_preferences": False})]
    invalid = runner.invoke(cli, ["extract", "--extractor", "spacy", "John Smith"])
    assert invalid.exit_code == 2
    assert "Invalid value" in invalid.output
