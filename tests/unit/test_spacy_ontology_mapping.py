"""spaCy under an ontology: labels land only on pairs spaCy actually decides.

``spacy_label_map`` used to send every spaCy label to the *first* ontology
label of its POLE+O type, and ``SpacyEntityExtractor`` typed every label the
map left out as ``OBJECT``. Under the ``medical`` template that made amounts
diseases; under ``scientific`` every person an author. These tests use a fake
``nlp`` so no spaCy model is needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from neo4j_agent_memory.config.settings import ExtractionConfig
from neo4j_agent_memory.extraction.factory import create_spacy_extractor
from neo4j_agent_memory.extraction.spacy_extractor import SpacyEntityExtractor
from neo4j_agent_memory.ontology.builtin import POLEO_ONTOLOGY, get_template


def _fake_nlp(*ents: tuple[str, str]) -> Any:
    """An ``nlp`` callable whose doc carries ``(text, label)`` entities."""

    def nlp(text: str) -> Any:
        spans = []
        for value, label in ents:
            start = text.index(value)
            spans.append(
                SimpleNamespace(
                    text=value, label_=label, start_char=start, end_char=start + len(value)
                )
            )
        return SimpleNamespace(ents=spans)

    return nlp


TEXT = "Ada Lovelace paid $5 to Acme in London"
ENTS = (("Ada Lovelace", "PERSON"), ("$5", "MONEY"), ("Acme", "ORG"), ("London", "GPE"))


def _extract(extractor: SpacyEntityExtractor) -> list[tuple[str, str, str | None]]:
    extractor._nlp = _fake_nlp(*ENTS)
    return [(e.name, e.type, e.subtype) for e in extractor._extract_sync(TEXT)]


def test_the_medical_template_gets_nothing_from_spacy() -> None:
    extractor = create_spacy_extractor(ExtractionConfig(), ontology=get_template("medical"))
    assert isinstance(extractor, SpacyEntityExtractor)
    assert extractor.drop_unmapped is True
    assert _extract(extractor) == []


def test_the_news_template_keeps_what_it_declares_bare() -> None:
    extractor = create_spacy_extractor(ExtractionConfig(), ontology=get_template("news"))
    assert isinstance(extractor, SpacyEntityExtractor)
    assert _extract(extractor) == [
        ("Ada Lovelace", "PERSON", None),
        ("Acme", "ORGANIZATION", None),
        # spaCy's GEOPOLITICAL subtype is not declared, so the bare type is used.
        ("London", "LOCATION", None),
    ]


def test_a_base_type_only_ontology_keeps_spacys_defaults() -> None:
    extractor = create_spacy_extractor(ExtractionConfig(), ontology=POLEO_ONTOLOGY)
    assert isinstance(extractor, SpacyEntityExtractor)
    assert extractor.drop_unmapped is False
    assert ("$5", "OBJECT", "CURRENCY") in _extract(extractor)


@pytest.mark.parametrize("ontology", [None, POLEO_ONTOLOGY])
def test_without_a_subtyped_ontology_unknown_labels_still_default_to_object(ontology) -> None:
    extractor = create_spacy_extractor(ExtractionConfig(), ontology=ontology)
    assert isinstance(extractor, SpacyEntityExtractor)
    extractor._nlp = _fake_nlp(("Thing", "SOMETHING_NEW"))
    assert [(e.type) for e in extractor._extract_sync("Thing")] == ["OBJECT"]
