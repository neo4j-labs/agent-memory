.PHONY: help install install-backend install-frontend install-infra run run-backend run-frontend \
	build test test-integration lint lint-backend lint-frontend typecheck synth clean deploy destroy \
	load-data load-data-reset adopt-graph

# Default target
help:
	@echo "Financial Services Advisor - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install all dependencies (backend + frontend)"
	@echo "  make install-backend  Install backend Python dependencies"
	@echo "  make install-frontend Install frontend Node dependencies"
	@echo ""
	@echo "Development:"
	@echo "  make run              Start both backend and frontend (recommended)"
	@echo "  make run-backend      Start FastAPI backend only (port 8000)"
	@echo "  make run-frontend     Start Vite frontend only (port 5173)"
	@echo "  make load-data        Load sample data into Neo4j (idempotent)"
	@echo "  make load-data-reset  Reload from a clean slate (demo labels only)"
	@echo "  make adopt-graph      Adopt the domain graph as long-term memory entities"
	@echo ""
	@echo "Quality:"
	@echo "  make test             Run backend unit tests"
	@echo "  make test-integration Run backend tests against NEO4J_URI"
	@echo "  make lint             Run linters (backend + frontend)"
	@echo "  make typecheck        Run mypy over the backend"
	@echo "  make synth            Synthesize the CDK stacks (no AWS credentials needed)"
	@echo ""
	@echo "Production:"
	@echo "  make build            Build frontend for production"
	@echo "  make deploy           Deploy to AWS using CDK"
	@echo "  make destroy          Remove all AWS resources"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean            Remove generated files and dependencies"
	@echo ""
	@echo "Configure Neo4j connection in backend/.env (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)"

# Install all dependencies
install: install-backend install-frontend install-infra

install-backend:
	cd backend && uv sync --extra dev

install-frontend:
	cd frontend && npm ci

install-infra:
	cd infrastructure && npm ci

# Run both backend and frontend concurrently
run:
	@echo "Starting Financial Services Advisor..."
	@echo "  Backend:  http://localhost:8000"
	@echo "  Frontend: http://localhost:5173"
	@echo "  API Docs: http://localhost:8000/docs"
	@echo ""
	@echo "Press Ctrl+C to stop both servers."
	@echo ""
	@trap 'kill 0' INT; \
	(cd backend && uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000) & \
	(cd frontend && npm run dev) & \
	wait

# Run backend only
run-backend:
	cd backend && uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Run frontend only
run-frontend:
	cd frontend && npm run dev

# Load sample data into Neo4j. MERGE-based, so re-running is a no-op.
load-data:
	cd backend && uv run python ../../data/load_sample_data.py

# Clear the demo labels first. Agent memory (:Conversation/:Message/:Entity/
# :ReasoningTrace) is never touched.
load-data-reset:
	cd backend && uv run python ../../data/load_sample_data.py --reset

# Attach the library's :Entity super-label to the compliance graph, so the
# domain nodes *are* long-term memory entities.
adopt-graph:
	cd backend && uv run python ../../data/load_sample_data.py --adopt-only

# Build frontend for production
build:
	cd frontend && npm run build

# Testing
test:
	cd backend && uv run pytest tests/ -v

# Needs NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD pointing at a throwaway database.
test-integration:
	cd backend && uv run pytest tests/ -v -m integration

typecheck:
	cd backend && uv run mypy src

# Linting
lint: lint-backend lint-frontend

lint-backend:
	cd backend && uv run ruff check src/

lint-frontend:
	cd frontend && npm run lint

# Synthesize the CloudFormation templates. Needs no AWS credentials, which
# makes it the cheapest check that the infrastructure still compiles.
synth:
	cd infrastructure && npm ci && npm run synth

# Deploy to AWS. Calls the package script: `npm run cdk deploy --all` swallows
# `--all` as an npm flag and deploys only the first stack.
deploy: build
	cd infrastructure && npm ci && npm run deploy

# Destroy AWS resources
destroy:
	cd infrastructure && npm run destroy

# Clean up
clean:
	rm -rf backend/.venv
	rm -rf frontend/node_modules
	rm -rf frontend/dist
	rm -rf infrastructure/node_modules
	rm -rf infrastructure/cdk.out
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
