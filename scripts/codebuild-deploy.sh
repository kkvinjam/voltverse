#!/usr/bin/env bash
# Runs inside the CodeBuild project created by cloudformation/voltverse.yaml.
# Builds all container images, pushes them to ECR, seeds DynamoDB and applies the Kubernetes manifests.
# Every input arrives as an environment variable set on the CodeBuild project.
set -euo pipefail

: "${AWS_REGION:?}" "${ACCOUNT_ID:?}" "${CLUSTER_NAME:?}" "${REPO_PREFIX:?}" "${TARGET_GROUP_ARN:?}"
: "${PRODUCTS_TABLE:?}" "${INVENTORY_TABLE:?}" "${CARTS_TABLE:?}" "${ORDERS_TABLE:?}" "${PAYMENTS_TABLE:?}"
: "${BEDROCK_MODEL_ID:?}" "${APP_LOG_GROUP:?}" "${METRICS_LOG_GROUP:?}" "${ENVIRONMENT_NAME:?}"
: "${VPC_ID:?}" "${ALB_SECURITY_GROUP_ID:?}"

export ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
export IMAGE_TAG="${IMAGE_TAG:-b${CODEBUILD_BUILD_NUMBER:-$(date +%s)}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

log "Allowing the CloudFront VPC origin to reach the internal ALB"
# CloudFront creates this security group in our VPC when the VPC origin is created.
CF_SG="$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=${VPC_ID}" \
  "Name=group-name,Values=CloudFront-VPCOrigins-Service-SG" --query 'SecurityGroups[0].GroupId' --output text)"
[ -n "$CF_SG" ] && [ "$CF_SG" != "None" ] || { echo "CloudFront-VPCOrigins-Service-SG not found in ${VPC_ID}"; exit 1; }
if ! out="$(aws ec2 authorize-security-group-ingress --group-id "$ALB_SECURITY_GROUP_ID" --ip-permissions \
    "IpProtocol=tcp,FromPort=80,ToPort=80,UserIdGroupPairs=[{GroupId=${CF_SG},Description=CloudFront VPC origin}]" 2>&1)"; then
  grep -q InvalidPermission.Duplicate <<<"$out" || { echo "$out"; exit 1; }
fi
echo "ALB ${ALB_SECURITY_GROUP_ID} accepts tcp/80 from ${CF_SG}"

log "Installing kubectl matching the cluster version"
K8S_MINOR="$(aws eks describe-cluster --name "$CLUSTER_NAME" --query cluster.version --output text)"
KUBECTL_VERSION="$(curl -fsSL "https://dl.k8s.io/release/stable-${K8S_MINOR}.txt")"
curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
chmod +x /usr/local/bin/kubectl
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION"
kubectl version

log "Building and pushing images (tag ${IMAGE_TAG})"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
build_push() {
  local name="$1"; shift
  local image="${ECR_REGISTRY}/${REPO_PREFIX}/${name}:${IMAGE_TAG}"
  docker build --quiet -t "$image" "$@"
  docker push --quiet "$image"
}
for svc in catalog inventory cart checkout payment orders assistant; do
  build_push "$svc" -f services/python.Dockerfile --build-arg "SERVICE=${svc}" services
done
build_push storefront services/storefront

log "Seeding DynamoDB"
python3 -c "import boto3" 2>/dev/null || python3 -m pip install --quiet boto3
python3 scripts/seed_data.py --products-table "$PRODUCTS_TABLE" --inventory-table "$INVENTORY_TABLE" --region "$AWS_REGION"

log "Applying Kubernetes manifests"
python3 scripts/render.py k8s/*.yaml > /tmp/voltverse.yaml
kubectl apply -f /tmp/voltverse.yaml

log "Waiting for rollouts"
kubectl -n observability rollout status deploy/otel-collector --timeout=15m
for d in catalog inventory cart checkout payment orders assistant storefront; do
  kubectl -n voltverse rollout status "deploy/${d}" --timeout=15m
done

log "Waiting for the storefront to become healthy behind the load balancer"
for _ in $(seq 1 60); do
  healthy="$(aws elbv2 describe-target-health --target-group-arn "$TARGET_GROUP_ARN" \
    --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text)"
  if [ "${healthy:-0}" -ge 1 ]; then echo "Healthy targets: ${healthy}"; break; fi
  sleep 10
done
[ "${healthy:-0}" -ge 1 ] || { echo "No healthy targets after 10 minutes"; kubectl -n voltverse get pods -o wide; exit 1; }

kubectl -n voltverse get pods -o wide
log "Deployment complete"
