#!/usr/bin/env bash
set -euo pipefail

# B.LEAGUE streams service — deploy to Google Cloud Run Japan
# No env vars or secrets needed. All credentials are hardcoded in source.
# Deployed to the same project as nbl1-streams: virtual-machines-437208

PROJECT="virtual-machines-437208"
REGION="asia-northeast1"
SERVICE="bleague-streams"

gcloud config set project "$PROJECT"

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 1 \
  --timeout 120
