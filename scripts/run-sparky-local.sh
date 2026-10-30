#!/usr/bin/env bash
# Run Sparky (the assistant agent) locally in the embedded backend — no containers, no EKS, no DynamoDB.
# It starts the real catalog/inventory/cart/checkout/payment/orders services in-process against an
# in-memory DynamoDB (moto), seeded with the catalog and demo orders. Only needs Bedrock access.
#
# Usage:   scripts/run-sparky-local.sh
# Env overrides:
#   AWS_PROFILE_NAME   AWS profile to pull credentials from   (default: amo-log-acct)
#   BEDROCK_REGION     region to call Bedrock in              (default: us-east-1)
#   BEDROCK_MODEL_ID   model / inference profile              (default: global.anthropic.claude-opus-5-5)
#   PORT               loopback port to serve on              (default: 8080)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-amo-log-acct}"
BEDROCK_REGION="${BEDROCK_REGION:-us-east-1}"
BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-global.anthropic.claude-opus-5-5}"
PORT="${PORT:-8080}"

if [ ! -d .venv-sparky ]; then
  echo "venv .venv-sparky not found — creating it and installing dependencies..."
  python3.12 -m venv .venv-sparky
  # shellcheck disable=SC1091
  . .venv-sparky/bin/activate
  pip install -q --upgrade pip
  pip install -q -r services/common/requirements.txt -r services/assistant/requirements-standalone.txt
else
  # shellcheck disable=SC1091
  . .venv-sparky/bin/activate
fi

echo "Exporting short-lived credentials from profile '$AWS_PROFILE_NAME'..."
eval "$(aws configure export-credentials --profile "$AWS_PROFILE_NAME" --format env)"

export SPARKY_BACKEND=embedded
export OTEL_SDK_DISABLED=true                 # no local collector; agent telemetry off for a local run
export AWS_REGION="$BEDROCK_REGION"
export BEDROCK_REGION
export BEDROCK_MODEL_ID
export PYTHONPATH="$REPO_ROOT/services/common"  # so the app and the embedded services find voltcommon

echo "Starting Sparky on http://127.0.0.1:$PORT  (model: $BEDROCK_MODEL_ID, region: $BEDROCK_REGION)"
echo "The embedded store takes a few seconds to seed on first request. Ctrl-C to stop."
cd services/assistant
exec python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --no-access-log
