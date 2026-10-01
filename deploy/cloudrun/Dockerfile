# Neo4j Agent Memory MCP Server
# Cloud Run deployment image
#
# Build:
#   docker build -f deploy/cloudrun/Dockerfile -t neo4j-memory-mcp .
#
# Run locally (Neo4j stays in AuraDB):
#   docker run -p 8080:8080 \
#     -e NEO4J_URI=neo4j+s://<instance-id>.databases.neo4j.io \
#     -e NEO4J_USER=neo4j \
#     -e NEO4J_PASSWORD=your-aura-password \
#     -e GOOGLE_CLOUD_PROJECT=your-test-project \
#     neo4j-memory-mcp
#   Local embedding calls also require readable Application Default Credentials;
#   see deploy/cloudrun/README.md for the env file and read-only credential mount.

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY pyproject.toml README.md README-pypi.md ./
COPY src/ ./src/

# Install the package with MCP and Google dependencies
RUN pip install --no-cache-dir -e ".[mcp,google]"

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8080

# Set environment variables
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
# This example uses Vertex AI embeddings via Application Default Credentials.
# Disable the default extraction pipeline so no implicit OpenAI LLM is needed.
ENV NAM_EXTRACTION__EXTRACTOR_TYPE=none

# Run the MCP server via the installed CLI entry point.
#
# Transport is Streamable HTTP ("http"), the network transport in the current MCP
# spec and the only one FastMCP 4 serves besides stdio. The MCP endpoint is
# POST/GET /mcp, with no trailing slash (/mcp/ answers with a redirect to an
# http:// URL) — not the /sse + /messages pair the retired HTTP+SSE transport
# used. Update client URLs if you are migrating from an older image.
CMD ["neo4j-agent-memory", "mcp", "serve", \
     "--transport", "http", "--host", "0.0.0.0", "--port", "8080", \
     "--backend", "bolt", \
     "--embedding", "vertex_ai/gemini-embedding-001", \
     "--embedding-dimensions", "768", "--no-auto-preferences"]
