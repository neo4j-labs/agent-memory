# Contributing to Neo4j Agent Memory

Contributions are welcome! Please read the guidelines below before submitting a pull request.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/neo4j-labs/agent-memory.git
cd agent-memory/neo4j-agent-memory

# Install with uv
uv sync --group dev

# Or use the Makefile
make install
```

## Using the Makefile

The project includes a comprehensive Makefile for common development tasks:

```bash
# Run all tests (unit + integration with auto-Docker)
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

# Run examples
make example-basic      # Basic usage example
make example-resolution # Entity resolution example
make example-langchain  # LangChain integration example
make example-pydantic   # Pydantic AI integration example
make examples           # Run all examples

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

The checked surface is `src`, `benchmarks`, and the top-level `examples/*.py`
demos, and it is kept at **zero** errors/diagnostics — CI fails on any new
error. Run the checkers with the integration extras installed
(`uv sync --all-extras --group dev`) so `integrations/` is analyzed against real
framework types.

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

| Example | Description | Requirements |
|---------|-------------|--------------|
| [`lennys-memory/`](examples/lennys-memory/) | **Flagship demo**: Podcast knowledge graph with AI chat, graph visualization, map view, entity enrichment | Neo4j, OpenAI, Node.js |
| [`financial-services-advisor/`](examples/financial-services-advisor/) | **AWS Strands demo**: Multi-agent KYC/AML compliance with 5 specialized agents, CDK deployment | Neo4j Aura, AWS Bedrock, Node.js |
| `full-stack-chat-agent/` | Full-stack web app with FastAPI backend and Next.js frontend | Neo4j, OpenAI, Node.js |
| `basic_usage.py` | Core memory operations (short-term, long-term, reasoning) | Neo4j, OpenAI API key |
| `entity_resolution.py` | Entity matching strategies | None |
| `langchain_agent.py` | LangChain integration | Neo4j, OpenAI, langchain extra |
| `pydantic_ai_agent.py` | Pydantic AI integration | Neo4j, OpenAI, pydantic-ai extra |
| `domain-schemas/` | GLiNER2 domain schema examples (8 domains) | GLiNER extra, optional Neo4j |

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
| **TypeScript CI** (`ci-typescript.yml`) | Push to `main`, PRs touching `typescript/**` | Lint, vitest (unit + integration), build, packed-artifact check, per-example type-check matrix |
| **TypeScript E2E** (`e2e-typescript.yml`) | Push, PR, nightly | Run TypeScript SDK e2e suite against live NAMS sandbox (uses `MEMORY_API_KEY` secret) |
| **Publish Python** (`publish-python.yml`) | Git tags `python-v*` | Build and publish to PyPI, create GitHub releases |
| **Publish TypeScript** (`publish-typescript.yml`) | Git tags `typescript-v*` | Build and publish to npm with provenance |
| **TypeDoc** (`docs-typedoc.yml`) | Push to `main` (TS docs paths) or `typescript-v*` tags | Build TypeDoc API reference, deploy to GitHub Pages |
| **TCK Conformance** (`tck-conformance.yml`) | Nightly + workflow_dispatch | Run agent-memory-tck Bronze suite against the published `@neo4j-labs/agent-memory` package |
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

This repo ships two independently versioned packages. Each has its own
tag prefix and publish workflow.

### Python (neo4j-agent-memory → PyPI)

1. Update version in `pyproject.toml`
2. Create and push a tag with the **`python-v`** prefix:
   ```bash
   git tag python-v0.4.1
   git push origin python-v0.4.1
   ```
3. `publish-python.yml` builds and publishes to PyPI, then creates a
   GitHub Release.

### TypeScript (@neo4j-labs/agent-memory → npm)

1. Update version in `typescript/package.json`
2. Update `typescript/CHANGELOG.md`
3. Create and push a tag with the **`typescript-v`** prefix:
   ```bash
   git tag typescript-v0.3.0
   git push origin typescript-v0.3.0
   ```
4. `publish-typescript.yml` builds and publishes to npm with provenance,
   then creates a GitHub Release.

> Tag prefixes are enforced by the publish workflows. Plain `v*` tags
> will not trigger a publish.

## TypeScript Contributions

The TypeScript SDK lives at [`typescript/`](typescript/). It is a thin
HTTP client over the NAMS REST API and ships five framework integrations
(Vercel AI SDK, MCP, LangChain JS, Mastra, AWS Strands).

### Setup

Requires Node.js 20+.

```bash
cd typescript
npm ci
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
behavioral spec. The in-tree suite at `typescript/test/tck/` runs in
`ci-typescript.yml` on every PR. A nightly job
(`tck-conformance.yml`) runs the TCK against the **published** npm
package to catch packaging regressions.

To run the bridge server locally for cross-language testing:

```bash
make ts-conformance    # or: cd typescript && npm run conformance:server
# Bridge listens on TCK_BRIDGE_PORT (default 3001)
```

### Code style (TypeScript)

- **Formatter / Linter**: `tsc --noEmit` + `eslint src/` (run via
  `npm run lint`)
- **Tests**: vitest (`test/unit`, `test/integration`, `test/e2e`,
  `test/tck`)
- **Build**: tsup → `dist/` (CJS + ESM + type declarations)
- **Engines**: Node 20+, but written to run on Bun, Deno, Cloudflare
  Workers, and Vercel Edge

### Adding a new framework integration

1. Add `src/integrations/<name>.ts`
2. Wire a subpath export in `typescript/package.json` under `exports`
3. Add a runnable example at `typescript/examples/<name>/`
4. Add a how-to guide at `docs/modules/ROOT/pages/how-to/typescript/<name>.adoc`
5. Add a row to the integrations table in
   `docs/modules/ROOT/pages/sdks/typescript.adoc` and in
   `typescript/README.md`

## Documentation Guidelines (Diataxis Framework)

The documentation follows the [Diataxis framework](https://diataxis.fr/), which organizes content into four distinct types based on user needs:

| Type | Purpose | User Need | Location |
|------|---------|-----------|----------|
| **Tutorials** | Learning-oriented | "I want to learn" | `docs/tutorials/` |
| **How-To Guides** | Task-oriented | "I want to accomplish X" | `docs/how-to/` |
| **Reference** | Information-oriented | "I need to look up Y" | `docs/reference/` |
| **Explanation** | Understanding-oriented | "I want to understand why" | `docs/explanation/` |

### When to Include Documentation in a PR

- **New public API?** --> Update `docs/reference/` with method signatures
- **New user-facing feature?** --> Add how-to guide in `docs/how-to/`
- **Major new capability?** --> Consider adding a tutorial in `docs/tutorials/`
- **Architectural change?** --> Add explanation in `docs/explanation/`
- **Code examples compile?** --> Run `make test-docs-syntax`

### Building and Testing Documentation

```bash
# Build documentation locally
cd docs && npm install && npm run build

# Preview documentation
cd docs && npm run serve

# Run documentation tests
make test-docs           # All doc tests
make test-docs-syntax    # Validate Python code snippets compile
make test-docs-build     # Test build pipeline
make test-docs-links     # Validate internal links
```

### Diataxis Decision Tree

```
Is this about learning a concept from scratch?
  --> Yes: Tutorial (docs/tutorials/)
  --> No:

Is this about accomplishing a specific task?
  --> Yes: How-To Guide (docs/how-to/)
  --> No:

Is this describing what something is or how to use it?
  --> Yes: Reference (docs/reference/)
  --> No:

Is this explaining why something works the way it does?
  --> Yes: Explanation (docs/explanation/)
```
