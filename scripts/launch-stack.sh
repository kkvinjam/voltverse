#!/usr/bin/env bash
# Launch (or update) VoltVerse in the account/region of your current AWS credentials.
#
#   scripts/launch-stack.sh --repo https://github.com/you/voltverse.git
#   scripts/launch-stack.sh --s3-bucket my-bucket --s3-key voltverse-source.zip --participant-role arn:aws:iam::123:role/Participant
#   AWS_PROFILE=team-07 AWS_REGION=us-east-1 scripts/launch-stack.sh --repo ...   # repeat per account
#
# Options: --stack-name NAME (default voltverse)  --env NAME (resource prefix, default voltverse)
#          --repo URL  --branch NAME  --s3-bucket B  --s3-key K  --model ID  --participant-role ARN
#          --public-endpoint (expose the EKS API endpoint for kubectl)  --otlp URL  --param Key=Value (repeatable)
#          --template-bucket B (upload the template via S3; defaults to --s3-bucket when given)
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=voltverse
PARAMS=()
TEMPLATE_BUCKET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stack-name) STACK="$2"; shift 2 ;;
    --env) PARAMS+=("EnvironmentName=$2"); shift 2 ;;
    --repo) PARAMS+=("SourceRepoUrl=$2"); shift 2 ;;
    --branch) PARAMS+=("SourceBranch=$2"); shift 2 ;;
    --s3-bucket) PARAMS+=("SourceS3Bucket=$2"); TEMPLATE_BUCKET="${TEMPLATE_BUCKET:-$2}"; shift 2 ;;
    --template-bucket) TEMPLATE_BUCKET="$2"; shift 2 ;;
    --s3-key) PARAMS+=("SourceS3Key=$2"); shift 2 ;;
    --model) PARAMS+=("BedrockModelId=$2"); shift 2 ;;
    --participant-role) PARAMS+=("ParticipantRoleArn=$2"); shift 2 ;;
    --public-endpoint) PARAMS+=("EnablePublicClusterEndpoint=true"); shift ;;
    --otlp) PARAMS+=("ExternalOtlpEndpoint=$2"); shift 2 ;;
    --param) PARAMS+=("$2"); shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option $1"; exit 1 ;;
  esac
done

echo "Account: $(aws sts get-caller-identity --query Account --output text)  Region: $(aws configure get region || echo "${AWS_REGION:-?}")"
echo "Deploying stack ${STACK} (takes ~30 minutes)..."
aws cloudformation deploy \
  --stack-name "$STACK" \
  --template-file cloudformation/voltverse.yaml \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  ${TEMPLATE_BUCKET:+--s3-bucket "$TEMPLATE_BUCKET" --s3-prefix cloudformation} \
  ${PARAMS:+--parameter-overrides "${PARAMS[@]}"}

aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
