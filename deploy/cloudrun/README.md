# Deploy the MCP server on Cloud Run

Run the Python MCP server as an authenticated Cloud Run service connected to Neo4j AuraDB. This is an experimental Neo4j Labs deployment example. Complete local packaging and protocol checks before using a cloud project.

## Prerequisites

- A checkout of this repository, Docker, and Google Cloud CLI.
- A test Google Cloud project with billing and Cloud Run, Cloud Build, Artifact Registry, Secret Manager, and Vertex AI APIs enabled.
- Permission to create the required resources, deploy using the runtime service account, and grant the intended developer/service identity `roles/run.invoker`.
- A dedicated [AuraDB instance](../../examples/AURA_SETUP.md) reachable from Cloud Run and credentials stored in Secret Manager. Use a fresh test database with 768-dimensional vector indexes for the selected embedding configuration.
- Access to `gemini-embedding-001` in Vertex AI location `us-central1`. The runtime service account needs permission to invoke that model; database secrets alone are insufficient.

The image explicitly selects Vertex AI embeddings (`gemini-embedding-001`, 768 dimensions) and the Bolt backend. It disables automatic entity extraction and preference detection, so this path needs no OpenAI key or separate LLM. Message storage, embedding search and explicit graph tools remain available. Installing `[mcp,google]` alone would not select Google providers: the SDK defaults still select OpenAI embeddings, and provider credentials are checked lazily when a tool uses them.

The container uses Streamable HTTP at `/mcp/`. It does not implement application authentication itself; Cloud Run IAM protects the endpoint. The local proxy below supplies the developer's Cloud Run identity. A production MCP client needs its own supported service-to-service authentication path.

## 1. Verify the image locally

Run commands from the repository root:

```bash
docker build -f deploy/cloudrun/Dockerfile -t neo4j-memory-mcp:local .
docker run --rm neo4j-memory-mcp:local neo4j-agent-memory mcp serve --help
```

The Dockerfile must copy `README-pypi.md`, because that is the packaging readme declared by `pyproject.toml`. The context is the repository root, not the `deploy/cloudrun` directory.

For a protocol smoke test, supply the Aura `neo4j+s://` URI, username and password as `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` through a local, uncommitted environment file. Map the Aura setup's `NEO4J_USERNAME` value to the CLI's `NEO4J_USER` key. Docker runs the MCP application here; the database remains in Aura. Configure local Application Default Credentials (ADC) and select the Vertex AI project. The following mount passes the ADC file read-only; the local process uses your UID/GID so it can read the file without widening its permissions:

```bash
export PROJECT_ID=your-test-project
gcloud auth application-default login
export ADC_FILE="$(gcloud info --format='value(config.paths.global_config_dir)')/application_default_credentials.json"
docker run --rm --user "$(id -u):$(id -g)" \
  --env-file /absolute/path/to/test-memory.env \
  -e GOOGLE_CLOUD_PROJECT="$PROJECT_ID" \
  -e GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/google-adc.json \
  --mount "type=bind,src=$ADC_FILE,dst=/run/secrets/google-adc.json,readonly" \
  -p 8080:8080 neo4j-memory-mcp:local
```

In another terminal, use the CLI installed by the selected `[mcp]` dependency set:

```bash
fastmcp list http://127.0.0.1:8080/mcp/ --prompts --resources
```

Expect the extended profile's registered Bolt tools, prompts and resources. Then call `memory_store_message` with a synthetic message and use `memory_search` with that session ID; confirm the returned text and inspect the stored record in the test database. Registration or a TCP listener does not exercise embedding credentials. Automatic extraction/preference detection remains disabled even though generic tool descriptions mention those optional behaviors. Check supported operations, not just registration counts; the NAMS backend has different applicability. Keep the full build and protocol output with deployment evidence. A metadata check alone does not prove the complete image runs.

## 2. Prepare secrets and the runtime identity

Set the intended project and region. The following commands create resources in that project:

```bash
export PROJECT_ID=your-test-project
export REGION=us-central1
gcloud config set project "$PROJECT_ID"
gcloud iam service-accounts create neo4j-memory-sa --project "$PROJECT_ID"
export RUNTIME_SA="neo4j-memory-sa@$PROJECT_ID.iam.gserviceaccount.com"
```

Create `neo4j-uri`, `neo4j-user`, and `neo4j-password` in Secret Manager using the project's normal secret-entry process. Grant the runtime identity access to each secret before deployment:

```bash
for SECRET_NAME in neo4j-uri neo4j-user neo4j-password; do
  gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
    --member="serviceAccount:$RUNTIME_SA" \
    --role=roles/secretmanager.secretAccessor
done
```

Enable Vertex AI and grant the runtime identity `roles/aiplatform.user` (or your project's narrower equivalent that permits the selected model). Google's [model access-control guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/access-control) lists the predefined roles and operation permissions:

```bash
gcloud services enable aiplatform.googleapis.com --project "$PROJECT_ID"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/aiplatform.user
```

Cloud Run supplies ADC through its attached service account; do not mount the developer's local ADC file or bake a service-account key into the deployed image. See Google's [ADC credential lookup guide](https://docs.cloud.google.com/docs/authentication/application-default-credentials) for the local-file and attached-identity paths. `cloudbuild.yaml` supplies `GOOGLE_CLOUD_PROJECT=$PROJECT_ID`; replace that placeholder when applying `service.yaml`. The current CLI provider path uses Vertex AI location `us-central1`, independently of the Cloud Run `_REGION` substitution.

Cloud Run reads these secrets using the service identity. See [Configure secrets for services](https://docs.cloud.google.com/run/docs/configuring/services/secrets) for identity and secret-version requirements. The build/deploy principal also needs the project permissions appropriate to your organization's Cloud Build setup; it is distinct from this runtime identity.

## 3. Build and deploy an authenticated test service

Create the image repository, then submit the checked-in build configuration from the repository root:

```bash
gcloud artifacts repositories create neo4j-agent-memory \
  --repository-format=docker --location="$REGION"
gcloud builds submit --config deploy/cloudrun/cloudbuild.yaml \
  --substitutions="_REGION=$REGION" .
```

The configuration tags images with Cloud Build's `BUILD_ID`, uses the `neo4j-memory-sa` identity, enables the invoker IAM check, and specifies `--no-allow-unauthenticated`. `service.yaml` is an alternative declarative template; replace its project/image placeholders and review its resource settings before using it. It does not independently remove an existing public IAM grant.

Grant the intended developer access, substituting their identity:

```bash
gcloud run services add-iam-policy-binding neo4j-memory-mcp \
  --region="$REGION" --member="user:developer@example.com" \
  --role=roles/run.invoker
```

## 4. Verify authenticated MCP invocation

Use Google's local development proxy, which authenticates as the active Cloud CLI account:

```bash
gcloud run services proxy neo4j-memory-mcp \
  --project="$PROJECT_ID" --region="$REGION" --port=8081
```

In another terminal:

```bash
fastmcp list http://127.0.0.1:8081/mcp/ --prompts --resources
```

Then use a synthetic conversation to verify one supported write/read round trip and confirm it reaches the intended database. A public unauthenticated request should be denied. Stop the local proxy when the check finishes. Google's [developer authentication guide](https://docs.cloud.google.com/run/docs/authenticating/developers) describes the proxy and its limitations; use the [service-to-service authentication guide](https://docs.cloud.google.com/run/docs/authenticating/service-to-service) for deployed callers.

This documentation repair verified package metadata, CLI/settings selection and provider construction with offline mocks locally. A complete Docker build and live Cloud Run/proxy round trip remain deployment checks to execute in the selected environment; they are not certified by a local documentation build.

## Existing public services

Omitting an old `--allow-unauthenticated` flag alone does not establish a private service. Review the service's invoker IAM-check setting and current IAM policy. If an existing `allUsers` invoker grant is unintended, remove that specific binding:

```bash
gcloud run services remove-iam-policy-binding neo4j-memory-mcp \
  --region="$REGION" --member=allUsers --role=roles/run.invoker
```

Also review any `allAuthenticatedUsers` grant and recheck anonymous and authorized invocation. See [Cloud Run public access](https://docs.cloud.google.com/run/docs/authenticating/public) and the [remove binding command](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/remove-iam-policy-binding). Treat a live policy change as a separate operational action; these files do not alter an already deployed service.

## Configuration and troubleshooting

| Setting | Source or default |
|---|---|
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Required Secret Manager entries |
| `NEO4J_DATABASE` | `neo4j`, unless configured otherwise |
| `GOOGLE_CLOUD_PROJECT` | Deployment project, supplied by Cloud Build or replaced in the service template |
| Vertex AI identity | Attached runtime service account with model-invocation permission; local smoke test uses mounted ADC |
| Embedding model / location / dimensions | Image selects `vertex_ai/gemini-embedding-001`, `us-central1`, 768 |
| Automatic extraction / preferences | `NAM_EXTRACTION__EXTRACTOR_TYPE=none` and `--no-auto-preferences`; no LLM configured |
| Listener | `0.0.0.0:8080`, Streamable HTTP `/mcp/` |
| Memory / CPU | 1 GiB / 1 CPU in the example deployment |
| Instances | 0–10; size and concurrency require workload testing |

For secret denial, check all three secret grants against the configured runtime identity. For database failures, verify TLS, network reachability and credentials. For embedding failures, check ADC, the configured project, Vertex AI API enablement, model access and quotas in `us-central1`. Matching dimensions alone does not make an existing model's vectors compatible; use a fresh database or perform the documented embedding migration. To enable extraction later, configure an explicit LLM/provider and its dependencies/credentials as a separate change. For startup, inspect container logs; the service template uses a TCP startup probe because `/mcp/` is a protocol endpoint, not a health page. Old `/sse` or `/messages` client URLs must be changed to `/mcp/`.

Cloud identity and command references were checked against Google's documentation on 13 September 2026. Record the source commit, image digest, package dependencies, service revision, successful protocol output and cleanup with each actual deployment verification.
