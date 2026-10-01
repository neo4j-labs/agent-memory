# Documentation implementation evidence — 13 September 2026

This directory records repository-level implementation of W00–W14. The authoritative disposition is [the remediation ledger](../../../DOCUMENTATION_REMEDIATION_STATUS.md). Individual lane reports retain the history of focused checks and earlier failures; their intermediate totals and pending cross-lane notes are superseded by the combined results here.

## Scope and baseline

- Starting revision: `0303dc0066e0c071c8468536acc6219c183e2f97`, branch `docs-review`. No commit or publication was created by the implementation task.
- Original audit: 111 authored Antora pages, 18 broken rendered content links, 46 direct quadrant-index omissions, 12 malformed image descriptions and 78 missing-attribute warnings. The two dated review/plan files were preserved.
- Final source inventory: 112 pages. The added page is the canonical backend-capabilities reference. The old configuration page remains a forwarding page with compatible anchors.
- [All 48 repository README variants](readme-sweep.md) and CONTRIBUTING were rescanned for command, runtime, package/release and lifecycle claims. Historical design documents and generated OpenWiki were not rewritten.
- Four repository skills supplied the review criteria: Diataxis, branding, Excalidraw and Labs. Native Excalidraw font portability and the official shared UI font choices are documented exceptions in [MAINTAINING.md](../../MAINTAINING.md).

## Environment and reproducibility

Validation used macOS, Node `v24.11.1`, system Python `3.14.6`, the existing development environment and a temporary Python environment with declared framework extras and FastMCP 4.0.3. This does not establish execution across every supported runtime version. Framework versions/install commands are recorded in [integration evidence](integration-howtos.md); CI repeats the relevant checks in its declared environment.

Antora dependencies came from the committed docs npm lockfile. The downloaded official UI bundle used for every final build has SHA-256:

```text
e8a9dce084f88f5b715d0418c4ac36f98b2b3cc1cc70ea35101a4968698c53be
```

`ANTORA_UI_BUNDLE` selected this local bundle because the playbook's upstream latest-bundle URL is mutable. The build tool accepts an equivalent downloaded official bundle without changing the playbook.

These are the main commands, run from the repository root unless indicated otherwise:

```bash
ANTORA_UI_BUNDLE=/tmp/agent-memory-audit-ui.zip \
  python3 scripts/check_docs.py --build --report /tmp/agent-memory-docs-quality-final.json

ANTORA_UI_BUNDLE=/tmp/agent-memory-audit-ui.zip \
  /tmp/agent-memory-docs-integrations/bin/python -m pytest tests/docs \
  --ignore=tests/docs/test_tutorial_examples.py \
  --ignore=tests/docs/test_howto_examples.py -q

.venv/bin/ruff check scripts/check_docs.py scripts/manage_diagrams.py \
  tests/docs docs/modules/ROOT/examples
.venv/bin/ruff format --check scripts/check_docs.py scripts/manage_diagrams.py \
  tests/docs docs/modules/ROOT/examples

git diff --check
```

Temporary paths identify this run, not prerequisites for future maintainers. Use the setup commands in MAINTAINING.md to create an equivalent environment. The two excluded legacy suites require a live database; their status is **not run**, not passed. The 143 signature/ellipsis snippets skipped by the syntax pass are intentionally partial reference displays. Complete-program and source-signature contracts are separate tests.

## Final verification

| Area | Evidence and result |
|---|---|
| Source, routes and fresh rendering | [Quality report](quality-report.json): all 112 authored pages, zero source errors, zero build warnings/errors, zero rendered file/fragment/image failures; 257 HTML files include existing generated attachments |
| Combined docs tests | [605 passed, 143 skipped](docs-tests.log), including the new source/model/CLI/program/polling/adapter/build/diagram/packaging regressions |
| Python references/core examples | [266 focused checks](python-tutorials-and-howtos.md): 186 reference contracts plus 80 core tutorial/how-to checks; these are included in the combined total |
| Hosted/framework tutorials | [Complete programs and lifecycle checks](hosted-and-framework-tutorials.md), using fake service/model boundaries and actual public models |
| Python framework recipes | [29 contracts and 197 existing adapter regressions](integration-howtos.md); actual constructors/protocols with no paid inference or live data |
| Operational recipes | [18 final contracts](operational-howtos.md): extraction failures, persisted-ID reuse, buffered failure accounting, stable entity backfill and provider dimensions; included in combined docs total |
| TypeScript SDK/examples | [367 SDK + 145 example tests](typescript.md); SDK build/lint, all nine example installs/typechecks, clean no-dist bootstrap, real local HTTP/stdio boundaries and two-process recall |
| TypeDoc | Supported source generation produced 144 temporary HTML files with zero warnings/errors. [All 3,884 internal generated links](typedoc-links.json) resolve across the 144 files. AST comparison of all five changed SDK source files after comment removal matched HEAD. Tracked generated attachments have no changes. |
| Old URLs/sections | [Anchor comparison](anchor-check.json): all 111 original pages rendered for ID comparison, no meaningful old IDs lost; generic `preamble` IDs on two restructured pages intentionally excluded |
| Literal placeholders | [Before/after warning evidence](literal-placeholder-check.json): escaped intended URI placeholders preserve their displayed values; no global warning suppression |
| CI | [Exact contract command and workflow review](ci-integration.md): explicit supplemental Microsoft client dependency, preserved environment, YAML and shell checks; Ruff covers all 24 Antora Python fixtures |
| Diagrams | [Manifest](../../diagrams/manifest.json) checks active image coverage, source/output SHA-256, native scene IDs/bindings and explicit source exceptions. [Source migrations](../../diagrams/source-migrations.json) account for all 37 old paths. |
| Desktop and phone layouts | [Eight page/viewport checks](viewport-checks.json), at 1440px and 390px: no horizontal page overflow, no unloaded images. Homepage, first tutorial, core reference and Microsoft integration inspected. |

Focused suite totals overlap. The TypeScript suites use local HTTP/stdio and framework fixtures; their successes do not establish deployed NAMS behavior. Existing Eve fake tests remain useful internal regression tests but do not validate its unsupported hosted profile/order paths.

## Visual review and provenance

Forty-five canonical scenes are retained under `docs/assets/diagrams/excalidraw`, including preserved legacy variants. Twenty-seven active/retained export records are in the manifest. Native Excalidraw export uses the pinned optional toolchain in `docs/diagrams`, separate from Antora. Editable source validity alone is not accepted as visual verification.

Final [message, reasoning and buffered-write corrections](diagram-final-corrections.json) align graph glyphs, actual labels, recorded order and queue backpressure/failure semantics with source. The review covers semantic memory colors, meaningful titles and labels, connector direction, backend applicability, readable text at actual page size, white backgrounds for theme independence, full alt descriptions and full-size links. Generic architecture replaced the example-specific landing diagram. Unsupported numerical benchmark/identity claims were removed from graphics as well as prose. The TfL screenshot is explicitly a captured illustration of that separate example; lack of editable source is recorded as an exception, not an invented source match.

Representative final screenshots:

- [Desktop homepage](homepage-desktop.png)
- [Phone homepage](homepage-mobile.png)
- [Homepage architecture at rendered size](homepage-diagram.png)

## Source-backed capability and order checks

The new [backend reference](../../modules/ROOT/pages/reference/backend-capabilities.adoc) distinguishes Python Bolt, Python NAMS and TypeScript NAMS, including preferences/facts/relationship writes, extraction configuration/readiness, reasoning and MCP registration. Conversation IDs/user metadata/unused namespace are not represented as universal authorization or search filters. The current ignored Bolt semantic-search filter remains a product follow-up; a documentation caveat does not require or prove a runtime fix.

Graph adoption starts with prerequisites and dry-run; admin API key categories precede schema/user/ontology actions; migrations explain unsupported stores and preserved identity. Local MCP and hosted service tools are described separately. Registration counts are scoped to a profile/backend and do not imply every registered operation is supported.

The [conceptual review](concepts.md) separates durability, retrieval/context assembly and model calls; traces record application-supplied actions/outcomes. It removes unsupported empirical comparisons and identity examples that merge distinct product variants. Full Labs support/deprecation wording is prominent in root README, PyPI README and the site homepage.

## Released artifacts

[Public artifact metadata](public-artifacts.json) was fetched on `2026-09-13T17:56:44Z`. The selected wheel and npm archive were downloaded and verified against published digests. This was archive/API-declaration inspection, not a clean installed-artifact walkthrough.

- PyPI `neo4j-agent-memory==0.5.0`: Python `>=3.10`, wheel SHA-256 `d054fcdc34fbbe70d0e405e9349f1ad0fd57159d45a87e2624b5fb54a1761384`. Eighty-four of 133 packaged Python files differ from the checkout. Current `connect`, `BoltSettings` and `NamsSettings` exports are absent. The sdist digest in metadata was recorded but that archive was not downloaded.
- npm `@neo4j-labs/agent-memory@0.4.1`: published Node `>=20.0.0`; archive SHA-1 `a3961f988d2dac58e7f9e1a100e3a6ab2d5d16b6`. Its middleware declarations differ from the current source's V4 interface and Node 22 requirement. Matching version strings do not make these surfaces identical.

Docs label source examples accordingly. SDK release owners must verify the selected released artifacts before claiming source examples work unchanged with the public packages.

## Cloud Run and external limits

The previous Docker COPY omitted the readme declared by Hatchling. In an isolated metadata check containing the original copied inputs, validation raised `OSError: Readme file does not exist: README-pypi.md`; adding the actual readme allowed metadata validation. A regression now checks packaging inputs before installation. This proves the repaired input defect, not completion of a full container build.

The [Cloud Run source-contract review](cloudrun.md) also corrected the provider setup: installing the Google extra does not select Google defaults. The image now explicitly selects Bolt, Vertex embeddings at 768 dimensions, ADC/project identity and disabled extraction/automatic preference detection. Four packaging/CLI/provider/template checks pass with remote boundaries mocked. The Docker engine was not running, so the complete image, MCP startup and Cloud Run invocation were not executed. Deployment files and prerequisites were reviewed against the CLI source and Google's current official command/authentication documentation. Changes preserve private invocation and identify runtime identity, secret grants and the authenticated proxy path. See the [deployment README](../../../deploy/cloudrun/README.md) for the selected configuration and protocol/readback sequence.

No live NAMS, Neo4j database, provider model, cloud resource, IAM policy or public documentation was changed. Live primary outcomes, service/UI tool inventories, Cloud Run, matching SDK releases, normal TypeDoc regeneration and labs-pages/cache/redirect verification remain explicitly owned gates in the remediation ledger.

## Failure handling

The `logs` directory preserves available focused failures and successful checks. Initial fixture mistakes, dependency-version mismatches and sandbox loopback restrictions were corrected or rerun in an authorized environment; they are not reclassified as SDK behavior. The build checker was additionally corrected after final inspection exposed Antora JSON diagnostics using string levels (`warn`) instead of numeric levels. New regression cases exercise both formats; the final build log is empty and the checker no longer silently ignores those warnings. The final combined run also exposed a missing FastMCP module in the temporary framework environment. Installing the already-declared dependency resolved that failure; the [full failed run](logs/combined-docs-missing-fastmcp.log) and final passing output are both retained.

Generated OpenWiki was untouched. Generated TypeDoc was inspected through supported temporary generation, not hand-edited. The two original dated review/plan artifacts remain unchanged historical records.
