# Contributing to Neo4j Agent Memory

This is a Neo4j Labs project — for general questions and support, please use the [Neo4j Community Forum](https://community.neo4j.com).

Contributions are welcome! Please read the guidelines below before submitting a pull request.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/neo4j-labs/agent-memory.git
cd agent-memory

# Install with uv
uv sync --group dev

# Or use the Makefile
make install
```

## Using the Makefile

The project includes a comprehensive Makefile for common development tasks:

```bash
# Run unit tests
make test

# Run unit tests only
make test-unit

# Run integration tests (auto-starts Neo4j via Docker)
make test-integration

# Code quality
make lint         # Run ruff linter
make format       # Format code with ruff
make typecheck    # Run mypy (strict) type checking
make ty           # Run ty (second, independent type checker)
make check        # Run all checks (lint + format + mypy + ty)

# Docker management for Neo4j
make neo4j-start  # Start Neo4j container
make neo4j-stop   # Stop Neo4j container
make neo4j-logs   # View Neo4j logs
make neo4j-clean  # Stop and remove volumes

# Run examples (see `make help` for the full list)
make example-hello      # Smallest round trip (one PEP 723 file)
make example-basic      # Guided tour of the whole API surface
make example-resolution # Entity resolution strategies (no Neo4j)
make example-no-llm     # Fully local: llm=None + local embedder
make example-langchain  # LangChain 1.x agent (keyless)
make example-pydantic   # PydanticAI 2.x agent (keyless)
make examples           # Run every key-free example
make examples-with-keys # Run the examples that need credentials
make ts-test-examples   # Type-check + test every typescript/examples/* directory

# Full-stack chat agent
make chat-agent-install  # Install backend + frontend dependencies
make chat-agent-backend  # Run FastAPI backend (port 8000)
make chat-agent-frontend # Run Next.js frontend (port 3000)
make chat-agent          # Show setup instructions
```

## Type Safety (Python SDK)

This section covers the **Python SDK** (repo root: `src/`, `benchmarks/`,
`examples/`). The TypeScript SDK under `typescript/` is type-checked separately
by `tsc` via `make ts-lint` (see the TypeScript CI workflow); the `make`
targets and conventions below apply only to the Python code.

The Python SDK is checked by **two** type checkers, both blocking in CI and both
run by `make check`:

- **mypy** in `--strict` mode is the source of truth (`make typecheck`).
- **ty** (Astral's checker) is the second, independent checker whose
  complementary rules must also pass (`make ty`).

The checked surface is `src`, `benchmarks`, the top-level `examples/*.py` demos
and every `examples/<dir>/main.py` entrypoint, and it is kept at **zero**
errors/diagnostics — CI fails on any new error. Run the checkers with the
integration extras installed (`uv sync --all-extras --group dev`) so
`integrations/` is analyzed against real framework types.

`make lint` / `make format-check` cover `src tests examples` — the whole examples
tree, not just the top-level scripts. mypy cannot take more than one `main.py`
per invocation ("Duplicate module named main") and the example directories are
hyphenated so they cannot be packages, which is why `make typecheck` loops over
them one file at a time.

Follow these conventions when adding or changing code:

1. **`from __future__ import annotations`** at the top of every module, so
   annotations are lazy strings and heavy/optional types can be imported under
   `TYPE_CHECKING`.
2. **`TYPE_CHECKING` imports** for annotation-only types (framework types, heavy
   modules, cross-layer models).
3. **Match overridden signatures to the base class** — frameworks supply real
   types; use them rather than `Any`.
4. **`Protocol` over `Any`** for structural/duck-typed boundaries;
   **generics (`TypeVar`/`ParamSpec`)** over `Any` for pass-through helpers.
5. **Narrow `X | None` through a local** before use — instance attributes do not
   narrow across `await`/calls.
6. **Prefer fixing the type over casting.** A `cast` is a last resort at a
   genuine external boundary, with an inline comment stating why. Never launder
   a value through `object` (`cast(T, cast(object, x))`) to silence a
   cross-hierarchy mismatch — fix the underlying types instead.
7. **Every `# type: ignore` is coded and justified** — `# type: ignore[code]  # why`.
   Bare ignores are forbidden; `warn_unused_ignores` removes stale ones.
8. **Legitimate `Any` (keep, document):** `**kwargs: Any` forwarding,
   arbitrary-JSON tool inputs/results, framework model objects at the boundary,
   Neo4j record dicts, and heterogeneous dispatch tables. Anything else is a
   defect — replacing `Any` with a suppression is a regression, not a fix.

### Backend-typed clients

The base Protocols (`ShortTermProtocol` / `LongTermProtocol` /
`ReasoningProtocol` in `core/protocols.py`) are the backend-agnostic contract —
every method is honored by both the self-hosted (bolt) and hosted (NAMS)
backends. `MemoryClient` is generic over them (PEP 696 defaults), so
`MemoryClient(settings)` (and `async with MemoryClient(settings)`) types
`client.short_term` / `.long_term` / `.reasoning` as the base Protocols.
Framework integrations are written against this agnostic `MemoryClient` and
must stay agnostic — do not add an `integrations/` call only one backend
supports.

For bolt-only functionality (geospatial, geocoding, dedup, provenance,
tool-usage stats, `add_messages_batch`, `extract_entities_from_session`), get a
backend-typed client rather than casting: `await connect(BoltSettings(...))` →
`BoltMemoryClient` (its `.short_term`/`.long_term`/`.reasoning` are the concrete
bolt classes), `await connect(NamsSettings(...))` → `NamsMemoryClient`.
`connect()` returns an already-connected client (not a context manager — call
`await client.close()`).

## Running Examples

Examples are located in `examples/` and demonstrate various features:

[`examples/README.md`](examples/README.md) is the full gallery, with a chooser
table and the conventions every example follows. A selection:

| Example | Description | Requirements |
|---------|-------------|--------------|
| [`hello-memory/`](examples/hello-memory/) | The smallest round trip: one PEP 723 file, thirteen lines of body | `uv` only (NAMS key **or** Neo4j) |
| [`basic_usage.py`](examples/basic_usage.py) | Guided tour: twelve sections across all three memory types | Neo4j (OpenAI key optional) |
| [`entity_resolution.py`](examples/entity_resolution.py) | Entity matching strategies | None (optional: `fuzzy` extra, plus an embedder for the semantic section) |
| [`enrichment_example.py`](examples/enrichment_example.py) | Wikipedia/Diffbot entity enrichment | Neo4j, network access (Diffbot key optional) |
| [`langchain_agent.py`](examples/langchain_agent.py) | LangChain 1.x `create_agent` + memory middleware | Neo4j, `langchain-agents` extra (OpenAI optional) |
| [`pydantic_ai_agent.py`](examples/pydantic_ai_agent.py) | PydanticAI 2.x agent with memory tools and traces | Neo4j, `pydantic-ai` extra (OpenAI optional) |
| [`no_llm/`](examples/no_llm/) | Fully local: `llm=None`, local embedder, spaCy + GLiNER | Neo4j, `extraction` + `sentence-transformers` extras |
| [`domain-schemas/`](examples/domain-schemas/) | GLiNER2 domain schemas (8 domains) via one runner | `gliner` extra, optional Neo4j |
| [`nams-quickstart/`](examples/nams-quickstart/) | Hosted backend end to end, no Neo4j to operate | `MEMORY_API_KEY`, `nams` extra |
| [`claude-code-team-memory/`](examples/claude-code-team-memory/) | One memory graph shared across a team's editors, no agent code | `MEMORY_API_KEY` (configs validate offline) |
| [`strands-session-manager/`](examples/strands-session-manager/) | `Neo4jSessionManager` on `Agent(session_manager=...)` | Neo4j, `strands` extra (no API key) |
| [`full-stack-chat-agent/`](examples/full-stack-chat-agent/) | FastAPI + PydanticAI + Next.js 16 over two Neo4j graphs | Neo4j, OpenAI, Node 22+ |
| [`lennys-memory/`](examples/lennys-memory/) | **Flagship Python demo**: podcast knowledge graph, 28-tool agent, graph + map views | Neo4j, OpenAI, Node 22+ |
| [`financial-services-advisor/`](examples/financial-services-advisor/) | Multi-agent KYC/AML compliance, implemented twice (AWS Strands + Bedrock, Google ADK + Gemini) | Neo4j, AWS or GCP credentials, Node 22+ |
| [`typescript/examples/`](typescript/examples/) | Nine TypeScript examples; flagship `nextjs-memory-chat/` | `MEMORY_API_KEY`, Node 22+ (24+ for `eve-commerce-agent`) |

### Environment Setup

Examples load environment variables from `examples/.env`. Copy the template:

```bash
cp examples/.env.example examples/.env
# Edit examples/.env with your settings
```

Key variables:
- `NEO4J_URI` - If set, uses this Neo4j; if not set, auto-starts Docker
- `NEO4J_PASSWORD` - Neo4j password (`test-password` for Docker)
- `OPENAI_API_KEY` - Required for OpenAI embeddings and LLM extraction

```bash
# Run with your own Neo4j (uses NEO4J_URI from .env)
make example-basic

# Or without .env (auto-starts Docker Neo4j)
rm examples/.env  # Ensure no .env file
make example-basic  # Will start Docker with test-password
```

## Testing

### Environment Variables

```bash
# Control integration test behavior
RUN_INTEGRATION_TESTS=1      # Enable integration tests
SKIP_INTEGRATION_TESTS=1     # Skip integration tests
AUTO_START_DOCKER=1          # Auto-start Neo4j via Docker (default: true)
AUTO_STOP_DOCKER=1           # Auto-stop Neo4j after tests (default: false)
```

### Integration Test Script

```bash
# Keep Neo4j running after tests (useful for debugging)
./scripts/run-integration-tests.sh --keep

# Run with verbose output
./scripts/run-integration-tests.sh --verbose

# Run specific test pattern
./scripts/run-integration-tests.sh --pattern "test_short_term"
```

### Test Categories

```bash
# Unit tests (fast, no external dependencies)
pytest tests/unit -v

# Integration tests (requires Neo4j)
pytest tests/integration -v

# Example validation tests
pytest tests/examples -v

# All tests with coverage
pytest --cov=neo4j_agent_memory --cov-report=html
```

## CI/CD Pipeline

This project uses GitHub Actions for continuous integration and deployment.

### Workflow Overview

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| **Python CI** (`ci-python.yml`) | Push to `main`, PRs touching `src/**`, `tests/**`, `docs/**`, etc. | Linting, type checking, tests, build validation |
| **TypeScript CI** (`ci-typescript.yml`) | Push to `main`, PRs touching TypeScript sources, examples or related docs | Lint, vitest (unit + integration), build, packed-artifact check, per-example type-check matrix |
| **TypeScript E2E** (`e2e-typescript.yml`) | Push, PR, nightly | Run TypeScript SDK e2e suite against live NAMS sandbox (uses `MEMORY_API_KEY` secret) |
| **Publish Python** (`publish-python.yml`) | Git tags `python-v*` | Build and publish to PyPI, create GitHub releases |
| **Publish TypeScript** (`publish-typescript.yml`) | Git tags `typescript-v*` | Build and publish to npm with provenance |
| **Publish NAMS AI provider** (`publish-nams-ai-provider.yml`) | Git tags `nams-ai-provider-v*` | Type-check, test, build and publish `@neo4j-labs/nams-ai-provider` to npm with provenance, then create a GitHub Release |
| **TypeDoc** (`docs-typedoc.yml`) | Push to `main` (TS docs paths) | Regenerate TypeDoc API reference into `docs/modules/ROOT/attachments/api/typescript/`, commit to `main`, and dispatch the labs-pages docs rebuild |
| **TCK Conformance** (`tck-conformance.yml`) | workflow_dispatch (nightly schedule inactive) | Run agent-memory-tck Bronze suite against the published `@neo4j-labs/agent-memory` package |
| **NAMS Integration** (`nams-integration.yml`) | Push, PR, nightly | Run NAMS sandbox integration tests (Python side, uses `NAMS_SANDBOX_KEY` secret) |

### CI Jobs

1. **Lint** - Code quality checks using Ruff (`ruff check` + `ruff format --check`)
2. **Type Check** - Static type analysis using mypy on `src/`
3. **Unit Tests** - Python 3.10, 3.11, 3.12, 3.13 with coverage (uploaded to Codecov)
4. **Integration Tests** - Neo4j 5.26 via GitHub Actions services, matrix across Python versions
5. **Example Tests** - Quick validation (no Neo4j) + full validation (with Neo4j)
6. **Build** - Package build validation, wheel/sdist, install + import check

### Running CI Locally

Before submitting a PR, run the same checks locally:

```bash
# Run all checks (recommended before PR)
make ci

# Or run individual checks:
make lint        # Ruff linting
make format      # Auto-format code
make typecheck   # Mypy type checking
make test        # Unit tests only
make test-all    # Unit + integration tests
```

### Pull Request Requirements

All PRs must pass these checks before merging:
- Lint (ruff check)
- Format (ruff format)
- Unit tests (all Python versions)
- Integration tests
- Build validation

## Code Style

- **Formatter**: Ruff (line length: 88)
- **Linter**: Ruff
- **Type Checker**: mypy (strict mode)
- **Docstrings**: Google style

## Development Workflow

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Run `make ci` to validate
5. Commit with descriptive messages
6. Push and open a PR against `main`

## Publishing

This repo ships three independently versioned packages. Each has its own
tag prefix and publish workflow.

### Python (neo4j-agent-memory → PyPI)

1. Update the version in **both** `pyproject.toml` (`[project] version`) and
   `src/neo4j_agent_memory/__init__.py` (`__version__`). The two are maintained
   by hand; `tests/unit/test_version_consistency.py` fails if they drift, and
   `uv build` takes the `pyproject.toml` value.
2. Cut the release section in `CHANGELOG.md`: rename `## [Unreleased]` to
   `## [<version>] - <YYYY-MM-DD>`, open a fresh empty `## [Unreleased]` above
   it, and add the matching link reference at the bottom of the file.
3. Merge that to `main` and wait for CI to go green — nothing downstream
   re-runs the test suite.
4. Create and push a tag with the **`python-v`** prefix:
   ```bash
   git tag python-v0.6.0
   git push origin python-v0.6.0
   ```
5. `publish-python.yml` verifies the tag against the versions from step 1,
   then builds and publishes to PyPI and creates a GitHub Release. The `pypi`
   environment has no approval gate, so the push publishes — and a version
   number, once claimed on PyPI, cannot be reused.

### TypeScript (@neo4j-labs/agent-memory → npm)

1. Choose the intended release version and update both `typescript/package.json`
   and `typescript/src/version.ts`, plus applicable lockfile version entries.
2. Update `typescript/CHANGELOG.md`; distinguish released behavior from source-only work.
3. From `typescript/`, run `npm run check:version`, `npm run lint`, the applicable
   tests, `npm run build` and `npm pack --dry-run`. The existing build rejects a
   manifest/`VERSION` mismatch.
4. Review the resulting artifact and release changes. Only an authorized release
   should create and push the matching `typescript-v<X.Y.Z>` tag.
   `publish-typescript.yml` publishes the package with npm provenance and creates
   a GitHub Release. Do not infer publication from the source version alone.

### NAMS AI provider (@neo4j-labs/nams-ai-provider → npm)

The Vercel AI SDK provider in `typescript/packages/vercel-ai-provider/` is
released independently of the SDK.

1. Update `version` in `typescript/packages/vercel-ai-provider/package.json`
   (and its `package-lock.json` entry). If the release needs a newer SDK, widen
   the `@neo4j-labs/agent-memory` peer range there too.
2. Add the release section to `typescript/packages/vercel-ai-provider/CHANGELOG.md`.
3. From `typescript/packages/vercel-ai-provider/`, run `npm ci`,
   `npm run typecheck`, `npm test`, `npm run build` and `npm pack --dry-run`.
4. Update the hand-maintained `docs/modules/ROOT/pages/reference/nams-ai-provider.adoc`
   if the provider's API changed; it has no TypeDoc generation.
5. Only an authorized release should create and push the matching
   `nams-ai-provider-v<X.Y.Z>` tag. `publish-nams-ai-provider.yml` publishes
   the package with npm provenance and creates a GitHub Release. The workflow
   does not compare the tag with `package.json`, so check that they match
   before pushing.

> Tag prefixes are enforced by the publish workflows. Plain `v*` tags
> will not trigger a publish.

## TypeScript Contributions

The TypeScript SDK lives at [`typescript/`](typescript/). It is a thin
HTTP client over the NAMS REST API and ships five framework integrations
(Vercel AI SDK, MCP, LangChain JS, Mastra, AWS Strands).

### Setup

Requires Node.js 22+.

```bash
cd typescript
npm ci
npm run build   # required before installing any file:../.. example
```

### Common commands

```bash
# From the repo root
make ts-install       # cd typescript && npm ci
make ts-build         # cd typescript && npm run build
make ts-test          # cd typescript && npm test
make ts-test-unit     # cd typescript && npm run test:unit
make ts-lint          # cd typescript && npm run lint
make ts-docs          # cd typescript && npm run docs:api (TypeDoc)
make ts-conformance   # cd typescript && npm run conformance:server (TCK bridge)

# Or directly inside typescript/
npm run lint
npm run test:unit
npm run test:integration
npm run test:tck         # In-tree TCK Bronze conformance (RUN_TCK_BRIDGE=1)
npm run build
npm pack --dry-run       # Verify the publishable artifact
```

### TCK conformance

The TypeScript SDK is verified against the cross-language
[`agent-memory-tck`](https://github.com/neo4j-labs/agent-memory-tck)
behavioral spec. The in-tree bridge suite at `typescript/test/tck/` is opt-in through
`npm run test:tck`; the regular `npm test` command runs unit and integration tests.
The `tck-conformance.yml` workflow runs the TCK Bronze suite against the
**published** npm package to catch packaging regressions. It has a nightly
schedule, but its job runs only on manual `workflow_dispatch` until the
TCK-side package runner lands.

To run the bridge server locally for cross-language testing:

```bash
make ts-conformance    # or: cd typescript && npm run conformance:server
# Bridge listens on TCK_BRIDGE_PORT (default 3001)
```

### Code style (TypeScript)

- **Lint/type checks**: `npm run lint` runs `tsc --noEmit` for source and
  `tsc -p tsconfig.test.json` for tests/conformance; it does not invoke ESLint.
- **Tests**: vitest (`test/unit`, `test/integration`, `test/e2e`,
  `test/tck`)
- **Build**: tsup → `dist/` (ESM + type declarations)
- **Engines**: Node 22+, but written to run on Bun, Deno, Cloudflare
  Workers, and Vercel Edge

### Adding a new framework integration

1. Add `src/integrations/<name>.ts`
2. Wire a subpath export in `typescript/package.json` under `exports`
3. Add a runnable example at `typescript/examples/<name>/`
4. Add a how-to guide at `docs/modules/ROOT/pages/how-to/typescript/<name>.adoc`
5. Add a row to the integrations table in
   `docs/modules/ROOT/pages/sdks/typescript.adoc` and in
   `typescript/README.md`

## Documentation

Follow the repository's four documentation skills in `.claude/skills` and [Maintain the documentation](docs/MAINTAINING.md). Pages follow Diataxis, with one path and observable outcomes in tutorials, complete verified procedures in how-tos, source-backed API contracts in reference, and rationale in explanations.

```bash
make docs-install
make docs-render-install
make docs
make docs-lint
make docs-serve
```

The output is `docs/build/site`. The preview command builds once and serves static files; rebuild and refresh after edits. Add every page to its quadrant/subindex and sidebar, preserve existing URL/anchor routes, and check rendered fragments and image attributes. Keep complete tutorial programs in maintained example resources and run their targeted checks.

Publishing the site and releasing SDK artifacts are separate steps. Use the maintainer guide's publication handoff; do not edit generated TypeDoc HTML or OpenWiki by hand.
