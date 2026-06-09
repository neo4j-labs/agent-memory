"""Integration & end-to-end tests for ontology-aware LLM extraction (0.6.0).

These exercise the full wiring: ``MemorySettings`` (rich schema + a mock LLM
provider) → ``MemoryClient._create_extractor`` → factory precedence resolution →
``LLMEntityExtractor`` → ``add_message`` extraction → persisted ``:Entity`` nodes
and ``MENTIONS`` / ``RELATED_TO`` edges in Neo4j, then queried back through the
client API.

A mock ``StructuredExtractor`` stands in for a real LLM so the tests are
deterministic and need no API key, but every other layer is real (Neo4j via the
testcontainer, the factory, the extractor, the storage path).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from neo4j_agent_memory import MemoryClient, MemorySettings
from neo4j_agent_memory.config.settings import (
    ExtractionConfig,
    ExtractorType,
    SchemaConfig,
    SchemaModel,
)
from neo4j_agent_memory.schema.models import (
    EntitySchemaConfig,
    EntityTypeConfig,
    RelationTypeConfig,
)

# --------------------------------------------------------------------------- #
# Mock providers (deterministic stand-ins for a real LLM)
# --------------------------------------------------------------------------- #


class ScriptedStructured:
    """Returns a fixed entity/relation list regardless of input."""

    model = "scripted"

    def __init__(self, entities, relations=None):
        self._entities = entities
        self._relations = relations or []

    async def complete_structured(self, messages, response_model, **kwargs):
        return response_model(entities=self._entities, relations=self._relations)

    async def complete(self, messages, **kwargs):  # pragma: no cover
        raise NotImplementedError


class TextDrivenStructured:
    """Emits entities whose surface form appears in the message text.

    Makes the end-to-end flow meaningful: the extractor builds a prompt from the
    real message content, and this mock 'extracts' the entities it actually
    contains.
    """

    model = "text-driven"

    KNOWN = {
        "adopt the local transcription tool": ("Adopt the local transcription tool", "DECISION"),
        "ship the hume file-drop": ("Ship the Hume file-drop", "DECISION"),
        "hume": ("Hume", "PRODUCT"),
        "sudhir hasbe": ("Sudhir Hasbe", "PERSON"),
        "gartner": ("Gartner", "ORGANIZATION"),
    }

    async def complete_structured(self, messages, response_model, **kwargs):
        text = messages[-1].content.lower()
        seen = set()
        entities = []
        for key, (name, typ) in self.KNOWN.items():
            if key in text and name not in seen:
                seen.add(name)
                entities.append({"name": name, "type": typ, "confidence": 0.9})
        return response_model(entities=entities)

    async def complete(self, messages, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _meeting_schema(strict: bool = False) -> EntitySchemaConfig:
    return EntitySchemaConfig(
        name="meeting",
        strict_types=strict,
        entity_types=[
            EntityTypeConfig(
                name="DECISION",
                description="A concrete agreement, resolution, or choice made in a meeting.",
                examples=["Adopt local transcription tool"],
            ),
            EntityTypeConfig(name="TASK", description="An action item."),
            EntityTypeConfig(name="OUTCOME", description="A result or impact."),
            EntityTypeConfig(name="PRODUCT", description="A software product or feature."),
            EntityTypeConfig(name="PERSON"),
            EntityTypeConfig(name="ORGANIZATION"),
        ],
        relation_types=[
            RelationTypeConfig(
                name="DECIDED_BY",
                description="Who made the decision.",
                source_types=["DECISION"],
                target_types=["PERSON"],
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# Client factory fixture
# --------------------------------------------------------------------------- #


@pytest.fixture
async def make_ontology_client(memory_settings, mock_embedder, mock_resolver):
    """Build connected MemoryClients wired with a rich schema + mock LLM.

    The extractor is built by the factory from settings (not injected), so the
    full precedence-resolution path is exercised.
    """
    created: list[MemoryClient] = []

    async def _make(
        *,
        llm,
        entity_schema=None,
        custom_schema_path=None,
        strict_types=False,
        extract_relations=False,
    ) -> MemoryClient:
        schema_kwargs: dict = {"model": SchemaModel.CUSTOM, "strict_types": strict_types}
        if entity_schema is not None:
            schema_kwargs["entity_schema"] = entity_schema
        if custom_schema_path is not None:
            schema_kwargs["custom_schema_path"] = custom_schema_path

        settings = MemorySettings(
            neo4j=memory_settings.neo4j,
            schema_config=SchemaConfig(**schema_kwargs),
            extraction=ExtractionConfig(
                extractor_type=ExtractorType.LLM,
                enable_spacy=False,
                enable_gliner=False,
                extract_relations=extract_relations,
                extract_preferences=False,
            ),
            llm=llm,
        )
        client = MemoryClient(settings, embedder=mock_embedder, resolver=mock_resolver)
        try:
            await client.connect()
        except Exception as e:  # pragma: no cover - infra
            pytest.skip(f"Neo4j not available: {e}")
        # Clean slate per built client.
        await client._client.execute_write("MATCH (n) DETACH DELETE n")
        created.append(client)
        return client

    yield _make

    for client in created:
        try:
            await client._client.execute_write("MATCH (n) DETACH DELETE n")
        except Exception:  # pragma: no cover
            pass
        await client.close()


async def _mentioned_entities(client: MemoryClient, message_id) -> list[dict]:
    rows = await client._client.execute_read(
        """
        MATCH (m:Message {id: $id})-[:MENTIONS]->(e:Entity)
        RETURN e.name AS name, e.type AS type
        ORDER BY name
        """,
        {"id": str(message_id)},
    )
    return rows


# --------------------------------------------------------------------------- #
# Integration: extraction → storage
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.asyncio
class TestCustomTypePersistence:
    async def test_custom_typed_entities_persisted(self, make_ontology_client, session_id):
        """A described custom type reaches the prompt AND lands in Neo4j."""
        provider = ScriptedStructured(
            entities=[
                {"name": "Adopt local transcription tool", "type": "DECISION", "confidence": 0.9},
                {"name": "Sudhir Hasbe", "type": "PERSON", "confidence": 0.95},
            ]
        )
        client = await make_ontology_client(llm=provider, entity_schema=_meeting_schema())

        message = await client.short_term.add_message(
            session_id,
            "user",
            "Decisions: adopt the local transcription tool. Owner: Sudhir Hasbe.",
            extract_entities=True,
        )

        rows = await _mentioned_entities(client, message.id)
        types = {r["type"] for r in rows}
        assert "DECISION" in types
        assert "PERSON" in types

        # The custom type also became a PascalCase label.
        label_rows = await client._client.execute_read("MATCH (e:Decision) RETURN count(e) AS cnt")
        assert label_rows[0]["cnt"] >= 1

    async def test_intra_call_dedup_persists_single_node(self, make_ontology_client, session_id):
        """Hume emitted under three types collapses to one PRODUCT node on store."""
        provider = ScriptedStructured(
            entities=[
                {"name": "Hume", "type": "ORGANIZATION", "confidence": 0.7},
                {"name": "Hume", "type": "PERSON", "confidence": 0.8},
                {"name": "Hume", "type": "PRODUCT", "confidence": 0.95},
            ]
        )
        client = await make_ontology_client(llm=provider, entity_schema=_meeting_schema())

        message = await client.short_term.add_message(
            session_id, "user", "Hume shipped.", extract_entities=True
        )

        rows = await _mentioned_entities(client, message.id)
        humes = [r for r in rows if r["name"] == "Hume"]
        assert len(humes) == 1
        assert humes[0]["type"] == "PRODUCT"

    async def test_relations_persisted(self, make_ontology_client, session_id):
        """Extracted relations are stored as RELATED_TO edges."""
        provider = ScriptedStructured(
            entities=[
                {"name": "Adopt tool", "type": "DECISION", "confidence": 0.9},
                {"name": "Sudhir Hasbe", "type": "PERSON", "confidence": 0.9},
            ],
            relations=[
                {
                    "source": "Adopt tool",
                    "target": "Sudhir Hasbe",
                    "relation_type": "DECIDED_BY",
                    "confidence": 0.9,
                }
            ],
        )
        client = await make_ontology_client(
            llm=provider, entity_schema=_meeting_schema(), extract_relations=True
        )

        await client.short_term.add_message(
            session_id,
            "user",
            "Decision Adopt tool decided by Sudhir Hasbe.",
            extract_entities=True,
            extract_relations=True,
        )

        rel_rows = await client._client.execute_read(
            """
            MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity)
            RETURN a.name AS src, b.name AS tgt, r.relation_type AS rel
            """
        )
        assert any(
            r["src"] == "Adopt tool" and r["tgt"] == "Sudhir Hasbe" and r["rel"] == "DECIDED_BY"
            for r in rel_rows
        )

    async def test_custom_schema_path_flow(self, make_ontology_client, session_id):
        """A schema loaded from custom_schema_path drives extraction end-to-end."""
        schema_dict = {
            "name": "meeting-file",
            "entity_types": [
                {"name": "DECISION", "description": "A ratified choice."},
                {"name": "PERSON"},
            ],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(schema_dict, f)
            path = f.name
        try:
            provider = ScriptedStructured(
                entities=[{"name": "Adopt tool", "type": "DECISION", "confidence": 0.9}]
            )
            client = await make_ontology_client(llm=provider, custom_schema_path=path)
            message = await client.short_term.add_message(
                session_id, "user", "Decisions: adopt tool.", extract_entities=True
            )
            rows = await _mentioned_entities(client, message.id)
            assert any(r["type"] == "DECISION" for r in rows)
        finally:
            Path(path).unlink(missing_ok=True)

    async def test_strict_types_persists_allowed_types(self, make_ontology_client, session_id):
        """Under strict_types the constrained model accepts in-schema types and stores them."""
        provider = ScriptedStructured(
            entities=[{"name": "Adopt tool", "type": "DECISION", "confidence": 0.9}]
        )
        client = await make_ontology_client(
            llm=provider, entity_schema=_meeting_schema(strict=True), strict_types=True
        )
        message = await client.short_term.add_message(
            session_id, "user", "Decisions: adopt tool.", extract_entities=True
        )
        rows = await _mentioned_entities(client, message.id)
        assert any(r["type"] == "DECISION" for r in rows)


# --------------------------------------------------------------------------- #
# End-to-end: multi-message conversation, queried back through the client API
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.asyncio
class TestEndToEndConversation:
    async def test_conversation_extracts_and_is_queryable(self, make_ontology_client, session_id):
        provider = TextDrivenStructured()
        client = await make_ontology_client(llm=provider, entity_schema=_meeting_schema())

        turns = [
            "Decisions: adopt the local transcription tool; ship the Hume file-drop by Q3.",
            "Sudhir Hasbe will own the rollout.",
            "Gartner was cited for event branding.",
        ]
        for content in turns:
            await client.short_term.add_message(session_id, "user", content, extract_entities=True)

        # All extracted types present in the graph.
        type_rows = await client._client.execute_read(
            "MATCH (e:Entity) RETURN DISTINCT e.type AS type"
        )
        types = {r["type"] for r in type_rows}
        assert {"DECISION", "PRODUCT", "PERSON", "ORGANIZATION"} <= types

        # Two DECISION items were ratified across the conversation.
        decision_rows = await client._client.execute_read(
            "MATCH (e:Entity {type: 'DECISION'}) RETURN e.name AS name ORDER BY name"
        )
        decision_names = {r["name"] for r in decision_rows}
        assert "Adopt the local transcription tool" in decision_names
        assert "Ship the Hume file-drop" in decision_names

        # The conversation is retrievable through the public API with all turns.
        conversation = await client.short_term.get_conversation(session_id)
        assert len(conversation.messages) == len(turns)

        # Entities are reachable from their originating messages via MENTIONS.
        mention_rows = await client._client.execute_read(
            """
            MATCH (m:Message)-[:MENTIONS]->(e:Entity {type: 'PRODUCT'})
            RETURN e.name AS name
            """
        )
        assert any(r["name"] == "Hume" for r in mention_rows)

    async def test_default_poleo_path_unaffected(
        self, memory_settings, mock_embedder, mock_resolver, session_id
    ):
        """A default (name-only, POLE+O) client still extracts and stores normally."""
        provider = ScriptedStructured(
            entities=[{"name": "Acme Corp", "type": "ORGANIZATION", "confidence": 0.9}]
        )
        settings = MemorySettings(
            neo4j=memory_settings.neo4j,
            extraction=ExtractionConfig(
                extractor_type=ExtractorType.LLM,
                enable_spacy=False,
                enable_gliner=False,
                extract_relations=False,
                extract_preferences=False,
            ),
            llm=provider,
        )
        client = MemoryClient(settings, embedder=mock_embedder, resolver=mock_resolver)
        try:
            await client.connect()
        except Exception as e:  # pragma: no cover
            pytest.skip(f"Neo4j not available: {e}")
        try:
            await client._client.execute_write("MATCH (n) DETACH DELETE n")
            message = await client.short_term.add_message(
                session_id, "user", "Acme Corp announced earnings.", extract_entities=True
            )
            rows = await _mentioned_entities(client, message.id)
            assert any(r["type"] == "ORGANIZATION" and r["name"] == "Acme Corp" for r in rows)
        finally:
            await client._client.execute_write("MATCH (n) DETACH DELETE n")
            await client.close()
