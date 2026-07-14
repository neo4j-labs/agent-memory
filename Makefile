.PHONY: help install install-all install-dev lint format typecheck ty typecheck-ratchet typecheck-ratchet-update test test-unit test-integration test-integration-mcp test-e2e test-all test-docker test-ci test-no-docker test-quick test-file test-match test-aws test-nams-unit test-nams-integration test-nams-staging test-nams-sandbox test-nams-local test-nams coverage coverage-all coverage-ci coverage-mcp test-examples test-examples-quick test-examples-no-neo4j test-docs test-docs-syntax test-docs-build test-docs-links neo4j-start neo4j-stop neo4j-logs clean build publish docs docs-diagrams-list docs-diagrams-status docs-diagrams-missing docs-diagrams-manifest docs-diagrams-add-refs docs-diagrams-generate example-basic example-resolution example-langchain example-pydantic examples chat-agent-install chat-agent-backend chat-agent-frontend chat-agent ts-install ts-build ts-test ts-test-unit ts-test-integration ts-lint ts-docs ts-conformance ts-pack ts-clean ts-test-examples

# Default target
help:
	@echo "neo4j-agent-memory Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install core dependencies"
	@echo "  make install-all      Install all dependencies including extras"
	@echo "  make install-dev      Install development dependencies"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint             Run linter (ruff check)"
	@echo "  make format           Format code (ruff format)"
	@echo "  make typecheck        Run type checker (mypy)"
	@echo "  make ty               Run the ty type checker (second checker)"
	@echo "  make typecheck-ratchet        Fail if mypy/ty counts exceed the budget"
	@echo "  make typecheck-ratchet-update Refresh the type-check budget after improving"
	@echo "  make check            Run all code quality checks"
	@echo ""
	@echo "Testing:"
	@echo "  make test             Run unit tests"
	@echo "  make test-unit        Run unit tests only"
	@echo "  make test-integration Run integration tests (uses testcontainers)"
	@echo "  make test-integration-mcp  Run MCP integration + E2E tests (uses testcontainers)"
	@echo "  make test-e2e         Run end-to-end MCP flow tests (uses testcontainers)"
	@echo "  make test-all         Run all tests (uses testcontainers)"
	@echo "  make test-docker      Run all tests with docker-compose Neo4j"
	@echo "  make test-ci          Run tests as they would run in CI"
	@echo "  make test-aws         Run AWS integration tests (Bedrock, Strands, AgentCore)"
	@echo "  make coverage         Run unit tests with coverage report"
	@echo "  make coverage-all     Run all tests with coverage (uses testcontainers)"
	@echo "  make coverage-mcp     Run MCP unit tests with coverage report"
	@echo ""
	@echo "Example Testing:"
	@echo "  make test-examples         Run all example smoke tests (uses testcontainers)"
	@echo "  make test-examples-quick   Run quick example validation (no Neo4j needed)"
	@echo "  make test-examples-no-neo4j Run example tests that don't need Neo4j"
	@echo ""
	@echo "Documentation Testing:"
	@echo "  make test-docs             Run all documentation tests (syntax, links, build)"
	@echo "  make test-docs-syntax      Run syntax validation for code snippets (fast)"
	@echo "  make test-docs-build       Run documentation build pipeline tests"
	@echo "  make test-docs-links       Run internal link validation tests"
	@echo ""
	@echo "Examples:"
	@echo "  make example-basic    Run basic usage example"
	@echo "  make example-resolution Run entity resolution example"
	@echo "  make example-langchain Run LangChain integration example"
	@echo "  make example-pydantic Run Pydantic AI integration example"
	@echo "  make examples         Run all examples"
	@echo ""
	@echo "Full-Stack Chat Agent:"
	@echo "  make chat-agent-install  Install chat agent dependencies (backend + frontend)"
	@echo "  make chat-agent-backend  Run chat agent backend server"
	@echo "  make chat-agent-frontend Run chat agent frontend dev server"
	@echo "  make chat-agent          Run both backend and frontend (requires two terminals)"
	@echo ""
	@echo "Neo4j:"
	@echo "  make neo4j-start      Start Neo4j test container"
	@echo "  make neo4j-stop       Stop Neo4j test container"
	@echo "  make neo4j-logs       View Neo4j container logs"
	@echo "  make neo4j-status     Check Neo4j container status"
	@echo "  make neo4j-wait       Wait for Neo4j to be ready"
	@echo "  make neo4j-clean      Stop and remove Neo4j data volumes"
	@echo ""
	@echo "Build & Publish:"
	@echo "  make build            Build package"
	@echo "  make publish          Publish to PyPI (requires credentials)"
	@echo "  make clean            Remove build artifacts"
	@echo ""
	@echo "Documentation:"
	@echo "  make docs-install     Install documentation build dependencies"
	@echo "  make docs             Build documentation to HTML"
	@echo "  make docs-serve       Build and serve with live reload (http://localhost:8080)"
	@echo "  make docs-watch       Watch for changes and rebuild"
	@echo "  make docs-clean       Remove built documentation"
	@echo ""
	@echo "Diagram Management:"
	@echo "  make docs-diagrams-status   Show status of all diagram placeholders"
	@echo "  make docs-diagrams-missing  Show diagrams missing Excalidraw files"
	@echo "  make docs-diagrams-generate Instructions for generating diagrams"
	@echo "  make docs-diagrams-add-refs Add image references to AsciiDoc files"

# =============================================================================
# Setup
# =============================================================================

install:
	uv sync

install-all:
	uv sync --all-extras

install-dev:
	uv sync --extra dev

# =============================================================================
# Code Quality
# =============================================================================

lint:
	uv run ruff check src tests

lint-fix:
	uv run ruff check --fix src tests

format:
	uv run ruff format src tests

format-check:
	uv run ruff format --check src tests

typecheck:
	uv run mypy src benchmarks examples/*.py

# Second, independent type checker (Astral's ty). Non-blocking by itself today;
# the typecheck-ratchet target is what gates regressions in CI.
ty:
	uv run ty check src benchmarks examples/*.py

# Monotonic error ratchet: fails if mypy/ty counts rise above the committed
# budget (scripts/typecheck-budget.txt), or improve without recording it.
# Requires the integration extras: `uv sync --all-extras --group dev`.
typecheck-ratchet:
	uv run python scripts/typecheck_ratchet.py

# Refresh the budget after lowering an error count, then commit the change.
typecheck-ratchet-update:
	uv run python scripts/typecheck_ratchet.py --update

check: lint format-check typecheck
	@echo "All code quality checks passed!"

# =============================================================================
# Testing
# =============================================================================

test: test-unit

test-unit:
	uv run pytest tests/unit -v

# Integration tests using testcontainers (auto-starts Neo4j container)
# Requires Docker to be running
test-integration:
	@echo "Running integration tests with testcontainers..."
	@echo "(Docker must be running - testcontainers will manage the Neo4j container)"
	uv run pytest tests/integration -v --timeout=300

# MCP-specific integration and E2E tests
test-integration-mcp:
	@echo "Running MCP integration and E2E tests with testcontainers..."
	uv run pytest tests/integration/test_mcp_server_integration.py tests/integration/test_mcp_e2e.py tests/integration/test_integration_layer.py -v --timeout=300

# End-to-end MCP flow tests (simulates Claude Desktop usage)
test-e2e:
	@echo "Running end-to-end MCP flow tests with testcontainers..."
	uv run pytest tests/integration/test_mcp_e2e.py -v --timeout=300

# NAMS unit tests (respx-based, no Docker required) — v0.4
test-nams-unit:
	uv run pytest tests/unit/nams -v

# NAMS integration tests (TCK conformance suite). Skipped when the
# TCK reference impl is not reachable; see tests/integration/nams/README.md.
test-nams-integration:
	@echo "Running NAMS integration tests..."
	uv run pytest tests/integration/nams -v --timeout=300

# NAMS integration against a named environment preset (see conftest
# _NAMS_ENV_PRESETS). A raw NAMS_SANDBOX_URL still overrides if exported.
test-nams-staging:
	NAMS_ENV=staging $(MAKE) test-nams-integration

test-nams-sandbox:
	NAMS_ENV=sandbox $(MAKE) test-nams-integration

test-nams-local:
	NAMS_ENV=local $(MAKE) test-nams-integration

# All NAMS tests (unit + integration)
test-nams: test-nams-unit test-nams-integration

# Run all tests using testcontainers
test-all:
	@echo "Running all tests (unit + integration + examples) with testcontainers..."
	@echo "(Docker must be running - testcontainers will manage the Neo4j container)"
	uv run pytest tests -v --timeout=300

# Run all tests with explicit docker-compose Neo4j (useful if testcontainers has issues)
test-docker: neo4j-start neo4j-wait
	NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=test-password \
		uv run pytest tests -v --timeout=300

# Run tests as they would run in CI (with environment variables)
test-ci:
	@echo "Running tests in CI mode with docker-compose Neo4j..."
	@docker compose -f docker-compose.test.yml up -d
	@$(MAKE) neo4j-wait-quiet
	NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=test-password \
		uv run pytest tests -v --timeout=300
	@docker compose -f docker-compose.test.yml down

# Run tests without integration tests (useful when Docker is not available)
test-no-docker:
	SKIP_INTEGRATION_TESTS=1 uv run pytest tests -v

# Quick test run - unit tests only, fast feedback
test-quick:
	uv run pytest tests/unit -v -x --tb=short

# Run a specific test file or pattern
# Usage: make test-file FILE=tests/integration/test_episodic_memory.py
test-file:
	uv run pytest $(FILE) -v --timeout=300

# Run tests matching a pattern
# Usage: make test-match PATTERN="test_add_message"
test-match:
	uv run pytest tests -v -k "$(PATTERN)" --timeout=300

# Run AWS integration tests (Bedrock, Strands, AgentCore)
# Includes unit tests for AWS modules + integration tests marked with @pytest.mark.aws
test-aws:
	@echo "Running AWS tests (unit + integration)..."
	uv run pytest tests/unit/embeddings/test_bedrock.py tests/unit/integrations/test_strands.py tests/unit/integrations/test_agentcore.py tests/unit/integrations/test_hybrid.py tests/integration/test_aws_integration.py -v --timeout=300

coverage:
	uv run pytest tests/unit --cov=src/neo4j_agent_memory --cov-report=term-missing --cov-report=html

# MCP module coverage (unit tests only, fast)
coverage-mcp:
	uv run pytest tests/unit/mcp tests/unit/test_integration.py --cov=src/neo4j_agent_memory/mcp --cov=src/neo4j_agent_memory/integration.py --cov-report=term-missing --cov-report=html

coverage-all:
	@echo "Running all tests with coverage using testcontainers..."
	uv run pytest tests --cov=src/neo4j_agent_memory --cov-report=term-missing --cov-report=html --timeout=300

# Coverage report for CI (with docker-compose Neo4j)
coverage-ci:
	@docker compose -f docker-compose.test.yml up -d
	@$(MAKE) neo4j-wait-quiet
	NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=test-password \
		uv run pytest tests --cov=src/neo4j_agent_memory --cov-report=term-missing --cov-report=xml --timeout=300
	@docker compose -f docker-compose.test.yml down

# =============================================================================
# Example Testing
# =============================================================================

# Run all example smoke tests (uses testcontainers for Neo4j)
# This validates that all examples work correctly with the current package
test-examples:
	@echo "Running example smoke tests with testcontainers..."
	@echo "(Docker must be running - testcontainers will manage the Neo4j container)"
	uv run pytest tests/examples -v --timeout=120

# Run quick example validation tests (structure checks, imports, no Neo4j needed)
test-examples-quick:
	@echo "Running quick example validation tests..."
	uv run pytest tests/examples -v -m "not requires_neo4j and not slow" --timeout=30

# Run example tests that don't require Neo4j
test-examples-no-neo4j:
	@echo "Running example tests that don't need Neo4j..."
	uv run pytest tests/examples/test_entity_resolution.py tests/examples/test_full_stack_apps.py -v --timeout=60

# Run example tests with docker-compose Neo4j (alternative to testcontainers)
test-examples-docker: neo4j-start neo4j-wait
	NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=test-password \
		uv run pytest tests/examples -v --timeout=120

# Run example tests in CI mode
test-examples-ci:
	@echo "Running example tests in CI mode..."
	@docker compose -f docker-compose.test.yml up -d
	@$(MAKE) neo4j-wait-quiet
	NEO4J_URI=bolt://localhost:7687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=test-password \
		uv run pytest tests/examples -v --timeout=120
	@docker compose -f docker-compose.test.yml down

# =============================================================================
# Documentation Testing
# =============================================================================

# Run all documentation tests (syntax validation, link checking, build tests)
# Does not include integration tests that require Neo4j
test-docs:
	@echo "Running all documentation tests..."
	uv run pytest tests/docs -v \
		--ignore=tests/docs/test_tutorial_examples.py \
		--ignore=tests/docs/test_howto_examples.py \
		--timeout=120

# Run only syntax validation tests (fast, no external dependencies)
test-docs-syntax:
	@echo "Running documentation syntax validation..."
	uv run pytest tests/docs/test_code_snippets.py -v -m "syntax or docs" --timeout=60

# Run documentation build pipeline tests (requires npm/node)
test-docs-build:
	@echo "Running documentation build tests..."
	uv run pytest tests/docs/test_build_pipeline.py -v --timeout=180

# Run internal link validation tests
test-docs-links:
	@echo "Running documentation link validation..."
	uv run pytest tests/docs/test_links.py -v --timeout=60

# Run documentation integration tests (requires Neo4j)
test-docs-integration:
	@echo "Running documentation integration tests with testcontainers..."
	uv run pytest tests/docs/test_tutorial_examples.py tests/docs/test_howto_examples.py -v --timeout=300

# =============================================================================
# Neo4j Docker Management
# =============================================================================

NEO4J_COMPOSE := docker compose -f docker-compose.test.yml

neo4j-start:
	$(NEO4J_COMPOSE) up -d
	@echo "Neo4j starting... use 'make neo4j-wait' to wait for it to be ready"

neo4j-stop:
	$(NEO4J_COMPOSE) down

neo4j-restart: neo4j-stop neo4j-start neo4j-wait

neo4j-logs:
	$(NEO4J_COMPOSE) logs -f

neo4j-status:
	@$(NEO4J_COMPOSE) ps

neo4j-wait:
	@echo "Waiting for Neo4j to be ready..."
	@$(NEO4J_COMPOSE) up -d
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do \
		if $(NEO4J_COMPOSE) exec -T neo4j cypher-shell -u neo4j -p test-password "RETURN 1" > /dev/null 2>&1; then \
			echo "Neo4j is ready!"; \
			exit 0; \
		fi; \
		echo "Waiting for Neo4j... ($$i/30)"; \
		sleep 2; \
	done; \
	echo "Neo4j failed to start within 60 seconds"; \
	exit 1

# Quiet version for internal use
neo4j-wait-quiet:
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do \
		if $(NEO4J_COMPOSE) exec -T neo4j cypher-shell -u neo4j -p test-password "RETURN 1" > /dev/null 2>&1; then \
			exit 0; \
		fi; \
		sleep 2; \
	done; \
	echo "Neo4j failed to start"; \
	exit 1

neo4j-clean:
	$(NEO4J_COMPOSE) down -v
	@echo "Neo4j container and volumes removed"

neo4j-shell:
	$(NEO4J_COMPOSE) exec neo4j cypher-shell -u neo4j -p test-password

# =============================================================================
# Build & Publish
# =============================================================================

clean:
	rm -rf dist/
	rm -rf build/
	rm -rf *.egg-info/
	rm -rf src/*.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

build: clean
	uv build

publish: build
	uv publish

publish-test: build
	uv publish --repository testpypi

# =============================================================================
# Documentation
# =============================================================================

# Install docs dependencies
docs-install:
	@echo "Installing documentation dependencies..."
	cd docs && npm install

# Build documentation to HTML
docs:
	@echo "Building documentation..."
	cd docs && npm run build
	@echo ""
	@echo "Documentation built to docs/_site/"
	@echo "Open docs/_site/index.html in your browser"

# Build and serve with live reload
docs-serve:
	@echo "Starting documentation server with live reload..."
	cd docs && npm run serve

# Watch for changes and rebuild
docs-watch:
	@echo "Watching for documentation changes..."
	cd docs && npm run watch

# Clean built documentation
docs-clean:
	@echo "Cleaning built documentation..."
	cd docs && npm run clean

# =============================================================================
# Diagram Management
# =============================================================================

# List all diagram placeholders in documentation
docs-diagrams-list:
	@python scripts/manage_diagrams.py list

# Show status of all diagrams (which have Excalidraw files)
docs-diagrams-status:
	@python scripts/manage_diagrams.py status

# Show only diagrams missing Excalidraw files
docs-diagrams-missing:
	@python scripts/manage_diagrams.py missing

# Generate manifest JSON of all diagrams
docs-diagrams-manifest:
	@python scripts/manage_diagrams.py manifest

# Add image references to AsciiDoc files for diagrams that have Excalidraw files
docs-diagrams-add-refs:
	@python scripts/manage_diagrams.py add-refs

# Generate diagrams using Claude with Excalidraw skill
# Usage: make docs-diagrams-generate
# This target outputs instructions for generating missing diagrams
docs-diagrams-generate:
	@echo "Diagram Generation Instructions"
	@echo "================================"
	@echo ""
	@echo "To generate missing Excalidraw diagrams, use Claude with the excalidraw skill:"
	@echo ""
	@echo "1. Run: make docs-diagrams-missing"
	@echo "2. For each missing diagram, ask Claude:"
	@echo "   'Generate an Excalidraw diagram for [TITLE] based on this ASCII art: ...'"
	@echo "3. Save the JSON to: docs/assets/images/diagrams/excalidraw/[slug].excalidraw"
	@echo "4. Run: make docs-diagrams-add-refs"
	@echo ""
	@python scripts/manage_diagrams.py missing --json 2>/dev/null || python scripts/manage_diagrams.py status

# =============================================================================
# Development Shortcuts
# =============================================================================

# Run a quick check before committing
pre-commit: format lint typecheck test-unit
	@echo "Pre-commit checks passed!"

# Full CI simulation (with Neo4j)
ci: check test-all
	@echo "CI simulation passed!"

# CI without Docker (for environments without Docker)
ci-no-docker: check test-unit
	@echo "CI simulation (no Docker) passed!"

# Interactive Python shell with package loaded
shell:
	uv run python -c "from neo4j_agent_memory import *; import asyncio" -i

# Watch tests (requires pytest-watch)
watch:
	uv run pytest-watch tests/unit

# Quick iteration: format, lint, and run unit tests
dev: format lint test-unit

# =============================================================================
# Examples
# =============================================================================

# Check if NEO4J_URI is set in environment or examples/.env
# If not set, we'll start Docker; otherwise use the configured Neo4j
define check_neo4j_env
	@if [ -f examples/.env ]; then \
		. examples/.env 2>/dev/null; \
	fi; \
	if [ -z "$$NEO4J_URI" ]; then \
		echo "NEO4J_URI not set, starting Docker Neo4j..."; \
		$(MAKE) neo4j-start neo4j-wait-quiet; \
		export NEO4J_PASSWORD=test-password; \
	else \
		echo "Using configured Neo4j at $$NEO4J_URI"; \
	fi
endef

# Basic usage example (requires Neo4j and OpenAI API key or sentence-transformers)
example-basic:
	@echo "Running basic usage example..."
	@if [ -f examples/.env ]; then \
		. examples/.env 2>/dev/null || true; \
	fi; \
	if [ -z "$$NEO4J_URI" ]; then \
		echo "NEO4J_URI not set, starting Docker Neo4j..."; \
		$(MAKE) neo4j-start neo4j-wait-quiet; \
		NEO4J_PASSWORD=test-password uv run python examples/basic_usage.py; \
	else \
		echo "Using configured Neo4j at $$NEO4J_URI"; \
		uv run python examples/basic_usage.py; \
	fi

# Entity resolution example (no external dependencies required)
example-resolution:
	@echo "Running entity resolution example..."
	uv run python examples/entity_resolution.py

# LangChain integration example (requires Neo4j and OpenAI API key)
example-langchain:
	@echo "Running LangChain integration example..."
	@if [ -f examples/.env ]; then \
		. examples/.env 2>/dev/null || true; \
	fi; \
	if [ -z "$$NEO4J_URI" ]; then \
		echo "NEO4J_URI not set, starting Docker Neo4j..."; \
		$(MAKE) neo4j-start neo4j-wait-quiet; \
		NEO4J_PASSWORD=test-password uv run python examples/langchain_agent.py; \
	else \
		echo "Using configured Neo4j at $$NEO4J_URI"; \
		uv run python examples/langchain_agent.py; \
	fi

# Pydantic AI integration example (requires Neo4j and OpenAI API key)
example-pydantic:
	@echo "Running Pydantic AI integration example..."
	@if [ -f examples/.env ]; then \
		. examples/.env 2>/dev/null || true; \
	fi; \
	if [ -z "$$NEO4J_URI" ]; then \
		echo "NEO4J_URI not set, starting Docker Neo4j..."; \
		$(MAKE) neo4j-start neo4j-wait-quiet; \
		NEO4J_PASSWORD=test-password uv run python examples/pydantic_ai_agent.py; \
	else \
		echo "Using configured Neo4j at $$NEO4J_URI"; \
		uv run python examples/pydantic_ai_agent.py; \
	fi

# Run all examples
examples: example-resolution example-basic example-langchain example-pydantic
	@echo "All examples completed!"

# =============================================================================
# Full-Stack Chat Agent
# =============================================================================

CHAT_AGENT_DIR := examples/full-stack-chat-agent

# Install dependencies for both backend and frontend
chat-agent-install:
	@echo "Installing chat agent backend dependencies..."
	cd $(CHAT_AGENT_DIR)/backend && uv sync
	@echo "Installing chat agent frontend dependencies..."
	cd $(CHAT_AGENT_DIR)/frontend && npm install
	@echo "Chat agent dependencies installed!"
	@echo ""
	@echo "Next steps:"
	@echo "  1. Copy .env.example to .env in both backend and frontend directories"
	@echo "  2. Add your OPENAI_API_KEY to backend/.env"
	@echo "  3. Start Neo4j: make neo4j-start"
	@echo "  4. Run backend: make chat-agent-backend"
	@echo "  5. Run frontend: make chat-agent-frontend (in another terminal)"

# Run the chat agent backend server
chat-agent-backend:
	@echo "Starting chat agent backend..."
	@if [ -f $(CHAT_AGENT_DIR)/backend/.env ]; then \
		echo "Using $(CHAT_AGENT_DIR)/backend/.env"; \
	else \
		echo "Warning: No .env file found. Copy .env.example to .env and configure it."; \
	fi
	cd $(CHAT_AGENT_DIR)/backend && uv run uvicorn src.main:app --reload --port 8000

# Run the chat agent frontend dev server
chat-agent-frontend:
	@echo "Starting chat agent frontend..."
	cd $(CHAT_AGENT_DIR)/frontend && npm run dev

# Show instructions for running both
chat-agent:
	@echo "Full-Stack Chat Agent"
	@echo "====================="
	@echo ""
	@echo "To run the chat agent, you need two terminal windows:"
	@echo ""
	@echo "Terminal 1 (Backend):"
	@echo "  make chat-agent-backend"
	@echo ""
	@echo "Terminal 2 (Frontend):"
	@echo "  make chat-agent-frontend"
	@echo ""
	@echo "Prerequisites:"
	@echo "  1. Install dependencies: make chat-agent-install"
	@echo "  2. Configure .env files in backend/ and frontend/"
	@echo "  3. Start Neo4j: make neo4j-start (or use your own instance)"
	@echo ""
	@echo "Then open http://localhost:3000 in your browser."

# Start Neo4j and run backend (convenience target)
chat-agent-backend-with-neo4j: neo4j-start neo4j-wait chat-agent-backend

# ============================================================================
# TypeScript SDK (typescript/)
#
# The TypeScript SDK is a sibling package — @neo4j-labs/agent-memory on npm.
# It has its own package.json, vitest config, and dependencies. These targets
# delegate to npm scripts inside typescript/.
# ============================================================================

TS_DIR := typescript

# Install TS dependencies (npm ci, reproducible from package-lock.json)
ts-install:
	cd $(TS_DIR) && npm ci

# Build the TS package (tsup → dist/)
ts-build:
	cd $(TS_DIR) && npm run build

# Run all TS tests (unit + integration)
ts-test:
	cd $(TS_DIR) && npm test

# Run TS unit tests only (no Neo4j or NAMS sandbox required)
ts-test-unit:
	cd $(TS_DIR) && npm run test:unit

# Run TS integration tests
ts-test-integration:
	cd $(TS_DIR) && npm run test:integration

# Lint TS code (tsc --noEmit + eslint)
ts-lint:
	cd $(TS_DIR) && npm run lint

# Build TypeDoc API reference (outputs to typescript/docs-api/)
ts-docs:
	cd $(TS_DIR) && npm run docs:api

# Start the TCK bridge conformance server (default port 3001)
ts-conformance:
	cd $(TS_DIR) && npm run conformance:server

# Inspect what would be published to npm
ts-pack:
	cd $(TS_DIR) && npm pack --dry-run

# Remove TS build artifacts
ts-clean:
	rm -rf $(TS_DIR)/dist $(TS_DIR)/docs-api $(TS_DIR)/.tsbuildinfo

# Type-check every TS example against the freshly built SDK. Catches
# API drift between examples and the SDK without needing API keys.
# Mirrors the ci-typescript.yml type-check-examples matrix.
ts-test-examples:
	cd $(TS_DIR) && npm ci && npm run build
	@for ex in langchain mastra mcp strands vercel-ai; do \
		echo "=== Type-checking $$ex ==="; \
		(cd $(TS_DIR)/examples/$$ex && npm install --no-package-lock && npx tsc --noEmit) || exit 1; \
	done
	@echo "All TS examples type-check cleanly."
