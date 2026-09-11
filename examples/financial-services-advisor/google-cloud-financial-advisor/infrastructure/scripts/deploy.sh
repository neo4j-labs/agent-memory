#!/bin/bash
# Deploy Google Cloud Financial Advisor to Cloud Run
# Usage: ./deploy.sh [PROJECT_ID] [REGION]

set -e

PROJECT_ID="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
REPO_NAME="financial-advisor"

echo "Deploying Financial Advisor to Google Cloud"
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo ""

# Check if required APIs are enabled
echo "Checking required APIs..."
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    secretmanager.googleapis.com \
    --project="$PROJECT_ID"

# Create Artifact Registry repository if it doesn't exist
echo "Setting up Artifact Registry..."
gcloud artifacts repositories describe "$REPO_NAME" \
    --location="$REGION" \
    --project="$PROJECT_ID" 2>/dev/null || \
gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --project="$PROJECT_ID" \
    --description="Financial Advisor container images"

# Check if secrets exist
echo "Checking secrets..."
if ! gcloud secrets describe neo4j-uri --project="$PROJECT_ID" &>/dev/null; then
    echo "ERROR: Secret 'neo4j-uri' not found."
    echo "Please run ./setup-secrets.sh first to configure Neo4j credentials."
    exit 1
fi

if ! gcloud secrets describe neo4j-password --project="$PROJECT_ID" &>/dev/null; then
    echo "ERROR: Secret 'neo4j-password' not found."
    echo "Please run ./setup-secrets.sh first to configure Neo4j credentials."
    exit 1
fi

# Submit Cloud Build from the repository root: cloudbuild.yaml's build steps
# use `dir: examples/financial-services-advisor/google-cloud-financial-advisor`,
# which is relative to the uploaded source root.
APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
REPO_ROOT="$(cd "$APP_DIR/../../.." && pwd)"

echo ""
echo "Starting Cloud Build from $REPO_ROOT ..."
cd "$REPO_ROOT"

gcloud builds submit \
    --config="examples/financial-services-advisor/google-cloud-financial-advisor/infrastructure/cloudbuild.yaml" \
    --substitutions="_REGION=$REGION,_REPO_NAME=$REPO_NAME" \
    --project="$PROJECT_ID" \
    .

# Get deployed URLs
echo ""
echo "Deployment complete!"
echo ""
echo "Service URLs:"
BACKEND_URL=$(gcloud run services describe financial-advisor-backend \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --format='value(status.url)' 2>/dev/null || echo "Not deployed")
FRONTEND_URL=$(gcloud run services describe financial-advisor-frontend \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --format='value(status.url)' 2>/dev/null || echo "Not deployed")

echo "  Backend:  $BACKEND_URL"
echo "  Frontend: $FRONTEND_URL"
echo ""
echo "API Docs:   $BACKEND_URL/docs"
