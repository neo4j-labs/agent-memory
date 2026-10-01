# Python tutorials and core how-to implementation evidence — 2026-09-13

## Ownership and delivered files

This batch edits authored documentation and adds runnable documentation examples/tests only. No runtime SDK source, generated pages, shared navigation/indexes, root README, or skills changed by this agent.

Tutorials:
- `docs/modules/ROOT/pages/tutorials/first-agent-memory.adoc`
- `docs/modules/ROOT/pages/tutorials/conversation-memory.adoc`
- `docs/modules/ROOT/pages/tutorials/knowledge-graph.adoc`

Core how-tos:
- `docs/modules/ROOT/pages/how-to/messages.adoc`
- `docs/modules/ROOT/pages/how-to/entities.adoc`
- `docs/modules/ROOT/pages/how-to/preferences.adoc`
- `docs/modules/ROOT/pages/how-to/reasoning-traces.adoc`
- `docs/modules/ROOT/pages/how-to/deduplication.adoc`
- `docs/modules/ROOT/pages/how-to/entity-extraction.adoc`
- `docs/modules/ROOT/pages/how-to/entity-extraction-schemas.adoc`
- `docs/modules/ROOT/pages/how-to/entity-extraction-batch.adoc`

New maintained example programs/helpers:
- `docs/modules/ROOT/examples/core_memory_settings.py`
- `docs/modules/ROOT/examples/first_agent_memory.py`
- `docs/modules/ROOT/examples/conversation_memory.py`
- `docs/modules/ROOT/examples/knowledge_graph.py`
- `docs/modules/ROOT/examples/core_memory_recipes.py`
- `docs/modules/ROOT/examples/extraction_recipes.py`

New focused test file: `tests/docs/test_core_python_tutorials.py` (80 contracts, including parameterized method binding; together with 186 reference contracts, 266 pass).

The batch-processing operational page and other seven Python tutorials are owned by other agents, not this batch. Integration how-tos are also owned separately.

## Finding/task dispositions

| Finding / work | Documentation and executable-example result | Verification boundary |
| --- | --- | --- |
| PY06 / W04 first tutorial | Retitled around actual memory-context outcome. Complete seed, verify, and search commands; typed entities and explicit preference; ordered persisted-message readback in second process; real RELATED_TO/relation_type query; score metadata; no empty-query listing or implied automatically learned facts. | Manual storage lesson intentionally has no conversational model call; chatbot is the next lesson. Live Neo4j/OpenAI verification remains environment-dependent. |
| PY07 / W04 knowledge tutorial | Complete custom DomainSchema + GLiNER + GLiREL constructor; proper tuple type/subtype mapping; explicit tokenizer/GLiREL dependencies; real relation_type/source/target kwargs; safe endpoint resolution; exact persisted-ID lookup before links; EXTRACTED_FROM provenance; graph restart inspection; actual model call consumes stored triples and source text. | Extraction quality/counts/model downloads not proven offline. Unresolved or ambiguous endpoints fail/skip visibly; zero usable relations stops ingestion. |
| W04 conversation tutorial | Complete seed/resume program with two explicitly selected session IDs, user-scoped preference retrieval, fictional catalog, actual model request containing saved history/preferences/catalog, stored assistant reply and product-lookup trace readback; completion on model failure. | Single-user learning database. App authorization is explicitly distinct from identifiers. Preference is explicitly seeded; no automatic learning claim. No live answer-quality claim. |
| PY08 / W09 message task | Runnable batch write, ordered IDs readback, current callable summary API, candidate-only semantic search. Tables direct readers to supported session/batch/context/deletion operations instead of invented wrappers. | Documents ignored Bolt search session filter, global context scope and 1,000-message default cap. Does not implement isolation. |
| PY08 / W09 entity task | Runnable add_entity tuple return, add_relationship exact endpoint IDs, add_fact obj/Fact.as_triple property, relationship/fact readback. Unsupported CRUD wrappers removed in favor of explicit application query guidance. | Documents nonpersisted relationship attributes, ID mismatch on repeated MERGE, and schema/attribute serialization. |
| PY08 / W09 preferences | Runnable independently scoped preference create/revise/supersede/history task with EntityRef/APPLIES_TO. Active and historical IDs checked. | Disables embedding generation for this revision example to avoid shared-node global duplicate reuse; explains tradeoff and shared mutation impact. No automatic preference-learning promise. |
| PY08 / W09 reasoning | Runnable deliberately failed tool action, real ToolCallStatus.ERROR and TraceOutcome, failure trace/tool readback; exact query/hook pointers. | No invented get_steps/get_tool_calls/search_traces/delete_trace or success/error kwargs. Trace is explicit action/outcome evidence, not private model reasoning. |
| PY08 / W09 deduplication | Runnable explicit LongTermMemory/DeduplicationConfig construction; reviewed unique pair merge and source-marker/alias readback; correct tuple/stat result contracts. | Embedding thresholds are measured choices, not universal safety; current merge transfers only MENTIONS and eligible SAME_AS links. Other edges require app reconciliation. |
| PY13 / W09 extraction/schema/batch tasks | Three focused tasks and complete no-database local extractor program. Correct registered schemas, custom DomainSchema/type mapping, separate EntitySchemaConfig/persisted schema/hosted ontology, actual pipeline aggregate vs GLiNER list return, streaming result wrappers and error accounting. | Character units and whitespace token approximation explicit; input string is loaded whole, not file streaming. No universal document-length cutoff; no accuracy claim from completion count. |
| Editorial tutorial/how-to criteria | One runnable repository path per tutorial; prerequisites, selected dependencies, named files, supported includes, run commands and expected milestones, cleanup/error guidance. How-tos have goal, prerequisites, numbered task/check and supported adjacent operations/reference links. Historical section URLs retained. | Root owns final strict build/anchor/visual verification and cross-site routing. |
| Image alt/caption issues in assigned pages | Comma-containing alt strings quoted, SVG width=100% and link=self. First lesson uses truthful message-chain diagram; preference graph explicitly conceptual; KG physical schema shown directly with Cypher. | Root owns native scene labels and rendering. |

The shorter how-to pages replace duplicated incomplete applications and fabricated APIs with runnable maintained programs plus exhaustive reference links. Historical section anchors remain, so earlier deep links resolve. Useful supported alternatives such as time filters, custom summaries, manual entity writes, facts, provenance, schema persistence, local relation extraction, review queues, trace queries, and streaming are retained as accurate task/reference routes.

## Additional source-backed limits corrected during this batch

These are source observations, not live service failures or automatic authorization to change runtime behavior:

1. `LongTermMemory.add_entity` allocates an ID, ignores the write result, and returns the allocated model; `build_create_entity_query` MERGEs by exact name/type and sets ID only ON CREATE. Repeated writes may return an ID absent from the graph. KG fixture uses exact persisted-ID readback before provenance/relationship writes; tests cover differing allocated/persisted IDs and repeated mention identity. The long-term reference and entity task document the limit.
2. Bolt `get_conversation(limit=None)` uses `limit or 1000`, then filters `since` in Python. Corrected the earlier copied “all messages” reference statement.
3. `MERGE_ENTITIES` transfers incoming MENTIONS and eligible SAME_AS only, adds aliases, and marks source; it does not generically migrate graph/provenance relationships. Reference and deduplication task now say this explicitly.
4. Preference duplicate lookup is global by category/embedding and can link multiple users to a shared Preference. Supersession changes that shared node's active view. The independent revision recipe uses generate_embedding=False and explains this scope/semantic-search tradeoff.

## Validation and retained logs

- `pytest tests/docs/test_core_python_tutorials.py tests/docs/test_python_reference_contracts.py -q`: **266 passed**, one existing environmental Google SDK FutureWarning; `logs/agent-memory-core-all-tests-final.log`.
- Tests bind every authored client/store method call in five programs to actual source signatures; construct actual config/schema/result models with injected provider; run the real pipeline and chunker against a local extractor; verify actual model-call inputs and trace completion paths with autospecced public stores; verify provenance uses persisted IDs; verify all tagged includes exist.
- Core tutorial initial run: 44 passed; `logs/agent-memory-core-tutorial-tests.log`. Intermediate reference+recipe run: 263 passed; `logs/agent-memory-core-all-tests.log`.
- A new repeated-mention test initially compared unrelated created_at timestamps on two fake entities. Corrected its assertion to the persisted source/target IDs and relationship payload that matter. Complete failure output retained: `logs/agent-memory-core-repeated-mention-failure.log`.
- Narrow Ruff check and formatting: passed; `logs/agent-memory-core-lint-final.log`. Initial unused-lambda warning retained and fixed; `logs/agent-memory-core-lint-initial.log`.
- `scripts.check_docs.source_report`: zero source link/image-alt errors for all 11 owned tutorial/task pages; all six Python files compile; `logs/agent-memory-core-source-check.log`.
- `git diff --check` on assigned reference/tutorial/how-to pages: passed after removing generated-table trailing spaces; `logs/agent-memory-python-docs-diff-check.log`.
- Five executable `--help` assembly smoke checks: `logs/agent-memory-core-help-check.log`.
- No live Neo4j, NAMS, LLM requests, model downloads, or external link/network tests were performed by this agent. No NAMS service version is inferred from SDK versions.

## Root integration follow-ups

- Add `tests/docs/test_core_python_tutorials.py` to offline docs CI. It does not require OpenAI/GLiNER installs because the provider/model dependencies are injected or loaded inside the actual executable entry point; model constructors stay lazy.
- Include these pages and examples in final strict Antora/HTML anchor verification. The first tutorial's title now truthfully describes a memory-context outcome.
- Carry the source-backed implementation limits above into remediation status without claiming source runtime fixes were made.

## Final rendered anchor comparison

- Compared HEAD and current source through the installed Asciidoctor renderer for all 25 owned pages (14 references, three tutorials, eight how-tos), excluding only generic preamble and generated table IDs. **Zero meaningful old IDs lost; zero duplicate rendered IDs; zero pages with literal paragraph-rendered headings.** Evidence: `logs/agent-memory-python-rendered-anchor-check.json`; runner `/tmp/check-python-doc-anchors.cjs`. This check strips includes when comparing document anchors; the root strict Antora build checks full include/render behavior.
- Converted stacked block IDs to inline anchor macros, retained current heading auto-IDs, and added 132 former reference IDs in labeled compatibility blocks with canonical section/task links. Mapping: `logs/agent-memory-python-reference-anchor-restoration.json`.
- Fixed 90 anchor-to-heading/block boundaries by inserting a blank line; added a focused regression guard to the core tests. Final combined suite: 266 passed.
- All five executable `--help` checks exited 0; the full output and environmental warnings remain in `logs/agent-memory-core-help-check.log`.
