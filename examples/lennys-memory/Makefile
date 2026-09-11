.PHONY: help install load-sample load-full load-fast load-resume load-dry-run run-backend run-frontend run neo4j clean geocode-locations extract-entities backfill-message-embeddings repair-links enrich enrich-status backfill-relationships backfill-relationships-status backfill-embeddings backfill-embeddings-status mcp-server test

# Default target
help:
	@echo "Lenny's Memory - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install        Install all dependencies (backend + frontend)"
	@echo "  make neo4j          Start Neo4j in Docker"
	@echo ""
	@echo "Data Loading:"
	@echo "  make load-sample    Load 5 transcripts (falls back to data/samples/)"
	@echo "  make load-full      Load every transcript in data/ (see data/README.md)"
	@echo "  make load-fast      Load all transcripts without entity extraction (faster)"
	@echo "  make load-resume    Resume loading (skip already loaded transcripts)"
	@echo "  make load-dry-run   Preview what would be loaded without loading"
	@echo ""
	@echo "Post-Processing:"
	@echo "  make extract-entities           Extract entities from loaded sessions"
	@echo "  make backfill-message-embeddings Embed messages loaded with --no-embeddings"
	@echo "  make repair-links               Rebuild FIRST_MESSAGE/NEXT_MESSAGE chains"
	@echo "  make backfill-relationships     Extract relationships between existing entities"
	@echo "  make backfill-relationships-status  Show relationship extraction status"
	@echo "  make backfill-embeddings        Generate embeddings for entities (for vector search)"
	@echo "  make backfill-embeddings-status Show embedding generation status"
	@echo "  make geocode-locations          Geocode Location entities (add lat/lon coordinates)"
	@echo "  make enrich                     Enrich entities with Wikipedia data"
	@echo "  make enrich-status              Show enrichment progress/status"
	@echo ""
	@echo "Development:"
	@echo "  make run            Start both backend and frontend (recommended)"
	@echo "  make run-backend    Start FastAPI backend only (port 8000)"
	@echo "  make run-frontend   Start Next.js frontend only (port 3000)"
	@echo "  make mcp-server     Start MCP server for Claude Desktop"
	@echo ""
	@echo "Testing:"
	@echo "  make test           Run the backend unit tests"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean          Remove generated files and dependencies"

# Install all dependencies
install: install-backend install-frontend

install-backend:
	cd backend && uv sync

install-frontend:
	cd frontend && npm install

# Start Neo4j
neo4j:
	docker compose up -d
	@echo "Waiting for Neo4j to start..."
	@sleep 10
	@echo "Neo4j available at http://localhost:7474"

# Quiet the HuggingFace/tokenizers chatter during loading. Deliberately does NOT
# set PYTHONWARNINGS=ignore: that also hid the library's own deprecation notices
# (the scripts scope their warning filters to the noisy modules instead).
LOAD_ENV = HF_HUB_DISABLE_PROGRESS_BARS=1 TOKENIZERS_PARALLELISM=false

# Load a few transcripts for a quick smoke test. With no --data-dir the loader
# uses ../data when it holds transcripts and ../data/samples otherwise, so this
# works on a clean clone (see data/README.md).
load-sample:
	cd backend && $(LOAD_ENV) uv run python ../scripts/load_transcripts.py \
		--sample 5

# Load all transcripts (full dataset)
load-full:
	cd backend && $(LOAD_ENV) uv run python ../scripts/load_transcripts.py

# Load all transcripts without entity extraction (faster)
load-fast:
	cd backend && $(LOAD_ENV) uv run python ../scripts/load_transcripts.py \
		--no-entities

# Resume loading (skip already loaded transcripts)
load-resume:
	cd backend && $(LOAD_ENV) uv run python ../scripts/load_transcripts.py \
		--resume

# Preview what would be loaded (dry run)
load-dry-run:
	cd backend && $(LOAD_ENV) uv run python ../scripts/load_transcripts.py \
		--dry-run

# Extract entities from already loaded sessions (post-processing)
extract-entities:
	cd backend && $(LOAD_ENV) uv run python ../scripts/load_transcripts.py \
		--extract-entities-only

# Embed messages that were loaded with --no-embeddings (pairs with load-fast).
# Without this, every vector search in the app returns nothing.
backfill-message-embeddings:
	cd backend && $(LOAD_ENV) uv run python ../scripts/load_transcripts.py \
		--embeddings-only

# Rebuild FIRST_MESSAGE/NEXT_MESSAGE chains for conversations loaded before
# sequential linking existed (short_term.migrate_message_links).
repair-links:
	cd backend && $(LOAD_ENV) uv run python ../scripts/load_transcripts.py \
		--repair-links

# Backfill RELATED_TO relationships between existing entities
# Uses GLiREL for relationship extraction (no LLM required)
backfill-relationships:
	cd backend && $(LOAD_ENV) uv run python ../scripts/backfill_relationships.py

# Show relationship extraction status
backfill-relationships-status:
	cd backend && $(LOAD_ENV) uv run python ../scripts/backfill_relationships.py --status

# Generate embeddings for entities that don't have them
# Required for semantic vector search of entities
backfill-embeddings:
	cd backend && $(LOAD_ENV) uv run python ../scripts/backfill_embeddings.py

# Show embedding generation status
backfill-embeddings-status:
	cd backend && $(LOAD_ENV) uv run python ../scripts/backfill_embeddings.py --status

# Geocode Location entities (add lat/lon coordinates)
# Uses Nominatim (OpenStreetMap) by default - rate limited to 1 req/sec
geocode-locations:
	cd backend && $(LOAD_ENV) uv run python ../scripts/geocode_locations.py

# Enrich entities with Wikipedia data
# Fetches descriptions, images, and links from Wikipedia/Wikimedia
# Rate limited to 2 req/sec to respect Wikimedia ToS
enrich:
	cd backend && $(LOAD_ENV) uv run python ../scripts/enrich_entities.py

# Show enrichment status (how many entities are enriched vs pending)
enrich-status:
	cd backend && $(LOAD_ENV) uv run python ../scripts/enrich_entities.py --status

# Run both backend and frontend concurrently
run:
	@echo "Starting Lenny's Memory..."
	@echo "  Backend:  http://localhost:8000"
	@echo "  Frontend: http://localhost:3000"
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

# Start MCP server for Claude Desktop
mcp-server:
	cd backend && uv run neo4j-agent-memory mcp serve \
		--password $${NEO4J_PASSWORD:-password} \
		--profile extended \
		--session-strategy per_day \
		--user-id lenny-desktop

# Run the backend unit tests (no Neo4j required)
test:
	cd backend && uv run pytest tests -m "not integration" -q --timeout=120

# Clean up
clean:
	rm -rf backend/.venv
	rm -rf frontend/node_modules
	rm -rf frontend/.next
