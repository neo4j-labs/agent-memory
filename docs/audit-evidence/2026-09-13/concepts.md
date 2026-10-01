# Editorial implementation evidence — 2026-09-13

## Scope and ownership

Updated 13 owned pages: `faq.adoc`, `glossary.adoc`, and the 11 `explanation/*.adoc` pages other than `index.adoc` and `backends.adoc`. No repository source code, tests, navigation, shared UI, image binaries, generated files, or skill files were changed by this agent. Root owns the two excluded explanation pages and the new `reference/backend-capabilities.adoc` destination.

Read and applied all four `.claude/skills/*/SKILL.md` sources and `AGENTS.md`. Removed duplicated task instructions and incomplete API catalogs from explanation pages in favor of conceptual rationale, tradeoffs, source-valid examples, and links to existing task/reference destinations. The retained graph Cypher example is explicitly a conceptual read-only fragment requiring stored data and a named parameter; it is not presented as a standalone program.

## Per-finding evidence

| ID | Disposition in owned scope | Evidence and implementation |
|---|---|---|
| ED-07 | Implemented | `explanation/poleo-model.adoc:23` replaces the duplicate subtype catalog with the classification rationale and links to the full schema reference; `:49`, `:58`, `:69`, `:78` route configuration, custom models, operations and mappings to their existing owners. `explanation/resolution-deduplication.adoc:54`, `:65`, `:74` replace invalid review/merge/config snippets with identity and policy explanations plus references. `explanation/framework-comparison.adoc:12`, `:51`, `:58` replace method/installation catalogs with integration shapes and maintained guide links. `explanation/memory-types.adoc:66` routes procedural usage to message/entity/trace how-tos. Provider, ontology and skill conceptual pages now explain design rather than carrying duplicate complete API tables. |
| ED-10 | Implemented | FAQ now uses concise answers and canonical routes. `faq.adoc:90` names the actual pipeline enum values; `:151` and `:156` remove LLM-only relation/OpenAI-only claims; `:58` routes server-version prerequisites without confusing them with driver versions; `:205` consistently uses source defaults 0.95/0.85; `:244` removes nonexistent automatic YAML CLI loading; `:278` and `:283` distinguish explicit instrumentation from automatic tracing. Removed the invalid coroutine `lru_cache` example and explained completed-value caching at `:305`. |
| ED-11 | Implemented | `explanation/framework-comparison.adoc:12` covers the actual repository integration shapes including Google ADK, without frozen framework/test counts. `:27` describes current LangChain 1.x history/middleware and Microsoft `agent-framework-core>=1.13,<2` from source and maintained guide. Removed retired `BaseMemory`/`load_memory_variables`/legacy retriever methods, preview pin, arbitrary rankings, and the malformed Markdown summary table. Framework setup/version details now reside in the maintained guides. |
| ED-12 | Implemented | `explanation/memory-types.adoc:24` defines persisted conversational, declarative and recorded-execution roles, distinguishes working context and approximate episodic analogies, and avoids hidden-reasoning promises. `glossary.adoc:33` uses the same approximate mappings without asserting a universal competitor API equivalence. Persisted duration is explicitly a policy, not implied by “short-term.” |
| ED-13 | Implemented | `explanation/graph-architecture.adoc:20`, `:29`, `:80` remove universal relational/graph complexity claims, unsourced timing table and storage estimates; explain representative workload measurement instead. `explanation/extraction-pipeline.adoc:18`, `:82` and FAQ remove unsourced extractor timing/size/accuracy rankings. `explanation/structured-extraction.adoc:21`, `:48`, `:83` and `why-provider-protocol.adoc:30`, `:37` remove guaranteed first-call/native/Instructor performance and empirical retry claims, replacing them with source-visible mechanisms and failure boundaries. `resolution-deduplication.adoc:92` removes unsupported universal “healthy” quality percentages. |
| ED-14 | Implemented | `explanation/resolution-deduplication.adoc:18` explicitly keeps iPhone Pro versus Pro Max distinct and distinguishes Air Max family versus Air Max 90 model. Other alias examples require actual identifiers and context. `:58` explains that absent SKUs are not matching identifiers; `:83` distinguishes historical identities, parents and subsidiaries. `poleo-model.adoc:69` and `:87` reinforce identity versus classification. |
| ED-18 | Conceptual/FAQ occurrences implemented | `faq.adoc:178` and `explanation/extraction-pipeline.adoc:90` remove arbitrary 100K-token thresholds and document character-based chunk size/overlap by default with token mode explicit. Operational how-to occurrences remain owned by the later how-to stream. |
| G-08 | Implemented in owned scope | `faq.adoc:23`, `framework-comparison.adoc:9`, and `skills.adoc:10` use experimental/community-supported Labs positioning, separate upstream GA from integration support, and remove unsupported “stable enough” interface promises. No blanket production-ready or officially supported claim remains in owned prose. |
| G-04 | Markup implemented in owned scope | All six retained image macros quote complete descriptions and retain width/align attributes. Local rendered `<img>` tags show complete comma-containing `alt` values and numeric widths. No binary image revision or visual diagram-content proof is claimed. |
| PY-01 | Documentation containment in owned scope | `multi-agent-sharing.adoc:8`, `:16`, `:25`, `:68` remove private-session/universal-user-filter promises, identify workspace/database authorization, and explicitly document Bolt entity search's ignored `session_id` filter. `glossary.adoc:19`, `:21`, `:22`, `:23` and FAQ backend answers agree. Runtime filter defect remains a separate root/source decision; no silent feature implementation or post-filter workaround was added. |

## Source checks used

- `src/neo4j_agent_memory/extraction/pipeline.py`: `MergeStrategy`, normalized-name/type key, candidate merge implementations, first-success selection, batch method.
- `src/neo4j_agent_memory/extraction/streaming.py`: `StreamingExtractor` constructor and chunk unit selection.
- `src/neo4j_agent_memory/extraction/gliner_extractor.py`: `DomainSchema`, schema wiring, GLiREL availability, label descriptions.
- `src/neo4j_agent_memory/memory/long_term.py`: deduplication defaults, user-specific methods, entity-search signature/behavior, explicit aliases.
- `src/neo4j_agent_memory/graph/queries.py`: actual `:HAS_MESSAGE`, `:MENTIONS`, `:RELATED_TO`, `:INITIATED_BY`, `:TRIGGERED_BY` patterns.
- `src/neo4j_agent_memory/config/settings.py`, `pyproject.toml`: settings loading, configured types, dependency ranges (driver versus server distinguished).
- `src/neo4j_agent_memory/llm/{protocol,factory,structured}.py` and native adapters: structured dispatch/validation/fallback, errors, dimensions, protocol boundaries.
- `src/neo4j_agent_memory/observability/`: explicit tracer/span/decorator APIs rather than universal instrumentation.
- Maintained LangChain and Microsoft integration guides plus corresponding source/package ranges.
- `CONTEXT.md`: NAMS/service/SDK/conformance terminology.
- Existing ontology/skills REST references establish documented contract only; no live service observation is claimed.

## Verification

- Snapshot of pre-edit source and section IDs: `logs/editorial-before.json`, using the prior fresh Antora-rendered audit site. Source heading counts exactly matched HTML heading counts for each page.
- Local render used `docs/node_modules/@antora/asciidoc-loader/node_modules/@asciidoctor/core` with `attribute-missing=warn` and a memory logger.
- `logs/editorial-render-validation.json`: 13 pages, all 286 prior section IDs retained, zero duplicate IDs, zero missing xref target files, zero missing fragment targets, zero Asciidoctor warning/error messages.
- `/tmp/editorial-rendered/*.html`: generated local render evidence; six image macros produce complete descriptions and numeric widths. FAQ and glossary tables render correctly.
- `git diff --check` on owned pages passed.
- No tests added for wording. No paid model calls, model downloads, cloud writes, or live hosted-service exercises were performed.

## Handoff and remaining gaps

1. Root's fresh final Antora build and rendered-link validator remain the site-level acceptance gate; standalone Asciidoctor conversion does not prove final Antora routing, shared UI, deployment, or live service behavior.
2. Root/new canonical `reference/backend-capabilities.adoc` is a deliberate dependency and existed by local link validation. The concept pages use file-level routes to minimize future fragment coupling.
3. Existing reference owners must finish API/configuration corrections in their packages. No new reference/how-to page was necessary for the removed duplicate catalogs. Important destinations: `reference/{schemas,extractors,configuration,schema-objects}.adoc`, `reference/api/{long-term,short-term,memory-client,llm-provider,adapters,factory}.adoc`, `how-to/{entities,deduplication,entity-extraction,entity-extraction-schemas,entity-extraction-batch}.adoc`, and existing integration guides.
4. Diagram binaries may still contain schema/automatic-behavior labels broader than the revised prose; root's visual/diagram stream owns that reconciliation. This agent changed alt markup only and does not assert visual review of the six files.
5. Publishing artifact versions, live NAMS template/feature availability, and upstream current releases require the plan's separate external verification. Prose now avoids fixed live counts and narrows claims to documented/source-visible scope.
6. Operational streaming threshold occurrences and the broader tutorial/how-to Diataxis sweep are outside this completed assignment and remain later work.
