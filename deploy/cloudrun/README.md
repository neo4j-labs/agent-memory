# Cloud Run Deployment for Neo4j Agent Memory MCP Server

Deploy the Neo4j Agent Memory MCP Server to Google Cloud Run for production use.

## Prerequisites

1. **Google Cloud Project** with billing enabled
2. **APIs enabled**:
   - Cloud Run API
   - Cloud Build API
   - Artifact Registry API
   - Secret Manager API
3. **Neo4j database** accessible from Cloud Run (e.g., Neo4j Aura)

## Quick Start

### 1. Set up secrets

Store your Neo4j credentials in Secret Manager:

```bash
# Set your project
export PROJECT_ID=your-project-id
gcloud config set project $PROJECT_ID

# Create secrets
echo -n "bolt+s://your-neo4j-host:7687" | gcloud secrets create neo4j-uri --data-file=-
echo -n "neo4j" | gcloud secrets create neo4j-user --data-file=-
echo -n "your-password" | gcloud secrets create neo4j-password --data-file=-
```

### 2. Create Artifact Registry repository

```bash
gcloud artifacts repositories create neo4j-agent-memory \
    --repository-format=docker \
    --location=us-central1 \
    --description="Neo4j Agent Memory images"
```

### 3. Deploy using Cloud Build

```bash
cd neo4j-agent-memory
gcloud builds submit --config deploy/cloudrun/cloudbuild.yaml .
```

### 4. Get the service URL

```bash
gcloud run services describe neo4j-memory-mcp \
    --region=us-central1 \
    --format='value(status.url)'
```

The MCP endpoint is `/mcp/` on that URL, e.g.
`https://neo4j-memory-mcp-xxxx.run.app/mcp/`. Point your MCP client at that
full path, not the bare service URL.

### 5. Verify with an MCP client

The `[mcp]` extra installs FastMCP 4, whose CLI can list the server's tools:

```bash
pip install "neo4j-agent-memory[mcp]"
fastmcp list https://neo4j-memory-mcp-xxxx.run.app/mcp/ --prompts --resources
```

Expect the 16 extended-profile tools, three prompts, and the memory resources.

## Transport

The container runs the server on **Streamable HTTP**
(`mcp serve --transport http`), the network transport in the current MCP
specification.

Earlier versions of this image used `--transport sse`, which served the legacy
HTTP+SSE transport at `/sse` + `/messages`. That transport is deprecated in the
MCP spec and in FastMCP 4, which this image now builds on. If you are upgrading,
change client URLs from `/sse` to `/mcp/`. The `--transport sse` flag still
starts a server, but it logs a deprecation warning and serves Streamable HTTP —
so a stale client URL will fail rather than silently fall back.

## Manual Deployment

If you prefer to deploy manually:

### Build and push the image

```bash
# Configure Docker for Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev

# Build
docker build -t us-central1-docker.pkg.dev/$PROJECT_ID/neo4j-agent-memory/neo4j-memory-mcp:latest \
    -f deploy/cloudrun/Dockerfile .

# Push
docker push us-central1-docker.pkg.dev/$PROJECT_ID/neo4j-agent-memory/neo4j-memory-mcp:latest
```

### Deploy to Cloud Run

```bash
gcloud run deploy neo4j-memory-mcp \
    --image=us-central1-docker.pkg.dev/$PROJECT_ID/neo4j-agent-memory/neo4j-memory-mcp:latest \
    --region=us-central1 \
    --platform=managed \
    --allow-unauthenticated \
    --memory=1Gi \
    --cpu=1 \
    --min-instances=0 \
    --max-instances=10 \
    --set-secrets="NEO4J_URI=neo4j-uri:latest,NEO4J_USER=neo4j-user:latest,NEO4J_PASSWORD=neo4j-password:latest"
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NEO4J_URI` | Neo4j connection URI | Required |
| `NEO4J_USER` | Neo4j username | Required |
| `NEO4J_PASSWORD` | Neo4j password | Required |
| `NEO4J_DATABASE` | Neo4j database name | `neo4j` |
| `PORT` | Server port | `8080` |

### Resource Limits

Default configuration:
- Memory: 1Gi
- CPU: 1
- Min instances: 0 (scale to zero)
- Max instances: 10
- Concurrency: 80 requests per instance

Adjust in `service.yaml` or via `gcloud run deploy` flags.

## Security

### Authentication

By default, the service allows unauthenticated access. For production:

1. **Remove `--allow-unauthenticated`** from deploy command
2. **Configure IAM** for authorized users/services:
   ```bash
   gcloud run services add-iam-policy-binding neo4j-memory-mcp \
       --region=us-central1 \
       --member="user:email@example.com" \
       --role="roles/run.invoker"
   ```

### Network Security

For private Neo4j databases:
1. Use **VPC Connector** to access private networks
2. Configure **Cloud NAT** for outbound connections
3. Use **Private Google Access** for internal services

## Monitoring

### Logs

```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=neo4j-memory-mcp" \
    --limit=100
```

### Metrics

View in Cloud Console:
- **Cloud Run > neo4j-memory-mcp > Metrics**
- Request count, latency, error rate
- Container instance count

## Troubleshooting

### Common Issues

1. **Connection refused to Neo4j**
   - Ensure Neo4j allows connections from Cloud Run IP ranges
   - Check Neo4j is using `bolt+s://` for TLS connections

2. **Secret access denied**
   - Grant Secret Manager access to the Cloud Run service account:
     ```bash
     gcloud secrets add-iam-policy-binding neo4j-uri \
         --member="serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
         --role="roles/secretmanager.secretAccessor"
     ```

3. **Out of memory**
   - Increase memory limit in deployment
   - Check for memory leaks in logs

## Cost Optimization

- **Scale to zero**: Min instances = 0 means no charges when idle
- **CPU throttling**: Enabled by default, reduces costs during idle
- **Right-size**: Start with 1Gi memory, increase only if needed
