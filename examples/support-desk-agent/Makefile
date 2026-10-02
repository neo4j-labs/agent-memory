.PHONY: help install install-backend install-frontend neo4j neo4j-stop seed reseed run-backend run-frontend test

help:
	@echo "Support-desk agent - development commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install         Install backend (uv) and frontend (npm) dependencies"
	@echo "  make neo4j           Start Neo4j 5.26 in Docker (ports 7474 / 7687)"
	@echo "  make seed            Import the ontology, activate revision 1, load the seed conversations"
	@echo "  make reseed          Start over: remove the seed, every chat and the ontology, then seed"
	@echo ""
	@echo "Run (two terminals):"
	@echo "  make run-backend     FastAPI on http://localhost:8000"
	@echo "  make run-frontend    Next.js on http://localhost:3000"
	@echo ""
	@echo "Test / clean up:"
	@echo "  make test            The example's tests (from the repository root)"
	@echo "  make neo4j-stop      Stop the Neo4j container (data volume kept)"

install: install-backend install-frontend

install-backend:
	cd backend && uv sync

install-frontend:
	cd frontend && npm ci

neo4j:
	docker compose up -d --wait
	@echo "Neo4j Browser: http://localhost:7474 (neo4j / test-password)"

neo4j-stop:
	docker compose down

seed:
	cd backend && uv run python -m src.seed

reseed:
	cd backend && uv run python -m src.seed --reset

run-backend:
	cd backend && uv run uvicorn src.main:app --reload --port 8000

run-frontend:
	cd frontend && npm run dev

test:
	cd ../.. && uv run pytest tests/examples/test_support_desk_agent_example.py -q
