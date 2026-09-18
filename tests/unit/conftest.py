"""Shared fixtures for the unit suite.

The CI ``test`` job installs no extras, so nothing here may depend on
``gliner2`` being importable. The :func:`gliner2_stub` fixture installs a
stand-in package into ``sys.modules`` for the duration of a test — via
``monkeypatch.setitem`` so the real module (which *is* installed in a
developer's ``--all-extras`` environment) is restored afterwards and the
stub wins while the test runs.

The stub mirrors the parts of the 2.0.0 API the extractor touches, with the
real result dataclasses so the field names and the "relation endpoints must
reference entities in this result" invariant are exercised for real.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass(frozen=True)
class StubJointEntity:
    """Mirror of ``gliner2.joint_ie.result.JointEntity``."""

    id: str
    type: str
    text: str
    start: int
    end: int
    confidence: float | None = None
    sentence_id: int | None = None
    rescued: bool = False


@dataclass(frozen=True)
class StubJointRelation:
    """Mirror of ``gliner2.joint_ie.result.JointRelation`` (endpoints are ids)."""

    type: str
    head: str
    tail: str
    confidence: float | None = None
    derived: bool = False


@dataclass
class StubJointResult:
    """Mirror of ``gliner2.joint_ie.result.JointResult``."""

    text: str = ""
    entities: list[StubJointEntity] = field(default_factory=list)
    relations: list[StubJointRelation] = field(default_factory=list)
    feasible: bool = True

    def __post_init__(self) -> None:
        known = {entity.id for entity in self.entities}
        if any(rel.head not in known or rel.tail not in known for rel in self.relations):
            raise ValueError("relation endpoints must reference entities in this result")


@dataclass
class StubJointIEConfig:
    """Mirror of ``gliner2.joint_ie.JointIEConfig`` (the fields we set)."""

    entity_threshold: float | None = None
    include_confidence: bool = True
    include_spans: bool = True
    max_len: int | None = None


class StubAttributeGroup:
    """Mirror of ``gliner2.AttributeGroup``."""

    def __init__(
        self,
        labels: list[str],
        multi_label: bool = False,
        threshold: float = 0.5,
        applies_to: list[str] | None = None,
        qualify_labels: bool = False,
    ) -> None:
        self.labels = labels
        self.multi_label = multi_label
        self.threshold = threshold
        self.applies_to = applies_to
        self.qualify_labels = qualify_labels


class StubSchema:
    """Mirror of ``gliner2.Schema`` — records the attribute groups attached.

    ``entity_attributes`` enforces the real 2.0.0 precondition: every
    ``applies_to`` label must already have been declared via ``entities()``.
    """

    def __init__(self) -> None:
        self.attribute_groups: dict[str, StubAttributeGroup] = {}
        self.declared_entities: dict[str, str] = {}
        self.calls: list[str] = []

    def entities(self, entities: dict[str, str]) -> StubSchema:
        self.calls.append("entities")
        self.declared_entities.update(entities)
        return self

    def entity_attributes(self, groups: dict[str, StubAttributeGroup]) -> StubSchema:
        self.calls.append("entity_attributes")
        if not self.declared_entities:
            raise ValueError("entity_attributes() requires entities() to be called first")
        for group in groups.values():
            for label in group.applies_to or []:
                if label not in self.declared_entities:
                    raise ValueError(f"entity_attributes() references undeclared entity {label!r}")
        self.attribute_groups.update(groups)
        return self


class StubJointSchema:
    """Mirror of ``gliner2.joint_ie.JointSchema`` — records every call."""

    def __init__(self) -> None:
        self.entities: list[tuple[str, str | None, dict[str, Any]]] = []
        self.relations: list[tuple[str, Any, Any, str | None, dict[str, Any]]] = []
        self.no_self_loop_calls: list[str | None] = []

    def entity(self, name: str, description: str | None = None, **kwargs: Any) -> StubJointSchema:
        self.entities.append((name, description, kwargs))
        return self

    def relation(
        self,
        name: str,
        head: Any,
        tail: Any,
        description: str | None = None,
        **kwargs: Any,
    ) -> StubJointSchema:
        self.relations.append((name, head, tail, description, kwargs))
        return self

    def no_self_loops(self, relation: str | None = None) -> StubJointSchema:
        self.no_self_loop_calls.append(relation)
        return self

    @property
    def entity_names(self) -> list[str]:
        return [name for name, _, _ in self.entities]

    @property
    def relation_names(self) -> list[str]:
        return [name for name, _, _, _, _ in self.relations]


class StubModel:
    """A loaded checkpoint: records load kwargs and attribute-pass calls."""

    def __init__(self, model_id: str, **load_kwargs: Any) -> None:
        self.model_id = model_id
        self.load_kwargs = load_kwargs
        self.extract_calls: list[tuple[str, Any, dict[str, Any]]] = []
        #: Result handed back by :meth:`extract` (the attribute pass).
        self.attribute_result: dict[str, Any] = {"entities": {}}

    def create_schema(self) -> StubSchema:
        return StubSchema()

    def extract(self, text: str, schema: Any, **kwargs: Any) -> dict[str, Any]:
        self.extract_calls.append((text, schema, kwargs))
        return self.attribute_result


class StubAutoExtractor:
    """Mirror of ``gliner2.AutoExtractor`` — records every load."""

    loads: list[tuple[str, dict[str, Any]]] = []

    @classmethod
    def from_pretrained(cls, model_id: str, **kwargs: Any) -> StubModel:
        cls.loads.append((model_id, kwargs))
        return StubModel(model_id, **kwargs)


class StubJointIEEngine:
    """Mirror of ``gliner2.joint_ie.JointIEEngine``.

    Hands back whatever is queued on :attr:`results` (falling back to an
    empty result) and records how it was called.
    """

    instances: list[StubJointIEEngine] = []

    def __init__(self, model: Any, *, device: Any = None, dtype: Any = None) -> None:
        self.model = model
        self.device = device
        self.dtype = dtype
        self.results: list[StubJointResult] = []
        self.extract_calls: list[tuple[str, Any, Any]] = []
        self.batch_calls: list[tuple[list[str], Any, Any]] = []
        self.long_calls: list[tuple[str, Any, Any, int, int]] = []
        StubJointIEEngine.instances.append(self)

    def create_schema(self) -> StubJointSchema:
        return StubJointSchema()

    def _next(self, text: str) -> StubJointResult:
        if self.results:
            return self.results.pop(0)
        return StubJointResult(text=text)

    def extract(self, text: str, schema: Any, *, config: Any = None) -> StubJointResult:
        self.extract_calls.append((text, schema, config))
        return self._next(text)

    def batch_extract(
        self, texts: list[str], schemas: Any, *, config: Any = None
    ) -> list[StubJointResult]:
        self.batch_calls.append((list(texts), schemas, config))
        return [self._next(text) for text in texts]

    def extract_long(
        self,
        text: str,
        schema: Any,
        *,
        config: Any = None,
        chunk_size: int = 384,
        chunk_overlap: int = 64,
    ) -> StubJointResult:
        self.long_calls.append((text, schema, config, chunk_size, chunk_overlap))
        queued = self._next(text)
        # The real ``extract_long_text`` rebuilds a fresh ``JointResult`` from
        # its merged chunk fragments and never carries a chunk's ``feasible``
        # flag across, so a windowed extraction always reports feasible=True.
        # Mirroring that here stops a queued feasible=False result from making
        # an infeasibility assertion pass on the windowed path, where the real
        # library could never produce it.
        return StubJointResult(
            text=queued.text,
            entities=queued.entities,
            relations=queued.relations,
        )


@pytest.fixture
def gliner2_stub(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a stub ``gliner2`` package for the duration of one test.

    Returns:
        The stub ``gliner2`` module. Its ``joint_ie`` submodule is reachable
        as ``stub.joint_ie``, and the engine instances built during the test
        are collected on ``StubJointIEEngine.instances``.
    """
    StubAutoExtractor.loads = []
    StubJointIEEngine.instances = []

    gliner2 = types.ModuleType("gliner2")
    gliner2.AutoExtractor = StubAutoExtractor  # type: ignore[attr-defined]
    gliner2.AttributeGroup = StubAttributeGroup  # type: ignore[attr-defined]
    gliner2.Schema = StubSchema  # type: ignore[attr-defined]

    joint_ie = types.ModuleType("gliner2.joint_ie")
    joint_ie.JointSchema = StubJointSchema  # type: ignore[attr-defined]
    joint_ie.JointIEConfig = StubJointIEConfig  # type: ignore[attr-defined]
    joint_ie.JointIEEngine = StubJointIEEngine  # type: ignore[attr-defined]
    joint_ie.JointIE = StubJointIEEngine  # type: ignore[attr-defined]
    joint_ie.JointEntity = StubJointEntity  # type: ignore[attr-defined]
    joint_ie.JointRelation = StubJointRelation  # type: ignore[attr-defined]
    joint_ie.JointResult = StubJointResult  # type: ignore[attr-defined]

    gliner2.joint_ie = joint_ie  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "gliner2", gliner2)
    monkeypatch.setitem(sys.modules, "gliner2.joint_ie", joint_ie)
    return gliner2
