#!/usr/bin/env bash
# Runs inside the CodeBuild project created by cloudformation/sparky-standalone.yaml (ARM64 build host).
# Builds and pushes the standalone Sparky image (Bedrock AgentCore Runtime needs linux/arm64) and the shopper
# simulator's Lambda image. Every input arrives as an environment variable set on the CodeBuild project.
set -euo pipefail

: "${AWS_REGION:?}" "${ACCOUNT_ID:?}" "${REPOSITORY_URI:?}" "${IMAGE_TAG:?}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

log "Checking CloudWatch Transaction Search"
# Agent spans are stored in the aws/spans log group, which needs Transaction Search (account + region level).
# Only turned on when it is off; never turned off by this stack.
DEST="$(aws xray get-trace-segment-destination --query Destination --output text)"
if [ "$DEST" = "CloudWatchLogs" ]; then
  echo "Transaction Search already enabled"
elif [ "${ENABLE_TRANSACTION_SEARCH:-true}" = "true" ]; then
  aws logs put-resource-policy --policy-name "TransactionSearchXRayAccess" --policy-document "$(cat <<JSON
{"Version": "2012-10-17", "Statement": [{"Sid": "TransactionSearchXRayAccess", "Effect": "Allow",
  "Principal": {"Service": "xray.amazonaws.com"}, "Action": "logs:PutLogEvents",
  "Resource": ["arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:aws/spans:*",
               "arn:aws:logs:${AWS_REGION}:${ACCOUNT_ID}:log-group:/aws/application-signals/data:*"],
  "Condition": {"ArnLike": {"aws:SourceArn": "arn:aws:xray:${AWS_REGION}:${ACCOUNT_ID}:*"},
                "StringEquals": {"aws:SourceAccount": "${ACCOUNT_ID}"}}}]}
JSON
)" >/dev/null
  aws xray update-trace-segment-destination --destination CloudWatchLogs >/dev/null
  echo "Transaction Search enabled (spans -> aws/spans)"
else
  echo "WARNING: Transaction Search is off and EnableTransactionSearch=false - agent traces won't be stored"
fi

log "Building and pushing images (${IMAGE_TAG}, $(uname -m))"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "${REPOSITORY_URI%%/*}"
docker build --quiet -f services/sparky-standalone.Dockerfile -t "${REPOSITORY_URI}:${IMAGE_TAG}" .
docker push --quiet "${REPOSITORY_URI}:${IMAGE_TAG}"
docker build --quiet -f services/sparky-shopper.Dockerfile -t "${REPOSITORY_URI}:shopper-${IMAGE_TAG}" .
docker push --quiet "${REPOSITORY_URI}:shopper-${IMAGE_TAG}"

log "Smoke test: the embedded store starts and serves demo data"
docker run -d --name smoke -e OTEL_SDK_DISABLED=true "${REPOSITORY_URI}:${IMAGE_TAG}" >/dev/null
trap 'docker rm -f smoke >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 60); do
  if docker exec smoke python -c "import json,urllib.request as u; r=u.urlopen(u.Request('http://127.0.0.1:8080/invocations', data=b'{\"action\":\"demo_data\"}', headers={'Content-Type':'application/json'}), timeout=5); print(len(json.load(r)['orders']), 'demo orders')" 2>/dev/null; then
    ok=1; break
  fi
  sleep 2
done
[ "${ok:-0}" = 1 ] || { docker logs smoke | tail -50; echo "standalone image failed its smoke test"; exit 1; }

log "Images ready"
