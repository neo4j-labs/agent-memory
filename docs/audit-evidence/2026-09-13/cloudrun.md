# Cloud Run provider source-contract review — 13 September 2026

The former `[mcp,google]` installation did not select Google model providers. `EmbeddingConfig` defaults to OpenAI (`src/neo4j_agent_memory/config/settings.py:130`), and default extraction configuration creates an implicit OpenAI LLM. The embedder initializes its API client lazily (`src/neo4j_agent_memory/embeddings/openai.py:54`), while an unavailable extraction stage can be skipped. Therefore a working database connection may allow the listener to start, but listing tools or passing a TCP probe does not establish usable embedding-dependent writes/search. Database secrets alone are insufficient.

## Changes

- `deploy/cloudrun/Dockerfile`: explicitly selects `--backend bolt`, `--embedding vertex_ai/gemini-embedding-001`, `--embedding-dimensions 768`, `--no-auto-preferences`, and `NAM_EXTRACTION__EXTRACTOR_TYPE=none`. No implicit OpenAI key/LLM is required for this defined path. The current native Vertex provider bridge and underlying embedder agree at 768; this avoids claiming arbitrary dimension overrides are forwarded by that bridge.
- `deploy/cloudrun/cloudbuild.yaml`: supplies `GOOGLE_CLOUD_PROJECT=$PROJECT_ID`, fixes repository-root command path, and replaces nonexistent `_NEO4J_*` substitution descriptions with the actual `_REPOSITORY` substitution. Database values are Secret Manager bindings.
- `deploy/cloudrun/service.yaml`: supplies the project placeholder and explicit extraction setting; corrects repository-root apply path.
- `deploy/cloudrun/README.md`: explains runtime service-account ADC, Vertex API/model invocation access, model/location/dimensions and fresh-database compatibility, disabled extraction/preferences, local read-only ADC mounting, and a write/search readback beyond tool registration. The CLI provider path currently uses `us-central1`; changing Cloud Run `_REGION` does not change it.
- `tests/docs/test_cloudrun_assets.py`: three additional source-contract checks, four total including the existing packaging input check. They invoke the actual CLI from the Docker CMD and actual settings/provider factories; only the server listener and remote embedding boundary are mocked. No SDK runtime source was changed.

## Verification

- `/tmp/agent-memory-docs-integrations/bin/python -m pytest tests/docs/test_cloudrun_assets.py -q`: **4 passed**, no warnings. Full final output: `logs/agent-memory-cloudrun-provider-contracts-final.log`.
- Shared base `.venv` also passed the same 4 tests, with one existing Google storage future warning; output: `logs/agent-memory-cloudrun-provider-contracts-base.log`.
- Temporary integration environment initially lacked `fastmcp` entirely (`ModuleNotFoundError`), producing the server's import fallback. Installed the declared FastMCP 4 range only into that temporary environment (resolved 4.0.3), then reran successfully. No test/source workaround or shared environment mutation. Initial failure remains `logs/agent-memory-cloudrun-provider-contracts.log`; install logs remain `logs/agent-memory-cloudrun-fastmcp-install.log` and `logs/agent-memory-cloudrun-fastmcp-install-approved.log`.
- Cloud Build/service YAML parse successfully; all README Bash blocks pass `bash -n`; targeted Ruff check/format and `git diff --check` pass.

## Remaining verification limits

These are source and offline mock contracts, not a complete image or deployment certification. No Docker build/container run, Google Cloud deployment, real database write, provider/model invocation, identity grant or credential file creation occurred. Live Vertex model availability/access, ADC behavior in the chosen project, quotas, database vector compatibility, authenticated MCP write/search round trip, and existing Cloud Run IAM policy must still be verified by the deployment operator. Final combined documentation suite: 605 passed, 143 signature/ellipsis skips; see docs-tests.log. The remediation ledger records the release/service/publication gates.
