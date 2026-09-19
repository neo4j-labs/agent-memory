"""Offline public-API and failure-path contracts for the core tutorial programs.

No provider requests, model downloads, or database connections are made here.
"""

import ast
import importlib
import inspect
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec
from uuid import uuid4

import pytest
from packaging.requirements import Requirement

from neo4j_agent_memory.core.query import BoltCypherQuery
from neo4j_agent_memory.extraction.base import ExtractedEntity, ExtractedRelation, ExtractionResult
from neo4j_agent_memory.graph import queries
from neo4j_agent_memory.memory.long_term import Entity, LongTermMemory, Preference
from neo4j_agent_memory.memory.reasoning import ReasoningMemory
from neo4j_agent_memory.memory.short_term import Conversation, Message, ShortTermMemory

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "docs/modules/ROOT/examples"
NAMES = ["first_agent_memory", "conversation_memory", "knowledge_graph"]
RECIPE_NAMES = ["core_memory_recipes", "extraction_recipes"]


@pytest.fixture
def lessons(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(EXAMPLES))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "NEO4J_URI": "neo4j+s://tutorial.databases.neo4j.io",
            "NEO4J_USERNAME": "tutorial-user",
            "NEO4J_PASSWORD": "synthetic-tutorial-password",
            "NEO4J_DATABASE": "tutorial-database",
        },
    )
    return {name: importlib.import_module(name) for name in NAMES + RECIPE_NAMES}


def calls():
    owners = {
        "short_term": ShortTermMemory,
        "long_term": LongTermMemory,
        "reasoning": ReasoningMemory,
        "query": BoltCypherQuery,
    }
    for name in NAMES + RECIPE_NAMES:
        for node in ast.walk(ast.parse((EXAMPLES / f"{name}.py").read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            receiver = node.func.value
            if (
                isinstance(receiver, ast.Attribute)
                and isinstance(receiver.value, ast.Name)
                and receiver.value.id == "client"
                and receiver.attr in owners
            ):
                yield pytest.param(
                    owners[receiver.attr], node, id=f"{name}:{node.lineno}:{node.func.attr}"
                )
            elif isinstance(receiver, ast.Name) and receiver.id == "store":
                yield pytest.param(
                    LongTermMemory, node, id=f"{name}:{node.lineno}:store.{node.func.attr}"
                )


@pytest.mark.parametrize("owner,node", list(calls()))
def test_tutorial_calls_bind_to_current_python_api(owner, node):
    method = getattr(owner, node.func.attr)  # A fabricated public method fails here.
    inspect.signature(method).bind(
        None, *[None for arg in node.args], **{kw.arg: None for kw in node.keywords}
    )


def test_shared_settings_construct_without_optional_extraction_models(lessons, monkeypatch):
    class OfflineProvider:
        dimensions = 1536

        async def embed(self, texts):
            return [[0.0] * self.dimensions for _ in texts]

        async def embed_one(self, _text):
            return [0.0] * self.dimensions

    def from_provider(spec, *, kind):
        assert spec == "openai/text-embedding-3-small" and kind == "embedding"
        return OfflineProvider()

    monkeypatch.setattr("neo4j_agent_memory.llm.from_provider", from_provider)
    settings = sys.modules["core_memory_settings"].settings()
    assert settings.backend == "bolt"
    assert settings.neo4j.uri == "neo4j+s://tutorial.databases.neo4j.io"
    assert settings.neo4j.username == "tutorial-user"
    assert settings.neo4j.password.get_secret_value() == "synthetic-tutorial-password"
    assert settings.neo4j.database == "tutorial-database"
    assert settings.extraction.extractor_type == "none"
    assert settings.llm is None
    assert settings.embedding.dimensions == 1536


def test_custom_extractor_uses_domain_schema_and_tuple_label_mapping(lessons):
    pipeline = lessons["knowledge_graph"].extractor()
    ner = pipeline._entity_extractor
    assert ner.entity_labels == lessons["knowledge_graph"].SCHEMA.entity_types
    assert ner.label_mapping["company"] == ("ORGANIZATION", None)
    assert (
        pipeline._relation_extractor.relation_types
        == lessons["knowledge_graph"].SCHEMA.relation_types
    )
    assert ner._model is None  # Constructor does not download the model.


def client_double():
    return SimpleNamespace(
        short_term=create_autospec(ShortTermMemory, instance=True),
        long_term=create_autospec(LongTermMemory, instance=True),
        reasoning=create_autospec(ReasoningMemory, instance=True),
        query=create_autospec(BoltCypherQuery, instance=True),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "example", ["first_agent_memory", "core_memory_recipes", "knowledge_graph"]
)
async def test_explicit_relationship_reads_match_the_actual_sdk_write_property(example):
    """Compare authored Cypher with the real write path, without fake read rows."""
    graph = SimpleNamespace(
        execute_write=AsyncMock(
            return_value=[{"id": str(uuid4()), "description": None, "confidence": 1.0}]
        )
    )
    store = LongTermMemory(graph)
    await store.add_relationship(uuid4(), uuid4(), "WORKS_AT")
    graph.execute_write.assert_awaited_once()
    write_query, parameters = graph.execute_write.await_args.args
    assert write_query == queries.CREATE_ENTITY_RELATIONSHIP
    assert parameters["relation_type"] == "WORKS_AT"
    stored_property = re.search(
        r"\[r:RELATED_TO\s*\{\s*(\w+)\s*:\s*\$relation_type\s*\}", write_query
    )
    assert stored_property, "Identify the actual property bound to relationship_type"

    # Python's AST joins adjacent literals, so this checks the full executable
    # query rather than a comment, method signature or independently canned row.
    read_queries = [
        node.value
        for node in ast.walk(ast.parse((EXAMPLES / f"{example}.py").read_text()))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "MATCH " in node.value
        and "RELATED_TO" in node.value
    ]
    assert len(read_queries) == 1
    read_property = re.search(r"\br\.(\w+)\s+(?:=\s*'WORKS_AT'|AS\s+relation\b)", read_queries[0])
    assert read_property, "Identify the logical name read by the example's Cypher"
    assert read_property[1] == stored_property[1], (
        f"{example} reads r.{read_property[1]}, but add_relationship writes r.{stored_property[1]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_relation", [None, "", "   ", 0])
async def test_knowledge_graph_inspection_rejects_any_empty_or_invalid_relation(
    lessons, invalid_relation, capsys
):
    lesson = lessons["knowledge_graph"]
    client = client_double()
    client.short_term.get_conversation.return_value = Conversation(
        session_id=lesson.SESSION,
        messages=[Message(role="user", content=text) for text in lesson.DOCUMENTS.values()],
    )
    client.query.cypher.side_effect = [
        [{"name": "Maya"}, {"name": "Northstar"}],
        [
            {"source": "Maya", "relation": "works_at", "target": "Northstar"},
            {"source": "Maya", "relation": invalid_relation, "target": "Northstar"},
        ],
    ]
    with pytest.raises(AssertionError, match="Expected nonempty logical relationship names"):
        await lesson.inspect_graph(client)
    assert "Verified:" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_knowledge_graph_inspection_returns_nonempty_stored_triples(lessons, capsys):
    lesson = lessons["knowledge_graph"]
    client = client_double()
    messages = [Message(role="user", content=text) for text in lesson.DOCUMENTS.values()]
    client.short_term.get_conversation.return_value = Conversation(
        session_id=lesson.SESSION, messages=messages
    )
    rows = [{"source": "Maya", "relation": "works_at", "target": "Northstar"}]
    client.query.cypher.side_effect = [[{"name": "Maya"}, {"name": "Northstar"}], rows]
    assert await lesson.inspect_graph(client) == (rows, messages)
    assert "Maya --works_at--> Northstar" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_restart_chat_passes_saved_history_and_scoped_preference_to_model(lessons):
    lesson = lessons["conversation_memory"]
    client = client_double()
    old = [
        Message(role="user", content="I need wide walking shoes for a city trip."),
        Message(role="assistant", content="I will look for wide walking shoes."),
    ]
    saved = Message(role="assistant", content="Consider the Trail Starter at USD 45.")
    client.short_term.get_conversation.side_effect = [
        Conversation(session_id=lesson.OLD_SESSION, messages=old),
        Conversation(session_id=lesson.NEW_SESSION, messages=[saved]),
    ]
    client.short_term.add_message.side_effect = [Message(role="user", content="Question"), saved]
    client.long_term.get_preferences_for.return_value = [
        Preference(category="budget", preference="Spend at most USD 60 on walking shoes")
    ]
    trace_id, step_id = uuid4(), uuid4()
    client.reasoning.start_trace.return_value = SimpleNamespace(id=trace_id)
    client.reasoning.add_step.return_value = SimpleNamespace(id=step_id)
    client.reasoning.get_trace_with_steps.return_value = SimpleNamespace(
        steps=[SimpleNamespace(tool_calls=[object()])]
    )
    client.query.cypher.return_value = [{"description": lesson.PRODUCT}]
    llm = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=saved.content))]
                    )
                )
            )
        )
    )
    messages = await lesson.resume(client, llm, "account-model")
    client.long_term.get_preferences_for.assert_awaited_once_with(lesson.USER)
    assert messages[1:3] == [{"role": m.role.value, "content": m.content} for m in old]
    assert "USD 60" in messages[0]["content"] and lesson.PRODUCT in messages[0]["content"]
    llm.chat.completions.create.assert_awaited_once_with(model="account-model", messages=messages)
    client.reasoning.complete_trace.assert_awaited_once_with(
        trace_id, outcome="Saved a response using retrieved shopper context", success=True
    )


@pytest.mark.asyncio
async def test_chat_failure_completes_trace_and_does_not_save_assistant_answer(lessons):
    lesson = lessons["conversation_memory"]
    client = client_double()
    client.short_term.get_conversation.return_value = Conversation(
        session_id=lesson.OLD_SESSION,
        messages=[
            Message(role="user", content="Question"),
            Message(role="assistant", content="Answer"),
        ],
    )
    client.long_term.get_preferences_for.return_value = [
        Preference(category="budget", preference="USD 60")
    ]
    client.short_term.add_message.return_value = Message(role="user", content="New question")
    trace_id = uuid4()
    client.reasoning.start_trace.return_value = SimpleNamespace(id=trace_id)
    client.reasoning.add_step.return_value = SimpleNamespace(id=uuid4())
    client.query.cypher.return_value = [{"description": lesson.PRODUCT}]
    llm = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(side_effect=RuntimeError("provider unavailable"))
            )
        )
    )
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await lesson.resume(client, llm, "account-model")
    client.reasoning.complete_trace.assert_awaited_once_with(
        trace_id, outcome="Response failed: RuntimeError", success=False
    )
    assert client.short_term.add_message.await_count == 1


@pytest.mark.asyncio
async def test_document_relations_use_exact_endpoints_and_record_provenance(lessons):
    lesson = lessons["knowledge_graph"]
    client = client_double()
    message = Message(role="user", content="Maya works at Northstar")
    client.short_term.add_message.return_value = message
    maya = Entity(name="Maya", type="PERSON")
    company = Entity(name="Northstar", type="ORGANIZATION")
    client.long_term.add_entity.side_effect = [(maya, None), (company, None)]
    persisted_maya_id = uuid4()
    client.query.cypher.side_effect = [[{"id": str(persisted_maya_id)}], [{"id": str(company.id)}]]
    result = ExtractionResult(
        entities=[
            ExtractedEntity(name="Maya", type="PERSON"),
            ExtractedEntity(name="Northstar", type="ORGANIZATION"),
        ],
        relations=[
            ExtractedRelation(source="Maya", target="Northstar", relation_type="works_at"),
            ExtractedRelation(source="Maya", target="Unresolved", relation_type="works_at"),
        ],
    )
    assert await lesson.store_document(client, "source.txt", message.content, result) == 1
    client.long_term.add_relationship.assert_awaited_once_with(
        source=maya.model_copy(update={"id": persisted_maya_id}),
        target=company,
        relationship_type="works_at",
        confidence=1.0,
    )
    assert client.long_term.link_entity_to_message.await_count == 2
    client.long_term.search_entities.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_document_extraction_fails_before_writing(lessons):
    client = client_double()
    with pytest.raises(RuntimeError, match="No entities extracted"):
        await lessons["knowledge_graph"].store_document(
            client, "empty.txt", "text", ExtractionResult()
        )
    client.short_term.add_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_mentions_of_same_persisted_entity_are_not_ambiguous(lessons):
    client = client_double()
    message = Message(role="user", content="Maya works at Northstar. Northstar is in Denver.")
    client.short_term.add_message.return_value = message
    maya = Entity(name="Maya", type="PERSON")
    first = Entity(name="Northstar", type="ORGANIZATION")
    second = Entity(name="Northstar", type="ORGANIZATION")
    client.long_term.add_entity.side_effect = [(maya, None), (first, None), (second, None)]
    client.query.cypher.side_effect = [
        [{"id": str(maya.id)}],
        [{"id": str(first.id)}],
        [{"id": str(first.id)}],
    ]
    result = ExtractionResult(
        entities=[ExtractedEntity(name=e.name, type=e.type) for e in (maya, first, second)],
        relations=[ExtractedRelation(source="Maya", target="Northstar", relation_type="works_at")],
    )
    assert (
        await lessons["knowledge_graph"].store_document(
            client, "source.txt", message.content, result
        )
        == 1
    )
    client.long_term.add_relationship.assert_awaited_once()
    edge = client.long_term.add_relationship.await_args.kwargs
    assert edge["source"].id == maya.id and edge["target"].id == first.id
    assert edge["relationship_type"] == "works_at" and edge["confidence"] == 1.0


def test_pages_include_the_complete_maintained_programs():
    for page, fixture in zip(
        ["first-agent-memory", "conversation-memory", "knowledge-graph"], NAMES, strict=True
    ):
        text = (ROOT / "docs/modules/ROOT/pages/tutorials" / f"{page}.adoc").read_text()
        assert f"include::example${fixture}.py[]" in text
        assert "include::example$core_memory_settings.py[]" in text
        assert "include::partial$aura-tutorial-setup.adoc[]" in text
        assert "include::partial$aura-tutorial-cleanup.adoc[]" in text
        assert "sleep 30" not in text


@pytest.mark.parametrize(
    "page,extras,program",
    [
        ("first-agent-memory", {"openai"}, "first_agent_memory.py"),
        ("conversation-memory", {"openai"}, "conversation_memory.py"),
        ("knowledge-graph", {"openai", "gliner", "spacy"}, "knowledge_graph.py"),
        (
            "anthropic-and-local-embeddings",
            {"anthropic", "sentence-transformers"},
            "anthropic_local_memory.py",
        ),
        (
            "microsoft-agent-memory",
            {"microsoft-agent", "openai"},
            "microsoft_shopping_tutorial.py",
        ),
        ("strands-agent-quickstart", {"strands", "bedrock"}, "strands_memory_tutorial.py"),
        ("mcp-server", {"mcp", "sentence-transformers"}, "mcp_local_tutorial.py"),
        ("nams-quickstart", {"nams"}, "nams_quickstart.py"),
        ("ontology-quickstart", {"nams"}, "ontology_quickstart.py"),
        ("skills-quickstart", {"nams"}, "skills_quickstart.py"),
    ],
)
def test_python_tutorials_install_published_sdk_and_run_local_files(page, extras, program):
    text = (ROOT / "docs/modules/ROOT/pages/tutorials" / f"{page}.adoc").read_text()
    assert "include::partial$python-tutorial-setup.adoc[]" in text
    assert f".Save as `{program}`\n[source,python]\n----\ninclude::example${program}[]" in text
    assert "attachment$" not in text and "downloadable tutorial" not in text
    assert "git clone" not in text
    assert "docs/modules/ROOT/examples/" not in text
    commands = [
        shlex.split(line)
        for block in re.findall(r"\[source,bash\]\n----\n(.*?)\n----", text, re.S)
        for line in block.splitlines()
        if line.startswith("python ") and not line.endswith("\\")
    ]
    installed = [
        Requirement(argument)
        for command in commands
        if command[:4] == ["python", "-m", "pip", "install"]
        for argument in command[4:]
    ]
    sdk = [requirement for requirement in installed if requirement.name == "neo4j-agent-memory"]
    assert len(sdk) == 1
    assert sdk[0].extras == extras
    assert str(sdk[0].specifier) == "==0.6.0"
    assert sdk[0].url is None
    scripts = {command[1] for command in commands if command[1].endswith(".py")}
    # The Skills seed spans lines; later standalone phases still identify the program.
    assert program in scripts
    for script in scripts:
        path = Path(script)
        assert not path.is_absolute() and ".." not in path.parts
        assert (EXAMPLES / path).is_file(), f"Missing maintained local program: {script}"
    if page == "knowledge-graph":
        requirements = {requirement.name: requirement for requirement in installed}
        assert "glirel" in requirements
        assert str(requirements["loguru"].specifier) == "<1,>=0.7"
    if page == "microsoft-agent-memory":
        assert any(requirement.name == "agent-framework-openai" for requirement in installed)


def test_python_local_setup_keeps_sdk_and_existing_state_separate():
    text = (ROOT / "docs/modules/ROOT/partials/python-tutorial-setup.adoc").read_text()
    commands = [
        shlex.split(line)
        for block in re.findall(r"\[source,bash\]\n----\n(.*?)\n----", text, re.S)
        for line in block.splitlines()
    ]
    assert ["mkdir", "-p", "~/agent-memory-tutorials"] in commands
    assert ["cd", "~/agent-memory-tutorials"] in commands
    assert ["python3", "-m", "venv", ".venv"] in commands
    assert ["source", ".venv/bin/activate"] in commands
    assert not any("--system-site-packages" in command for command in commands)
    assert "attachment$" not in text and "zipfile" not in text
    assert "Save as" in text and "Copy the entire block" in text
    assert ".tutorial-state/" in text and "Reuse unchanged helper files" in text


def test_repeated_python_setup_preserves_files_and_activates_existing_environment(tmp_path):
    """Execute the authored shell block and preserve existing files/environment."""
    folder = tmp_path / "agent-memory-tutorials"
    original = {
        "first_agent_memory.py": "# existing edited lesson\n",
        ".env": "SYNTHETIC_CONFIG=keep\n",
        ".tutorial-state/nams.json": '{"pending":"keep recorded operation"}\n',
        ".venv/bin/activate": "printf '%s\\n' 'Existing environment activated'\n",
        ".venv/pyvenv.cfg": "existing environment sentinel\n",
    }
    for relative, content in original.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    calls = tmp_path / "python-calls.txt"
    python = binaries / "python3"
    python.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' called >> {shlex.quote(str(calls))}\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    python.chmod(0o755)
    text = (ROOT / "docs/modules/ROOT/partials/python-tutorial-setup.adoc").read_text()
    block = re.findall(r"\[source,bash\]\n----\n(.*?)\n----", text, re.S)[0]
    block = block.replace("~/agent-memory-tutorials", shlex.quote(str(folder)))
    result = subprocess.run(
        ["/bin/bash", "-c", block],
        cwd=tmp_path,
        env={"PATH": str(binaries) + os.pathsep + os.defpath},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Using the existing virtual environment" in result.stdout
    assert "Existing environment activated" in result.stdout
    assert not calls.exists(), "Continuation must not recreate the environment"
    assert {relative: (folder / relative).read_text() for relative in original} == original


class LocalContractExtractor:
    """Small no-network extractor used to exercise the real pipeline and chunker."""

    name = "offline-contract"

    async def extract(self, text, **_kwargs):
        if "FAIL" in text:
            raise ValueError("selected input failure")
        return ExtractionResult(
            entities=[ExtractedEntity(name="Maya", type="PERSON")], source_text=text
        )


@pytest.mark.asyncio
async def test_recipe_batch_accounts_for_all_indexes_with_real_pipeline(lessons):
    recipe = lessons["extraction_recipes"]
    result = await recipe.batch(LocalContractExtractor(), ["Maya", "Maya again"])
    assert result.successful_items == 2
    assert [item.index for item in result.results] == [0, 1]


@pytest.mark.asyncio
async def test_recipe_batch_reports_failure_instead_of_empty_success(lessons, capsys):
    recipe = lessons["extraction_recipes"]
    with pytest.raises(RuntimeError, match="Retry failed source indexes"):
        await recipe.batch(LocalContractExtractor(), ["Maya", "FAIL"])
    assert "Input 1 failed: selected input failure" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_recipe_streaming_accounts_for_actual_chunks(lessons):
    recipe = lessons["extraction_recipes"]
    result = await recipe.streaming(LocalContractExtractor(), "Maya works at Northstar. " * 45)
    assert len(result.chunk_results) > 1
    assert all(chunk.success for chunk in result.chunk_results)
    assert result.entities[0].name == "Maya"


@pytest.mark.asyncio
async def test_recipe_streaming_reports_chunk_errors(lessons):
    recipe = lessons["extraction_recipes"]
    with pytest.raises(RuntimeError, match="Chunk extraction failed"):
        await recipe.streaming(LocalContractExtractor(), "FAIL " * 100)


def test_recipe_custom_schema_preserves_type_and_subtype(lessons):
    selected = lessons["extraction_recipes"].custom_extractor()
    assert selected.label_mapping == {
        "customer": ("PERSON", None),
        "product": ("OBJECT", "PRODUCT"),
    }


@pytest.mark.asyncio
async def test_failure_recipe_records_error_and_reads_completed_failure(lessons):
    from neo4j_agent_memory.core.memory import ToolCallStatus

    client = client_double()
    trace_id, step_id = uuid4(), uuid4()
    client.reasoning.start_trace.return_value = SimpleNamespace(id=trace_id)
    client.reasoning.add_step.return_value = SimpleNamespace(id=step_id)
    client.reasoning.get_trace_with_steps.return_value = SimpleNamespace(
        success=False,
        steps=[SimpleNamespace(tool_calls=[SimpleNamespace(status=ToolCallStatus.ERROR)])],
    )
    await lessons["core_memory_recipes"].reasoning(client, "offline")
    call = client.reasoning.record_tool_call.call_args
    assert call.kwargs["status"] == ToolCallStatus.ERROR
    outcome = client.reasoning.complete_trace.call_args.kwargs["outcome"]
    assert outcome.success is False and outcome.error_kind == "no_results"
    client.reasoning.get_trace_with_steps.assert_awaited_once_with(trace_id)


def test_howto_tag_includes_resolve_to_actual_functions():
    import re

    pages = [
        "messages",
        "entities",
        "preferences",
        "reasoning-traces",
        "deduplication",
        "entity-extraction",
        "entity-extraction-schemas",
        "entity-extraction-batch",
    ]
    for name in pages:
        text = (ROOT / "docs/modules/ROOT/pages/how-to" / f"{name}.adoc").read_text()
        for fixture, tags in re.findall(r"include::example\$(.+?)\[tags?=(.+?)\]", text):
            source = (EXAMPLES / fixture).read_text()
            for tag in tags.split(";"):
                assert f"# tag::{tag}[]" in source
                assert f"# end::{tag}[]" in source


def test_legacy_inline_anchors_are_separated_from_asciidoc_block_boundaries():
    """An adjacent heading becomes literal paragraph text after an inline anchor."""
    import re

    pages = ROOT / "docs/modules/ROOT/pages"
    selected = [
        pages / "tutorials" / f"{name}.adoc"
        for name in ("first-agent-memory", "conversation-memory", "knowledge-graph")
    ]
    selected += [
        pages / "how-to" / f"{name}.adoc"
        for name in (
            "messages",
            "entities",
            "preferences",
            "reasoning-traces",
            "deduplication",
            "entity-extraction",
            "entity-extraction-schemas",
            "entity-extraction-batch",
        )
    ]
    selected += list((pages / "reference/api").glob("*.adoc"))
    selected += [
        pages / "reference" / f"{name}.adoc"
        for name in (
            "configuration",
            "environment-variables",
            "cli",
            "extractors",
            "schemas",
            "schema-objects",
            "rest-api",
        )
    ]
    for path in selected:
        assert not re.search(
            r"^anchor:[^\n]+\n(?:={1,6} |\[|----|image::)", path.read_text(), re.M
        ), path
