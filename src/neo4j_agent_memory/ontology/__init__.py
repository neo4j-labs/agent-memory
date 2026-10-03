"""Backend-neutral ontology support.

An ontology is the typed schema the whole pipeline agrees on: it names the
entity labels (each mapped onto a POLE+O type), the typed relationships
between them with explicit source/target labels, and the decoding and
resolution constraints that go with them. One document drives GLiNER2.5's
JointIE schema, the LLM extractor's prompt, relation validation and
entity resolution — on the hosted backend and on bolt alike.

Start from a built-in::

    from neo4j_agent_memory.ontology import POLEO_ONTOLOGY, get_template

    doc = get_template("podcast")
    problems = doc.validate_structure()   # [] when sound

or convert what you already have::

    from neo4j_agent_memory.ontology import from_entity_schema, load_ontology

    doc = load_ontology("ontology.yaml")
    doc = from_entity_schema(my_entity_schema_config)
"""

from neo4j_agent_memory.ontology.builtin import (
    POLEO_ONTOLOGY,
    get_template,
    list_templates,
)
from neo4j_agent_memory.ontology.compile import (
    compile_attribute_schema,
    compile_joint_schema,
    label_map,
    prompt_fragment,
    spacy_label_map,
)
from neo4j_agent_memory.ontology.convert import (
    from_arrows,
    from_domain_schema,
    from_entity_schema,
    is_entity_schema_shape,
    load_ontology,
    to_entity_schema,
)
from neo4j_agent_memory.ontology.diff import diff_documents
from neo4j_agent_memory.ontology.models import (
    POLEO_TYPES,
    ActiveOntology,
    DomainInfo,
    EntityTypeDef,
    ImportWarning,
    MigrationJob,
    Ontology,
    OntologyDiff,
    OntologyDocument,
    OntologyImportResult,
    OntologyRecord,
    OntologySummary,
    OntologyVersion,
    PropertyDef,
    RelationshipDef,
)
from neo4j_agent_memory.ontology.protocol import OntologyAPI
from neo4j_agent_memory.ontology.store import BoltOntology

__all__ = [
    # Models
    "POLEO_TYPES",
    "ActiveOntology",
    "DomainInfo",
    "EntityTypeDef",
    "ImportWarning",
    "MigrationJob",
    "Ontology",
    "OntologyDiff",
    "OntologyDocument",
    "OntologyImportResult",
    "OntologyRecord",
    "OntologySummary",
    "OntologyVersion",
    "PropertyDef",
    "RelationshipDef",
    # Built-ins
    "POLEO_ONTOLOGY",
    "get_template",
    "list_templates",
    # Compile
    "compile_attribute_schema",
    "compile_joint_schema",
    "label_map",
    "prompt_fragment",
    "spacy_label_map",
    # Convert
    "from_arrows",
    "from_domain_schema",
    "from_entity_schema",
    "is_entity_schema_shape",
    "load_ontology",
    "to_entity_schema",
    # Diff
    "diff_documents",
    # Protocol
    "OntologyAPI",
    # Store (bolt)
    "BoltOntology",
]
