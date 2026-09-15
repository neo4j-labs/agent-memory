# Python reference implementation evidence — 2026-09-13

## Scope and changes

Edited only the 14 assigned authored reference pages plus a new isolated test file. No runtime, generated documentation, shared navigation, or shared test utilities changed by this agent.

- `reference/api/memory-client.adoc`: actual constructor/method signatures, connection lifecycle, accessor availability, string context result, graph/statistics shape, backend-specific context scope; retained `graph` anchor and obsolete-operation compatibility anchors.
- `reference/api/short-term.adoc`: actual message/batch/conversation/session/summary operations and model fields; precise bolt vs NAMS honored options, score location, async extraction status, global search/context scope caveat.
- `reference/api/long-term.adoc`: complete entity/preference/fact/relationship/deduplication/provenance/geospatial operation declarations and input/model tables. Removed invented CRUD methods; exact NAMS return shapes and omitted kwargs; extraction wait conditions and hosted graph operations.
- `reference/api/reasoning.adoc`: actual trace/step/tool/statistics/search/streaming operations, structured outcomes, model properties/statuses, and NAMS local synthetic-trace lifecycle/durability limits.
- `reference/api/adapters.adoc`: all 10 adapter constructor signatures source matched; missing credential/retry/batch parameters; SentenceTransformers immediate dimensions contract and actual defaults; explicit Vertex model default limitation.
- `reference/api/factory.adoc`, `reference/api/llm-provider.adoc`: precise scope of protocol tiers, model-routing/availability wording and canonical identifiers; retained accurate provider/protocol details.
- `reference/configuration.adoc`: all 11 child configuration group fields, defaults, enums and numeric constraints; MemorySettings field inventory; exact constructor/env/dotenv/file-secret precedence; distinction between accepted values and actual runtime consumption; deprecated-but-still-working provider shapes; no invented NAM groups or YAML CLI loader. Explicit stable anchors for graph schema/extraction/resolution/deduplication/observability and all settings sections.
- `reference/environment-variables.adoc`: complete nested settings field inventory, top-level provider/backend settings, precise process-only NAMS aliases, external/CLI input boundaries, unsupported settings groups, integration wiring caveats.
- `reference/cli.adoc`: options from actual Click command metadata; exact short flags, choices, schema path input, JSON/JSONL output; real stats semantics; complete MCP backend flags and provider requirements; preserved useful examples and added executable schema fixture inline.
- `reference/extractors.adoc`: actual protocol, constructor/method signatures, result models, batch-vs-stream differences, builder/schema behavior, merge strategies and early stopping; no invented methods or custom schema overload.
- `reference/schemas.adoc`: all built-in domain labels/descriptions compared with DOMAIN_SCHEMAS; correct DomainSchema import and separate extraction/persisted/hosted schema layers; public graph accessor for persistence; full schema types and persistence operations; default mapping/fallback semantics.
- `reference/schema-objects.adoc`: retained complete 33-object inventory; bolt-only scope; actual suppression of vector/point index errors and dimensions validation; explicit prefix-based drop scope.
- `reference/rest-api.adoc`: Python public accessor mappings versus protocol names; no-wrapper entries; complete trace compatibility limitation; endpoint/client-specific pagination; actual Python retries/Retry-After parsing; NAMS protocol vs unversioned service. TS agent confirmed other TS table rows and supplied correction: no TS getExtractionStatus wrapper, TS extraction wait polls entity search. Escaped literal URI placeholders.

## Finding dispositions for this batch

| Finding | Documentation result | Remaining boundary |
| --- | --- | --- |
| PY02 | MemoryClient reference corrected and expanded | No runtime isolation/parity change; source-backed scope limits explicit |
| PY03 | Short-term reference corrected and expanded | Normal linked-message deletion can fail due to remaining graph relationships; documented, source unchanged |
| PY04 | Long-term reference corrected and expanded | Relationship attributes are returned but not sent to bolt write; documented, source unchanged |
| PY05 | Reasoning reference corrected and expanded | NAMS synthetic task/trace/outcome state is not server-durable; documented, source unchanged |
| PY10 | Invalid configuration APIs/groups/imports removed; real settings and helper APIs covered | Runtime accepted-but-unconsumed fields remain; documented individually |
| PY11 | Complete nested environment field inventory and real aliases | Tests prove source declarations, not all external provider SDK environment behavior |
| PY12 | CLI option/output/schema/stats references corrected | Inference quality, model availability and hosted operation success are not offline-tested |
| PY13 | Extractor/schema constructors/results/builders/batches/stream references corrected | Full custom schema relation/property enforcement is not added by docs; explicit boundary |
| PY14 | All adapter constructors/defaults corrected | Legacy Vertex default is still rejected by its underlying embedder; explicit valid-model example/warning |
| PY15 | Python REST column now names implemented accessors or no wrapper | Hosted OpenAPI completeness/live behavior and external Go/C# source not verified in this batch |

## Code follow-ups identified while revalidating

These are observable source limits, not live-service test results or an assertion that all require new implementation:

- Bolt short-term semantic search accepts session_id without applying it. Short-term get_context appends global semantic results even with a supplied session, and MemoryClient context does not scope long-term/reasoning search.
- Bolt `DELETE_MESSAGE` only deletes MENTIONS and the Message, leaving HAS_MESSAGE/sequential/other relationships that can prevent deletion (`graph/queries.py:174–185`). No neighbor relinking exists.
- Long-term add_relationship omits attributes from execute_write arguments despite putting them on the returned Relationship.
- MemoryClient legacy EmbeddingConfig factory constructs only OpenAI and SentenceTransformer; enum acceptance for Vertex/Bedrock is not wiring.
- Unconsumed config fields identified in per-field reference tables: SchemaConfig model/enable_subtypes/strict_types/custom_schema_path; ResolutionConfig fuzzy_scorer; MemoryConfig defaults and TTL/audit fields except multi_tenant/write_mode/max_pending; all SearchConfig fields; ExtractionConfig confidence_threshold; LLMConfig temperature/max_tokens in extraction construction.
- Google geocoder requires an explicit/configured key; no automatic GOOGLE_GEOCODING_API_KEY fallback.
- OpenTelemetry tracer needs explicit endpoint= to create an exporter. `async_span` is the async context manager, while `span` is synchronous.
- NAMS retries only 429/500/502/503/504 and selected network errors; Retry-After supports numeric seconds, not HTTP dates.
- Bolt geocode_locations accepts skip_existing but always queries locations without coordinates.

## Validation and evidence

- New `tests/docs/test_python_reference_contracts.py`: source AST signature comparisons, complete settings field/default/constraint inventory checks, complete built-in schema registry comparison, bolt schema-object inventory equality, actual Click option/choice checks, and mocked extraction CLI/YAML/JSON scenario with no model/network calls. Final result is in `logs/agent-memory-python-reference-tests-final.log` (updated after schema API additions).
- Initial test run: 174 passed; `logs/agent-memory-python-reference-tests.log` retained.
- Static check: 14 pages, 194 Python blocks compiled with top-level-await support, no missing local xref target files, including declarations (before final schema additions); `logs/agent-memory-python-reference-static.log`.
- Ruff narrow check and format passed. Initial lint failure (import grouping and unused lambda receiver) retained in `logs/agent-memory-python-reference-lint.log`; both corrected.
- `logs/agent-memory-cli-contract.json` preserves actual Click metadata used for the reference.
- Existing Google Cloud SDK FutureWarning is environmental; no live Neo4j, NAMS, LLM, model downloads, external link checker, or credentialed requests run by this agent.

## Next assigned work

Core Python tutorials first-agent-memory/conversation-memory/knowledge-graph, then core how-tos, with checked-in runnable fixtures and offline contracts; details will be recorded separately in `python-tutorials-and-howtos.md`.

## Final follow-through after tutorial work

The tutorial revalidation additionally corrected reference prose for Bolt's 1,000-message default read cap and post-fetch since filter, add_entity's allocated-versus-persisted ID on an exact name/type MERGE hit, and the limited MENTIONS/SAME_AS transfer behavior of manual merges. No runtime fixes were made.

All assigned reference table trailing whitespace was stripped; git diff --check passes. Stacked legacy anchors were changed to inline anchors, and former implicit section IDs were restored with canonical pointers. Asciidoctor comparison of all 25 owned pages (including these 14 reference pages) reports zero lost meaningful anchors, zero duplicate IDs, and zero literal-heading pages: `logs/agent-memory-python-rendered-anchor-check.json`. Mapping: `logs/agent-memory-python-reference-anchor-restoration.json`.

Final combined reference and tutorial/how-to contracts: **266 passed** (186 reference + 80 core), one environmental Google SDK FutureWarning; `logs/agent-memory-core-all-tests-final.log`. All narrow Ruff checks pass. Full W04/W09 evidence: `python-tutorials-and-howtos.md`.

## Final literal-placeholder warning correction

At root's final-build request, escaped 12 intentional single-name placeholders in authored reference/ontology-api.adoc and 14 in reference/skills-api.adoc outside source blocks. No endpoint names, displayed placeholders, code samples, or API semantics changed. No global missing-attribute suppression was added.

Narrow Asciidoctor conversion with attribute-missing=warn: ontology-api and skills-api emitted 12/14 warnings before the escapes and zero afterward. The complete rendered HTML strings are identical before/after; every displayed placeholder remains present. The latest reference/configuration.adoc also emits zero warnings, confirming the prior sequence warning was resolved by anchor/heading blank-line separation. Evidence including complete prior warning messages: logs/agent-memory-reference-warning-check.json; logs/agent-memory-placeholder-escape-changes.json. Two-file git diff --check passes.
