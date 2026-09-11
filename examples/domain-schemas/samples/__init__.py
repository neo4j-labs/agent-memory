"""Sample corpora for the eight built-in GLiNER domain schemas.

One module per schema, each exporting a :class:`~samples.base.SampleSet`.
``run.py`` looks them up by schema name:

    python run.py --schema medical

Every corpus here is synthetic — see each module's docstring.
"""

from __future__ import annotations

from samples import (
    business,
    entertainment,
    legal,
    medical,
    news,
    podcast,
    poleo,
    scientific,
)
from samples.base import ALL_DEMOS, BATCH, RELATIONS, STREAMING, Document, Highlight, SampleSet

# Ordered registry: the order the README table and ``--list`` use.
SAMPLES: dict[str, SampleSet] = {
    sample.schema_name: sample
    for sample in (
        poleo.SAMPLE,
        podcast.SAMPLE,
        news.SAMPLE,
        scientific.SAMPLE,
        business.SAMPLE,
        entertainment.SAMPLE,
        medical.SAMPLE,
        legal.SAMPLE,
    )
}

__all__ = [
    "ALL_DEMOS",
    "BATCH",
    "RELATIONS",
    "SAMPLES",
    "STREAMING",
    "Document",
    "Highlight",
    "SampleSet",
    "get_sample",
    "list_sample_names",
]


def list_sample_names() -> list[str]:
    """Schema names with a sample corpus, in presentation order."""
    return list(SAMPLES)


def get_sample(schema: str) -> SampleSet:
    """Return the sample corpus for a schema name.

    Raises:
        KeyError: If no corpus exists for ``schema``.
    """
    try:
        return SAMPLES[schema]
    except KeyError:
        available = ", ".join(SAMPLES)
        raise KeyError(f"No sample corpus for schema '{schema}'. Available: {available}") from None
