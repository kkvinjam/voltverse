#!/usr/bin/env bash
# Zip the committed repository so the CloudFormation stack can deploy from S3 instead of Git
# (useful for private repos and Workshop Studio assets).
#
#   scripts/package-source.sh                      # writes dist/voltverse-source.zip
#   scripts/package-source.sh my-bucket [prefix/]  # ...and uploads it to s3://my-bucket/prefix/voltverse-source.zip
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist
git archive --format=zip --output dist/voltverse-source.zip HEAD
echo "Created dist/voltverse-source.zip from commit $(git rev-parse --short HEAD)"
if [ -n "${1:-}" ]; then
  key="${2:-}voltverse-source.zip"
  aws s3 cp dist/voltverse-source.zip "s3://$1/${key}"
  echo "Uploaded to s3://$1/${key}  ->  SourceS3Bucket=$1 SourceS3Key=${key}"
fi
