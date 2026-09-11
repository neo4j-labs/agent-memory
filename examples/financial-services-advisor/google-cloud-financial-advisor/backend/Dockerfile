# Google Cloud Financial Advisor — backend image (Cloud Run ready).
#
# Why requirements-docker.txt and not `uv sync`:
# `pyproject.toml` carries a `[tool.uv.sources]` editable path pointing at the
# repository root (`../../../..`) for local development. The Docker build
# context is `./backend`, so that path does not exist inside the image and
# `uv sync --frozen` fails. `requirements-docker.txt` is the same locked
# dependency set exported **without** sources, so `neo4j-agent-memory` resolves
# from PyPI. Regenerate it with `make docker-requirements` whenever
# `pyproject.toml` or `uv.lock` changes.

FROM python:3.12-slim

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY requirements-docker.txt ./
RUN uv pip install --system --no-cache -r requirements-docker.txt

COPY src/ src/

# Non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Cloud Run supplies PORT; default to 8080 for local runs.
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health')" || exit 1

CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
