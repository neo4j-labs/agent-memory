# Maintain the documentation

The published content is an Antora component. Edit pages under `modules/ROOT/pages`, navigation in `modules/ROOT/nav.adoc`, reusable fragments in `modules/ROOT/partials`, and published images in `modules/ROOT/images`.

## Build and check

From the repository root, use Node.js 22 and Python 3.10 or later for the combined maintainer workflow:

```bash
make docs-install
make docs
make docs-lint
```

`make docs-install` uses the committed npm lockfile. Antora's package itself permits Node 18+, but the current SDK/examples have separate runtime requirements. `make docs` writes `docs/build/site`. `make docs-lint` checks source/index routes, builds once into a fresh temporary directory, and validates rendered content links, fragments, image attributes, warnings and diagram provenance/freshness. It does not contact NAMS or execute embedded model/database/deployment commands.

To retain the complete build log and page inventory:

```bash
python3 scripts/check_docs.py --build --report docs/build/quality-report.json
```

`ANTORA_UI_BUNDLE=/absolute/path/to/ui-bundle.zip` selects a downloaded official UI bundle when offline. Record its hash with release evidence: the playbook's `ui-bundle-latest.zip` URL is mutable. `python3 scripts/check_docs.py --site-dir docs/build/site` checks an existing artifact but does not prove that artifact is fresh.

For a local preview:

```bash
make docs-serve
```

The command builds once and serves static files on port 8080. After an edit, run `make docs` in another terminal and refresh the browser. There is no supported watch/live-reload command.

## Write and review a page

Follow the four repository skills in `.claude/skills`. Tutorials follow one complete path with named files, exact setup/run commands and observable milestones. How-tos state a task, prerequisites, ordered solution and final verification. Reference follows the actual API and includes options, defaults, constraints and backend applicability. Explanations focus on rationale and tradeoffs.

Use Neo4j AuraDB for examples that connect directly to Neo4j through the Bolt backend. The shared `aura-tutorial-setup.adoc` and `aura-tutorial-cleanup.adoc` partials provide the tutorial path. Executable tutorial helpers read the exported `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` and `NEO4J_DATABASE` through `docs/modules/ROOT/examples/aura_connection.py`; never supply a local database or fixed password as a fallback. NAMS examples retain their NAMS service configuration. Reference tables must still document actual SDK defaults, and Docker instructions for application packaging or contributor tests serve a separate purpose.

Keep a complete executable counterpart for programs assembled across several tutorial steps. Identify partial snippets and signature displays as such. Use a named deterministic fixture for automated checks; never run every extracted code block indiscriminately. Keep source-contract checks, local integration, paid-model calls, live service checks and public-site verification separate.

Use `xref:` for internal pages. Add every page to its quadrant index or a linked subindex and to the sidebar. Preserve old anchors when restructuring; retain a forwarding page or use a verified Antora alias for an old URL. Do not create empty placeholder pages solely to silence link checks.

The main regression checks are:

```bash
uv run pytest tests/docs/test_docs_checker.py tests/docs/test_links.py -q
uv run pytest tests/docs/test_build_pipeline.py -q
```

Maintained Python programs under `docs/modules/ROOT/examples` are included by Antora's `example$` resource syntax. Both CI and `make lint` / `make format-check` run Ruff over this directory, including its integration subdirectory.

Python user instructions install the verified PyPI release (`neo4j-agent-memory==0.6.0`, with the selected extras). Every tutorial is self-contained: show the complete program and every local helper on the same page, with a listing title such as ``.Save as `first_agent_memory.py` ``. Use full `include::example$...[]` resources so the displayed code stays tied to its maintained source. Long supporting files can use collapsible blocks, but readers must be able to copy the complete file directly from the page. Do not require a repository checkout, a ZIP, or another code download to run the Python lessons.

Use `python-tutorial-setup.adoc` for the reusable local folder/environment setup. Commands run from `~/agent-memory-tutorials`, using the exact relative filenames shown by the page. Continuing readers retain their existing environment, configuration and private state. For a how-to that explains selected tagged functions and then runs a complete script, also provide all complete files needed by that command.

`extensions/example-files.js` registers the explicitly listed standalone how-to scripts and templates as Antora example resources during `contentClassified`. This keeps the rendered listings tied to their maintained sources outside the docs directory; it creates no downloadable archive. Keep the extension enabled in publishing playbooks and build from the accepted full repository checkout so those sources are available. Fresh-build tests compare the named, rendered files with their canonical source and verify that the tutorial programs' local imports are supplied on the same page.

Package availability and API compatibility must be verified independently of the repository version. Python 0.6.0 still has the older active-ontology metadata inference; the ontology lesson explicitly reads the authoritative REST response through its maintained helper. TypeScript retains its source build instructions until a compatible npm artifact is published; Python's version does not identify an npm release. Contributor development, SDK builds and release checks remain source workflows.

The framework documentation contracts run in Python CI's `integration-test` job, which installs all framework extras. Run the same offline checks locally with:

```bash
uv sync --group dev --all-extras
uv pip install 'agent-framework-openai>=1.13,<2'
uv run --no-sync pytest tests/docs/test_python_integration_contracts.py -q --timeout=60
```

The `microsoft-agent` extra installs the framework core; the separate OpenAI client is required for the actual `OpenAIChatClient` constructor check. Use `--no-sync` after this supplemental installation so the test command retains that environment. These contracts use real adapter/framework classes with fake model and storage boundaries; they do not require Neo4j, NAMS keys or paid model calls. Missing framework dependencies fail the contract suite rather than silently skipping it.

Install the declared dependency environment before import/integration checks. Preserve full failures; do not change correct imports to accommodate an older unrelated environment. Build tests reuse one fresh artifact and do not implicitly install npm packages.

## Visual policy and diagrams

Use the official Neo4j shared UI for site chrome. Its Public Sans/Roboto Mono typography is an intentional upstream-UI exception to local font suggestions; this project does not fork the UI to override those fonts. Use Labs purple for project framing and preserve semantic diagram colors: short-term green, long-term yellow, reasoning purple, storage blue. Native Excalidraw scenes retain portable renderer fonts (hand-drawn or sans-serif) rather than embedding a web font into every scene. This is a documented export exception; legibility and semantic color roles still apply. Keep actual API and schema property names even when generic style examples prefer another casing. Graph nodes use ellipses; components use rounded boxes. Use sentence-case authored headings.

Editable documentation scenes live under `docs/assets/diagrams/excalidraw`; exports live under `docs/modules/ROOT/images/diagrams`. Example-specific scenes can remain with the example. The diagram manifest records source/export paths, hashes, referring pages, and any unresolved legacy provenance. Source validity alone does not establish readable rendering.

Optional export tools are isolated from the Antora build:

```bash
npm ci --prefix docs/diagrams
cd docs/diagrams
npx playwright install chromium
cd ../..
node scripts/export_diagrams.mjs docs/assets/diagrams/excalidraw/the-three-layer-memory-architecture.excalidraw docs/modules/ROOT/images/diagrams/the-three-layer-memory-architecture.svg
python3 scripts/manage_diagrams.py status
```

The exporter uses Excalidraw's own renderer and writes SVG plus PNG with a white background for readable text in either site theme. `DIAGRAM_TOOLS_DIR` can select a temporary dependency installation; `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` can select an existing compatible Chromium executable. Review the output at actual page size and a narrow viewport before recording fresh hashes. Do not mark unknown legacy source/export pairs as verified merely because their filenames match.

Quote comma-containing image descriptions, for example `image::diagrams/example.svg["Conversations, entities and traces",width=100%]`. Alt text is an accessible description; a preceding `.Diagram title` is a visible caption.

Root, PyPI and site entry points carry the full Labs support disclaimer. Nested pages may link to that support context. Do not label an experimental adapter or demo production-ready because its upstream framework is stable. Unused custom logo assets are retained as historical assets until their consumers are established; they are not approved replacements for the official header.

## API generation and publication

TypeScript JSDoc and source types feed `.github/workflows/docs-typedoc.yml`, which regenerates committed attachments under `docs/modules/ROOT/attachments/api/typescript`. Fix source comments when they misdescribe behavior; do not hand-edit generated HTML. Inspect generation into temporary output before relying on the normal workflow. Generated OpenWiki follows its own scheduled workflow and must not be manually repaired.

The repository identifies labs-pages as the publication route for these docs and TypeDoc attachments. The external hosting owner must confirm the actual publication configuration, base path and cache behavior. There is no maintained `docs/vercel.json` or in-repository GitHub Pages publication workflow to configure here.

Before publication, record the accepted commit, SDK artifacts actually tested, UI bundle identity, generated TypeDoc revision, complete build/check output, page moves/redirects and remaining live verification. Verify the accepted public pages and old URLs after deployment. A repository fix is not proof that a package was released, the site republished, or a CDN cache refreshed.

Historical audits and plans remain dated snapshots. Track repair dispositions and the release/service/site handoff in `DOCUMENTATION_REMEDIATION_STATUS.md` at the repository root.
