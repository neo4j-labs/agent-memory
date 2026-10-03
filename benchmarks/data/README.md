# Benchmark gold data

Hand-written, synthetic gold data for the POLE+O model. Nothing here is
scraped, licensed, or derived from a real corpus, and no text is reused from
`examples/domain-schemas/samples/`.

## Files

### `poleo_gold.json`

A `BenchmarkSuite` (load it with `BenchmarkSuite.from_json_file`) holding 10
documents of 2–3 sentences each: 40 gold entities and 29 gold triples.

```python
from benchmarks import BenchmarkRunner, BenchmarkSuite

suite = BenchmarkSuite.from_json_file("benchmarks/data/poleo_gold.json")
result = await BenchmarkRunner(extractor).run_suite(suite)
print(result.metrics.micro_f1, result.relation_f1, result.strict_relation_f1)
```

Each test case carries:

- `expected_entities` — `{name, type, aliases}`. `type` is a POLE+O type
  (`PERSON`, `ORGANIZATION`, `LOCATION`, `EVENT`, `OBJECT`). The canonical
  `name` is always a literal substring of `text`; `aliases` hold the other
  surface forms a reasonable extractor may return, whether or not they appear
  verbatim.
- `expected_relations` — `{source, relation_type, target, source_aliases,
  target_aliases}`. Endpoint aliases mirror the entity aliases so that
  alias-tolerant scoring works at the relation level too.
- `metadata.notes` — why the case exists, and `metadata.negative_relations`
  where a triple must *not* be extracted.

Relation names come from the POLE+O vocabulary in
`src/neo4j_agent_memory/schema/models.py` (`_get_poleo_relation_types`). All
14 constrained types are exercised: `EMPLOYED_BY`, `MEMBER_OF`, `LOCATED_AT`,
`RESIDES_AT`, `HEADQUARTERS_AT`, `PARTICIPATED_IN`, `OCCURRED_AT`, `OWNS`,
`USES`, `KNOWS`, `SUBSIDIARY_OF`, `PARTNER_WITH`, `ALIAS_OF`, `INVOLVED`. The
catch-all `RELATED_TO` and `MENTIONS` are deliberately absent: a gold set that
accepts them measures nothing.

How it was written — every case is built around at least one known failure
mode:

- **Alias variation.** `Acme Corp` / `Acme` / `ACME Corporation` (poleo-01),
  `Northwind Logistics` / `Northwind` (poleo-02, poleo-10), `Dr. Amara Chen` /
  `A.C.` (poleo-06).
- **A planted negation.** poleo-02 says *"Marcus did not join Northwind"*. An
  extractor that ignores the polarity emits a false `EMPLOYED_BY` edge, which
  scores as a false positive; the forbidden triple is spelled out in
  `metadata.negative_relations`.
- **Cross-sentence relations.** Most triples in poleo-03, poleo-04, poleo-08,
  poleo-09 and poleo-10 have their two endpoints in different sentences.
- **Type traps.** `Lisbon` (LOCATION) versus `Lisbon Rail Summit` (EVENT);
  `Porto` as an alias of the `Porto workshop` location.
- **Nominal and metonymic reference.** *"The trust"*, *"The union"*,
  *"the arms"*, and *"reports to Lisbon"* — the last is deliberately **not** a
  `RESIDES_AT` edge.

### `poleo_resolution_gold.json`

Gold entity-resolution clusters over the same documents: 50 mentions in 29
clusters. Load it with `load_resolution_gold` and score a predicted clustering
with `bcubed`.

```python
from benchmarks import bcubed, load_resolution_gold

gold = load_resolution_gold("benchmarks/data/poleo_resolution_gold.json")
precision, recall, f1 = bcubed(predicted_cluster_ids, gold.cluster_map())
```

Shape:

```json
{
  "mentions": [{"id": "poleo-01:m1", "text": "Acme Corp",
                "type": "ORGANIZATION", "doc": "poleo-01"}],
  "clusters": {"poleo-01:m1": "acme"}
}
```

Invariants, enforced by `tests/unit/test_benchmarks.py`:

- Every mention id is `"<case id>:m<n>"` and its `doc` is a case in
  `poleo_gold.json`.
- Every mention `text` is a literal substring of that case's `text`.
- Every mention appears in `clusters`, and every cluster key is a known
  mention.

Cross-document clusters are the point: `acme` spans poleo-01, poleo-03 and
poleo-10; `kestrel-robotics` spans poleo-03, poleo-04 and poleo-08;
`marcus-bell`, `rotterdam`, `northwind-logistics`, `priya-raghavan` and
`lisbon` each span two documents.

One case is deliberately inconsistent between the two files: in poleo-07,
`J. Marchetti` and `Giulia Marchetti` are two gold *entities* joined by an
`ALIAS_OF` triple, but one gold *cluster*. Extraction records surface forms;
resolution records referents. A pipeline that collapses them before extraction
loses the `ALIAS_OF` edge, and one that never merges them scores below 1.0 on
B-cubed recall.

## Extending the data

1. Write the text first — 2 to 5 sentences, synthetic, no real people or
   companies. Give the case an id of the form `<suite>-NN`.
2. List `expected_entities`. Keep the canonical `name` a literal substring of
   `text`; put every other plausible surface form in `aliases`.
3. List `expected_relations` from the closed vocabulary above, and copy the
   endpoint aliases from the entities. Only annotate what the text actually
   asserts; a triple that needs world knowledge is not gold.
4. Add mentions and clusters to `poleo_resolution_gold.json` for any new proper
   names, keeping the id convention.
5. Say in `metadata.notes` what the case is there to catch, and run
   `uv run pytest tests/unit/test_benchmarks.py -q`. The gold-data tests check
   the invariants above, that relation endpoints name an expected entity or one
   of its aliases, and that endpoint types satisfy the POLE+O source/target
   constraints.

A new suite for a different domain is a new `*_gold.json` in this directory
with the same structure; nothing in the loader is specific to POLE+O.
